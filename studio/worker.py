"""Background worker: runs queued jobs one at a time through the existing pipeline, then each final's
review chain (check, then the fixes the limits allow).

Each job and each paid step is routed again when it runs, so every budget rule applies at spend time.
No retries: a failed job stays failed, a failed step sends its image to a person, and anything cut off
by a restart is marked by db.mark_interrupted, never re-run.

The review chain (one final job):

    final done                -> check                                   review: checking
    check: approve            -> (nothing)                               ready
    check: finish             -> finish (free colour correction)         fixing
    check: repair <code>      -> repair (paid, routed first)             fixing
    check: regenerate         -> regenerate (a new final, routed)        fixing
    check: composite          -> composite onto the approved anchor's
                                 final (free; logged under the shot)     fixing, or needs_person with no source
    check: flag or incomplete -> (nothing)                               needs_person
    finish/repair/regenerate/
    composite done            -> check on its output                     fixing
    any step fails            -> (nothing; never re-queued)              needs_person

A complete check whose next is repair, regenerate, composite or flag moves the render to out/reject, as
`python -m pipeline.qa` does, so the limits in brand/repairs.json count the same. Guards against free loops:
two colour corrections per render, one composite per job, and a logo fault after a composite needs a person.
A face fault never pays for a new image while the cast member has no approved reference sheet.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pipeline import finish, generate, ledger, logo, qa, repair, router
from pipeline.compile import ROOT, Compiled, compile_spec, is_missing, load_json
from pipeline.specs import colour_id

from . import db, review
from .brain.base import scrub

DRAFTS = 3  # Cloudflare drafts per drafts job
POLL_S = 1.0
SPEC_DIR = ROOT / "out" / "specs"  # git-ignored; specs/ is tracked and holds hand-written specs
COLOUR_CORRECTIONS_PER_RENDER = 2  # free, but a drift that survives two corrections is not converging
CHAIN = (None, "checking", "fixing", "ready", "needs_person")  # review states the chain may move on from
STEP_WORDS = {"check": "check", "finish": "colour correction", "repair": "repair", "regenerate": "new image",
              "composite": "logo placement"}
FACE_UNCHECKED = "The face could not be checked against a reference photo; please look."
NO_SOURCE = "No approved reference packshot to place the real logo on."
LIMITS_USED = "The automatic repairs and new images for this shot are used up; please look."
COLOUR_STUCK = "The colour was corrected twice and still does not match; please look."
LOGO_AFTER_COMPOSITE = "The real logo was placed on the reference packshot but still fails the logo check; please look."
DECIDED = "already decided by a person; not run"
log = logging.getLogger("studio.worker")


def routes_for(c: Compiled, kind: str) -> list[router.Route]:
    """final: one route. drafts: DRAFTS routes, each counting the ones before it."""
    if kind == "final":
        return [router.route(c)]
    if kind == "drafts":
        pending, routes = router.Pending(), []
        for _ in range(DRAFTS):
            r = router.route(c, "draft", pending)
            pending.add(r)
            routes.append(r)
        return routes
    raise ValueError(f"unknown job kind {kind!r}")


def _rel(path: Path) -> str:
    path = Path(path)
    return (path.relative_to(ROOT) if path.is_absolute() else path).as_posix()


def _write_spec(spec: dict) -> None:
    """out/specs/<shot_id>.json, so `python -m pipeline.qa|logo|finish` can find a dashboard final's spec."""
    path = SPEC_DIR / f"{spec['shot']['shot_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _layout(job: dict) -> list[str]:
    return [job["layout"]] if job["kind"] == "final" and job["layout"] else []


def has_face_reference(spec: dict) -> bool:
    """Whether the shot's cast member has an approved reference sheet (brand/cast.json) on disk."""
    cast = (spec["shot"].get("model") or {}).get("cast")
    sheet = load_json("brand/cast.json")["cast"].get(cast, {}).get("reference_sheet")
    return not is_missing(sheet) and (ROOT / sheet).is_file()


def anchor_source(spec: dict) -> Path | None:
    """The render recorded by the latest `anchor_approved` event for this shot's garment (catalogue, colour,
    GSM): the real logo is composited onto it. None when there is none, or that render is gone."""
    g = spec["shot"]["garment"]
    want = (g["catalogue"], colour_id(g["colour"]), int(g["gsm"]))
    for event in reversed([e for e in ledger.entries() if e.get("event") == "anchor_approved"]):
        src = ledger.find(event.get("file", ""))
        try:
            sg = json.loads(qa.spec_path(src["shot_id"]).read_text(encoding="utf-8"))["shot"]["garment"]
        except (TypeError, KeyError, FileNotFoundError, ValueError):
            continue  # not a logged render with a spec: not a source
        if (sg["catalogue"], colour_id(sg["colour"]), int(sg["gsm"])) == want:
            path = ROOT / src["file"]
            return path if path.is_file() else None
    return None


def _colour_corrections(entry: dict) -> int:
    root = entry.get("root") or entry.get("file")
    return sum(1 for e in ledger.entries() if e.get("status") == "ok" and e.get("op") == "colour_correct"
               and e.get("root") == root)


def _public(text: str) -> str:
    from .app import public_text  # lazy: app imports this module
    return public_text(scrub(text, limit=1000))


@dataclass
class Outcome:
    """What a finished step leaves: its output image, result, the review state and the step to queue next."""
    output: str | None
    result: dict | None
    review: str
    reason: str | None = None
    then: tuple[str, str, str | None] | None = None  # (kind, input, code)


class Worker:
    """runner(c, route) -> (path, result with cost_usd); None means generate.run_route, looked up per call."""

    def __init__(self, connect_fn: Callable[[], sqlite3.Connection] = db.connect,
                 runner: Callable | None = None, poll_s: float = POLL_S):
        self.connect_fn, self.runner, self.poll_s = connect_fn, runner, poll_s
        self._stop = threading.Event()

    # --- jobs ------------------------------------------------------------------------------------
    def run_once(self) -> bool:
        """Run the oldest queued job, if any. True when a job was taken. A done final queues its check."""
        conn = self.connect_fn()
        try:
            job = db.next_queued(conn)
            if job is None or not db.claim(conn, job["id"]):
                return False
            result = {"files": [], "cost_usd": 0.0}  # filled as each call returns
            try:
                self._run(job, result)
            except Exception as e:  # the job ends here; nothing re-queues it
                # keep what was already made and paid for (e.g. 2 of 3 drafts); None if nothing was
                db.set_status(conn, job["id"], "failed", error=scrub(str(e) or type(e).__name__, limit=1000),
                              result=result if result["files"] else None)
            else:
                db.set_status(conn, job["id"], "done", result=result)
                if job["kind"] == "final" and result["files"]:
                    db.add_step(conn, job["id"], "check", result["files"][0])
                    db.set_review(conn, job["id"], "checking", only_from=CHAIN)
            return True
        finally:
            conn.close()

    def _routed(self, c: Compiled, kind: str, confirmed_by: str | None) -> list[router.Route]:
        """The job's routes, re-checked now: refused (BudgetError) unless every one runs within the rules."""
        routes = routes_for(c, kind)
        blocked = next((r for r in routes if r.action != "run"), None)
        if blocked:
            raise ledger.BudgetError(blocked.reason or f"route is {blocked.action!r}")
        total = sum(r.estimate.get("cost_usd", 0.0) for r in routes)
        if total > ledger.RUN_APPROVAL_USD and not confirmed_by:
            raise ledger.BudgetError(f"run estimated at ${total:.2f} needs confirmation")
        return routes

    def _run(self, job: dict, result: dict) -> dict:
        """Fill `result` ({"files", "cost_usd"}) call by call, so a failure part-way keeps what was made.
        A final's layout was checked against the user's sketches for this shot when the job was created."""
        c = compile_spec(job["spec"], _layout(job))
        if job["kind"] == "final":
            _write_spec(job["spec"])
        routes = self._routed(c, job["kind"], job["confirmed_by"])
        runner = self.runner or generate.run_route
        for r in routes:
            path, res = runner(c, r)
            result["files"].append(_rel(path))
            result["cost_usd"] = round(result["cost_usd"] + res.cost_usd, 4)
        return result

    # --- review steps ----------------------------------------------------------------------------
    def run_step(self) -> bool:
        """Run the oldest queued review step, once no job is queued. True when a step was taken.

        Any exception (SystemExit from a CLI helper included) fails the step: the review needs a person with
        the error in plain words, and nothing is re-queued. Cost is what the ledger logged during the step."""
        conn = self.connect_fn()
        try:
            step = db.next_step(conn)
            if step is None or not db.claim_step(conn, step["id"]):
                return False
            job = db.get_job(conn, step["job_id"])
            before = len(ledger.entries())
            try:
                if job["review"] in db.VERDICTS:  # a person decided while this step waited: spend nothing
                    db.set_step(conn, step["id"], "failed", error=DECIDED)
                    return True
                out = self._step(conn, step, job)
            except (Exception, SystemExit) as e:
                error = scrub(str(e) or type(e).__name__, limit=1000)
                db.set_step(conn, step["id"], "failed", error=error, cost_usd=self._spent(before))
                db.set_review(conn, job["id"], "needs_person", only_from=CHAIN, reason=_public(
                    f"The automatic {STEP_WORDS[step['kind']]} stopped and was not retried: {error}"))
            else:
                db.set_step(conn, step["id"], "done", output=out.output, result=out.result,
                            cost_usd=self._spent(before))
                if out.then:
                    db.add_step(conn, job["id"], *out.then)
                db.set_review(conn, job["id"], out.review, only_from=CHAIN,
                              reason=_public(out.reason) if out.reason else None)
            return True
        finally:
            conn.close()

    @staticmethod
    def _spent(before: int) -> float:
        """Cost logged since entry `before`: the step's own calls (the worker runs one step at a time)."""
        return round(sum(e.get("cost_usd") or 0.0 for e in ledger.entries()[before:]), 6)

    def _step(self, conn, step: dict, job: dict) -> Outcome:
        if step["kind"] == "check":
            return self._check(conn, step, job)
        if step["kind"] == "finish":
            out = qa.rel(finish.colour_correct(ROOT / step["input"]))
            return Outcome(out, {"op": "colour_correct"}, "fixing", then=("check", out, None))
        if step["kind"] == "repair":
            return self._repair(step, job)
        if step["kind"] == "regenerate":
            return self._regenerate(job)
        if step["kind"] == "composite":
            return self._composite(step, job)
        raise ValueError(f"unknown review step kind {step['kind']!r}")

    def _check(self, conn, step: dict, job: dict) -> Outcome:
        """Run the checks, then decide the next step under the limits (qa.next_action, inside run_checks)."""
        image, spec = ROOT / step["input"], job["spec"]
        r = review.run_checks(image, spec["shot"]["shot_id"], job["user"])
        result = dict(r)
        if not r["complete"]:  # nothing was judged bad: the render stays where it is
            return Outcome(step["input"], result, "needs_person", r["reason"])
        action, code = r["next"], r["next_code"]
        if action == "approve":
            return Outcome(step["input"], result, "ready")
        if action == "finish":
            if _colour_corrections(ledger.find(image) or {}) >= COLOUR_CORRECTIONS_PER_RENDER:
                return Outcome(step["input"], result, "needs_person", COLOUR_STUCK)
            return Outcome(step["input"], result, "fixing", then=("finish", step["input"], code))

        moved = qa.rel(qa.move_to_reject(image))  # judged bad, as the CLI does: the limits count it the same
        result["moved_to"] = moved
        composited = any(s["kind"] == "composite" for s in db.steps_for(conn, job["id"]))
        if composited and any(c.startswith("logo_") for c in r["codes"]):
            return Outcome(moved, result, "needs_person", LOGO_AFTER_COMPOSITE)
        if action == "repair":
            return Outcome(moved, result, "fixing", then=("repair", moved, code))
        if action == "regenerate":
            if "face" in r["codes"] and not has_face_reference(spec):
                return Outcome(moved, result, "needs_person", FACE_UNCHECKED)
            return Outcome(moved, result, "fixing", then=("regenerate", moved, None))
        if action == "composite":
            source = anchor_source(spec)
            if source is None:
                return Outcome(moved, result, "needs_person", NO_SOURCE)
            return Outcome(moved, result, "fixing", then=("composite", qa.rel(source), code))
        return Outcome(moved, result, "needs_person", LIMITS_USED)  # flag

    def _repair(self, step: dict, job: dict) -> Outcome:
        """A paid one-change edit: routed first on the tier it will run on; a route other than "run" stops here."""
        image = ROOT / step["input"]
        entry = ledger.find(image)
        if entry is None:
            raise ValueError(f"{image.name} is not in out/log.jsonl")
        c = compile_spec(job["spec"], _layout(job))
        tier = entry.get("tier") if entry.get("tier") in ("standard", "premium") else c.tier
        r = router.route(dataclasses.replace(c, tier=tier, size=entry.get("size") or c.size))
        if r.action != "run":
            raise ledger.BudgetError(r.reason or f"route is {r.action!r}")
        path, res = repair.repair_image(image, step["code"], tier=r.tier)
        out = qa.rel(path)
        return Outcome(out, {"model": getattr(res, "model", None)}, "fixing", then=("check", out, None))

    def _regenerate(self, job: dict) -> Outcome:
        """A new final through the final's own path: compiled with the job's layout, routed, logged as a final."""
        c = compile_spec(job["spec"], _layout(job))
        (r,) = self._routed(c, "final", job["confirmed_by"])
        path, res = (self.runner or generate.run_route)(c, r)
        out = qa.rel(path)
        return Outcome(out, {"model": getattr(res, "model", None)}, "fixing", then=("check", out, None))

    def _composite(self, step: dict, job: dict) -> Outcome:
        """The real artwork onto the approved anchor's render (free), logged under this shot like `--as-shot`."""
        source, shot_id = ROOT / step["input"], job["spec"]["shot"]["shot_id"]
        spec = logo.as_shot_spec(shot_id)
        deco = next(d for d in spec["shot"]["decoration"] if d.get("render_mode") == "generate_and_verify")
        entry, _ = finish._spec_for(source)
        entry = {**entry, "shot_id": shot_id, "root": entry.get("root") or entry.get("file")}
        preset = logo.TECHNIQUE_PRESET.get(deco["type"], "print")
        rgb, info = logo.composite(source, deco["artwork"], deco["placement"], deco["size_cm"], preset)
        # one file per step: two shots composited onto one source never overwrite each other
        path = finish._derived(source, f"logo-{Path(deco['artwork']).stem}-s{step['id']}", rgb, entry,
                               "composite", {"composite": info})
        out = qa.rel(path)
        return Outcome(out, {"composite": info, "source": step["input"]}, "fixing", then=("check", out, None))

    # --- loop ------------------------------------------------------------------------------------
    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="studio-worker", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                took = self.run_once() or self.run_step()  # queued jobs first, then review steps
            except Exception:  # e.g. database locked: wait and look again; the job itself is not retried
                log.exception("worker pass failed")
                took = False
            if not took:
                self._stop.wait(self.poll_s)
