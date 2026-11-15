"""Background worker: runs queued jobs one at a time through the existing pipeline, then each final's
review chain (check, then the fixes the limits allow).

Each job and each paid step is routed again when it runs, so every budget rule applies at spend time.
No retries: a failed job stays failed, a failed step sends its image to a person, and anything cut off
by a restart is marked by db.mark_interrupted, never re-run.

The review chain (one final job):

    final done                -> check                                   review: checking
    check: approve            -> (nothing)                               ready, or needs_person when
                                                                         the face could not be checked
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
`python -m pipeline.qa` does, so the limits in brand/repairs.json count the same. The next step is decided
first and the render moved last, so a failure while deciding moves nothing.

Spend stays within the worst case shown before "Approve & create" (1 final + 2 new images, each with up to
2 repairs): repairs and new images are re-checked against the ledger when they run, and a composite is its
own render that is never repaired or replaced automatically (only approved, or colour-corrected, or sent to
a person). Free loops are capped: two colour corrections per render, one composite per job. A composite goes
only onto a front packshot shot with a chest logo. A face fault never pays for a new image while the cast
member has no approved reference sheet: the checks leave that face to a person (review.run_checks'
face_unchecked), after the other faults have had their fixes.

Every ledger entry logged inside a step carries {"review_step", "job_id"} (ledger.tagged), so a step's cost
is exactly its own calls.
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
from pipeline.compile import ROOT, Compiled, compile_spec, load_json
from pipeline.specs import colour_id, load_recipes

from . import db, review
from .brain.base import scrub
from .review import has_face_reference

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
COMPOSITE_FAULT = "The real logo was placed on the reference packshot, but the result still fails a check; please look."
DECIDED = "already decided by a person; not run"
FINALS_DIR = ROOT / "out" / "finals"
FRONT_PLACEMENTS = ("left chest", "centre chest")  # what the front reference packshot can carry
log = logging.getLogger("studio.worker")


class NeedsPerson(Exception):
    """A step that must not run: it fails, nothing is spent, and its message is the reason a person sees."""


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


def _in_finals(path: Path) -> bool:
    return path.is_file() and path.resolve().is_relative_to(FINALS_DIR.resolve())


def anchor_source(spec: dict) -> Path | None:
    """The render recorded by the latest `anchor_approved` event for this shot's garment (catalogue, colour,
    GSM): the real logo is composited onto it. None when there is none, or that render is gone or no longer
    in out/finals (e.g. moved to out/reject since)."""
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
            return path if _in_finals(path) else None
    return None


def front_packshot(spec: dict) -> bool:
    """Whether the real logo may go onto the front reference packshot for this shot: a packshot (no model) seen
    from the front packshot's camera angle, every rendered design on the chest."""
    shot = spec["shot"]
    front = load_recipes()["recipes"]["packshot-front"]["camera"]["angle"]
    decos = [d for d in shot.get("decoration", []) if d.get("render_mode") == "generate_and_verify"]
    return (not (shot.get("model") or {}).get("present") and (shot.get("camera") or {}).get("angle") == front
            and bool(decos) and all(d["placement"] in FRONT_PLACEMENTS for d in decos))


def _composite_made(entry: dict) -> bool:
    """Whether a render is a studio logo composite, or made from one (its colour correction, a repair): a
    composite is its own root (finish._derived own_root)."""
    root = entry.get("root")
    return entry.get("op") == "composite" or bool(root) and (ledger.find(root) or {}).get("op") == "composite"


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
                if job["kind"] == "final" and result["files"]:  # done, check queued, review set: one transaction
                    db.complete_final(conn, job["id"], result, result["files"][0])
                else:
                    db.set_status(conn, job["id"], "done", result=result)
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
        the error in plain words, and nothing is re-queued. NeedsPerson gives that reason word for word.
        Every ledger entry logged inside the step carries its tag, so its cost is exactly its own calls."""
        conn = self.connect_fn()
        try:
            step = db.next_step(conn)
            if step is None or not db.claim_step(conn, step["id"]):
                return False
            job = db.get_job(conn, step["job_id"])
            try:
                if job["review"] in db.VERDICTS:  # a person decided while this step waited: spend nothing
                    db.set_step(conn, step["id"], "failed", error=DECIDED)
                    return True
                with ledger.tagged(review_step=step["id"], job_id=job["id"]):
                    out = self._step(conn, step, job)
            except (Exception, SystemExit) as e:
                error = scrub(str(e) or type(e).__name__, limit=1000)
                db.set_step(conn, step["id"], "failed", error=error, cost_usd=self._spent(step["id"]))
                reason = error if isinstance(e, NeedsPerson) else \
                    f"The automatic {STEP_WORDS[step['kind']]} stopped and was not retried: {error}"
                db.set_review(conn, job["id"], "needs_person", only_from=CHAIN, reason=_public(reason))
            else:
                db.set_step(conn, step["id"], "done", output=out.output, result=out.result,
                            cost_usd=self._spent(step["id"]))
                if out.then:
                    db.add_step(conn, job["id"], *out.then)
                db.set_review(conn, job["id"], out.review, only_from=CHAIN,
                              reason=_public(out.reason) if out.reason else None)
            return True
        finally:
            conn.close()

    @staticmethod
    def _spent(step_id: int) -> float:
        """What the step's own calls cost: the ledger entries tagged with it."""
        return round(sum(e.get("cost_usd") or 0.0 for e in ledger.entries() if e.get("review_step") == step_id), 6)

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
        """Run the checks and decide the next step; then, last, move a render judged bad to out/reject as the
        CLI does (so the limits count it the same). A failure while deciding moves nothing."""
        image = ROOT / step["input"]
        r = review.run_checks(image, job["spec"]["shot"]["shot_id"], job["user"])
        state, reason, then = self._decide(conn, step, job, r)
        result = dict(r)
        if not (r["complete"] and r["next"] in qa.REJECT_ACTIONS):  # incomplete, approve or finish: stays put
            return Outcome(step["input"], result, state, reason, then)
        moved = qa.rel(qa.move_to_reject(image))
        result["moved_to"] = moved
        if then and then[1] is None:  # the step works on the moved render
            then = (then[0], moved, then[2])
        return Outcome(moved, result, state, reason, then)

    def _decide(self, conn, step: dict, job: dict, r: dict) -> tuple[str, str | None, tuple | None]:
        """(review state, reason, next step) for a check result; the next step's input None means "the render,
        once moved". Nothing here changes anything."""
        spec = job["spec"]
        if not r["complete"]:  # nothing was judged bad
            return "needs_person", r["reason"], None
        action, code = r["next"], r["next_code"]
        entry = ledger.find(ROOT / step["input"]) or {}
        if action == "approve":  # every other fault was fixed first; a face nobody could compare goes to a person
            if r.get("face_unchecked"):
                return "needs_person", FACE_UNCHECKED, None
            return "ready", None, None
        if action == "finish":
            if _colour_corrections(entry) >= COLOUR_CORRECTIONS_PER_RENDER:
                return "needs_person", COLOUR_STUCK, None
            return "fixing", None, ("finish", step["input"], code)
        if _composite_made(entry):  # the real artwork is never repaired or replaced automatically
            return "needs_person", COMPOSITE_FAULT, None
        if action == "repair":
            return "fixing", None, ("repair", None, code)
        if action == "regenerate":
            if "face" in r["codes"] and not has_face_reference(spec):
                return "needs_person", FACE_UNCHECKED, None
            return "fixing", None, ("regenerate", None, None)
        if action == "composite":
            if any(s["kind"] == "composite" for s in db.steps_for(conn, job["id"])):  # one per job
                return "needs_person", COMPOSITE_FAULT, None
            source = anchor_source(spec) if front_packshot(spec) else None
            if source is None:
                return "needs_person", NO_SOURCE, None
            return "fixing", None, ("composite", qa.rel(source), code)
        return "needs_person", LIMITS_USED, None  # flag

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
        """A new final through the final's own path: compiled with the job's layout, routed, logged as a final.
        The shot's regeneration limit is counted again now, as qa.next_action counts it: another final of the
        same shot (a second job, the CLI) may have landed since the check."""
        limit = load_json("brand/repairs.json")["limits"]["regenerations_per_shot"]
        if qa.regenerations_used(job["spec"]["shot"]["shot_id"]) >= limit:
            raise NeedsPerson(LIMITS_USED)
        c = compile_spec(job["spec"], _layout(job))
        (r,) = self._routed(c, "final", job["confirmed_by"])
        path, res = (self.runner or generate.run_route)(c, r)
        out = qa.rel(path)
        return Outcome(out, {"model": getattr(res, "model", None)}, "fixing", then=("check", out, None))

    def _composite(self, step: dict, job: dict) -> Outcome:
        """The real artwork onto the approved anchor's render (free), logged under this shot like `--as-shot`, but
        as a render of its own (its own root), so it never uses the anchor's repair or correction allowance.
        Written next to its source, which must be in out/finals."""
        source, shot_id = ROOT / step["input"], job["spec"]["shot"]["shot_id"]
        if not _in_finals(source):
            raise NeedsPerson(NO_SOURCE)
        spec = logo.as_shot_spec(shot_id)
        deco = next(d for d in spec["shot"]["decoration"] if d.get("render_mode") == "generate_and_verify")
        entry, _ = finish._spec_for(source)
        entry = {**entry, "shot_id": shot_id}
        preset = logo.TECHNIQUE_PRESET.get(deco["type"], "print")
        rgb, info = logo.composite(source, deco["artwork"], deco["placement"], deco["size_cm"], preset)
        # one file per step: two shots composited onto one source never overwrite each other
        path = finish._derived(source, f"logo-{Path(deco['artwork']).stem}-s{step['id']}", rgb, entry,
                               "composite", {"composite": info}, own_root=True)
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
