"""The automatic check-and-fix chain (studio.worker review steps) and the repair_image refactor.

Fake runner, fake checks and an in-memory ledger: no paid calls. run_checks and qa.next_action are the real ones,
so the limits are counted from the ledger exactly as the CLI counts them. Specs are the tracked ones in specs/.
"""
import json
import sqlite3
import sys
import threading
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline import finish, ledger, logo, qa, repair, router
from pipeline.providers import ProviderError
from studio import db, review
from studio import worker as worker_mod
from studio.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
SID = "ovs-wblack-450-anchor-front"            # plain packshot, standard
MAYA_SID = "ovs-wblack-450-maya-front"         # on-model, Maya (no approved reference sheet)
LOGO_SID = "ovs-wblack-450-oakmere-puff-front"  # premium, generate_and_verify Oakmere centre chest

COLOUR_PASS = {"result": "pass", "code": None, "de2000": 1.2, "measured_hex": "#303032", "target_hex": "#2F2F31",
               "dL": 0.1, "da": 0.2, "db": -0.3, "mask_source": "vision (cached)", "mask_fraction": 0.4}
COLOUR_DRIFT = {**COLOUR_PASS, "result": "fail", "code": "colour_drift", "de2000": 7.5}
STRAY_PASS = {"result": "pass", "code": None, "found": [], "stray": []}
LOGO_PASS = {"result": "pass", "decorations": [{"placement": "centre chest", "found": True, "codes": []}]}
LOGO_TEXT = {"result": "fail", "decorations": [{"placement": "centre chest", "found": True, "codes": ["logo_text"]}]}
GEMINI_COST = 0.134


def stamp() -> str:
    return ledger.now().isoformat(timespec="seconds")


class FakeBrain:
    """Judges every checklist line; env.current["visual"] names a code for the first line to fail with."""

    def __init__(self, env):
        self.env, self.calls = env, []

    def ask_json(self, task, prompt, schema, images=(), user="owner"):
        self.calls.append([Path(i).name for i in images])
        texts = schema["properties"]["lines"]["items"]["properties"]["text"]["enum"]
        code = self.env.current.get("visual")
        return SimpleNamespace(data={"lines": [
            {"text": t, "ok": not (code and i == 0), "code": code if code and i == 0 else None, "note": "seen"}
            for i, t in enumerate(texts)]}, provider="fake", fallback=False)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(k, "test")
    rows: list[dict] = []

    def record(e):  # as ledger.record: the step's tags (this thread's context) go into every entry
        rows.append({"ts": stamp(), **ledger.tags(), **e})
        return rows[-1]
    monkeypatch.setattr(ledger, "entries", lambda: rows)
    monkeypatch.setattr(ledger, "record", record)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "studio.db")
    monkeypatch.setattr(worker_mod, "SPEC_DIR", tmp_path / "specs")

    s = SimpleNamespace(rows=rows, script=[], current={}, checked=[], moves=[], runs=[], repairs=[], corrected=[],
                        composites=[], made=[])
    s.brain = FakeBrain(s)

    # --- checks: one script item per check, in order; anything not scripted passes ---
    def colour_check(image, target, on_model):
        s.current = s.script.pop(0) if s.script else {}
        s.checked.append(Path(image).name)
        value = s.current.get("colour", COLOUR_PASS)
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr(qa, "colour_check", colour_check)
    monkeypatch.setattr(qa, "stray_text_check", lambda image, allowed: s.current.get("stray", STRAY_PASS))
    monkeypatch.setattr(logo, "verify", lambda image: s.current.get("logo", LOGO_PASS))
    monkeypatch.setattr(review, "get_brain", lambda user, fast=False: s.brain)
    monkeypatch.setattr(qa.shutil, "move", lambda src, dest: s.moves.append(Path(src).name))

    # --- paid calls: logged like generate.run / run_route, nothing leaves the PC ---
    def runner(c, r):  # a final or a regenerate
        s.runs.append(r.phase)
        file = f"out/finals/zz-chain-{uuid.uuid4().hex[:6]}_{c.shot_id}_{r.phase}.jpg"
        record({"tag": f"{c.shot_id}_{r.phase}", "tier": r.tier, "provider": "gemini", "model": r.model,
                "size": r.size, "aspect": r.aspect, "shot_id": c.shot_id, "phase": r.phase,
                "requested_tier": r.requested_tier, "status": "ok", "file": file, "cost_usd": GEMINI_COST})
        return ROOT / file, SimpleNamespace(cost_usd=GEMINI_COST, model=r.model)
    s.runner = runner

    def repair_run(tier, prompt, refs, size, aspect, tag="image", out_dir=None, extra=None):
        s.repairs.append({"tier": tier, "prompt": prompt, "refs": [Path(r) for r in refs], "size": size,
                          "aspect": aspect, "tag": tag, "extra": extra})
        file = f"out/finals/zz-chain-{uuid.uuid4().hex[:6]}_{tag}.jpg"
        record({**(extra or {}), "tag": tag, "tier": tier, "provider": "gemini", "model": "gemini-3-pro-image",
                "size": size, "aspect": aspect, "status": "ok", "file": file, "cost_usd": GEMINI_COST})
        return ROOT / file, SimpleNamespace(cost_usd=GEMINI_COST, model="gemini-3-pro-image")
    monkeypatch.setattr(repair, "run", repair_run)

    def colour_correct(image):  # free: logged like finish._derived
        image = Path(image)
        s.corrected.append(image.name)
        entry = ledger.find(image)
        out = image.with_name(f"{image.stem}_cc.jpg")
        record({"shot_id": entry["shot_id"], "tier": entry.get("tier"), "provider": "local", "phase": "finish",
                "op": "colour_correct", "status": "ok", "file": qa.rel(out), "source": qa.rel(image),
                "root": entry.get("root") or entry.get("file"), "cost_usd": 0.0})
        return out
    monkeypatch.setattr(finish, "colour_correct", colour_correct)

    def composite(image, artwork, placement, size_cm, preset, geometry=None):
        s.composites.append({"image": Path(image).name, "artwork": artwork, "placement": placement,
                             "size_cm": size_cm, "preset": preset})
        return np.zeros((8, 8, 3), np.float32), {"artwork": artwork, "placement": placement, "size_cm": size_cm,
                                                 "preset": preset, "chest_cm_assumed": True}
    monkeypatch.setattr(logo, "composite", composite)

    yield s
    for p in s.made:  # real files this test wrote under out/finals
        for f in p.parent.glob(f"{p.stem}*"):
            f.unlink(missing_ok=True)


def make_final(spec_id=SID, layout=None, user="owner") -> int:
    spec = json.loads((ROOT / "specs" / f"{spec_id}.json").read_text(encoding="utf-8"))
    with closing(db.connect()) as conn:
        return db.create_job(conn, user, "final", {}, spec, {"base_usd": 0.1}, layout=layout)


def drain(w: Worker, limit: int = 40) -> int:
    n = 0
    while w.run_once() or w.run_step():
        n += 1
        assert n < limit, "the chain did not stop"
    return n


def job(jid: int) -> dict:
    with closing(db.connect()) as conn:
        return db.get_job(conn, jid)


def steps(jid: int) -> list[dict]:
    with closing(db.connect()) as conn:
        return db.steps_for(conn, jid)


def kinds(jid: int) -> list[tuple[str, str]]:
    return [(s["kind"], s["status"]) for s in steps(jid)]


def gemini_rows(env) -> list[dict]:
    return [r for r in env.rows if r.get("provider") == "gemini"]


# --- the brief's tests ------------------------------------------------------------------------

def test_pass_goes_ready(env):
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True
    assert job(jid)["review"] == "checking" and kinds(jid) == [("check", "queued")]
    final = job(jid)["result"]["files"][0]
    assert steps(jid)[0]["input"] == final

    drain(w)
    (check,) = steps(jid)
    assert (check["kind"], check["status"], check["output"]) == ("check", "done", final)
    assert check["result"]["next"] == "approve" and check["result"]["complete"] is True
    assert job(jid)["review"] == "ready" and job(jid)["review_reason"] is None
    assert env.moves == [] and env.runs == ["final"]
    (qa_event,) = [r for r in env.rows if r.get("event") == "qa"]
    assert qa_event["by"] == "studio" and qa_event["next"] == "approve"


def test_drift_runs_free_finish_then_rechecks(env):
    env.script = [{"colour": COLOUR_DRIFT}]
    jid = make_final()
    drain(Worker(db.connect, runner=env.runner))
    assert kinds(jid) == [("check", "done"), ("finish", "done"), ("check", "done")]
    check1, fix, check2 = steps(jid)
    assert check1["result"]["next"] == "finish" and fix["code"] == "colour_drift"
    assert fix["input"] == check1["input"] and fix["output"].endswith("_cc.jpg")
    assert check2["input"] == fix["output"] and fix["cost_usd"] == 0.0
    assert env.checked == [Path(check1["input"]).name, Path(fix["output"]).name]
    assert env.moves == []  # a finish is not a rejection: the CLI leaves the render in place
    assert len(gemini_rows(env)) == 1  # only the final was paid for
    assert job(jid)["review"] == "ready"


def test_colour_corrections_stop_after_two(env):
    """No free loop: a colour that still drifts after two corrections of one render needs a person."""
    env.script = [{"colour": COLOUR_DRIFT}] * 3
    jid = make_final()
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "finish", "check", "finish", "check"]
    assert len(env.corrected) == 2
    j = job(jid)
    assert j["review"] == "needs_person" and "colour" in j["review_reason"].lower()


def test_repair_then_pass(env, monkeypatch):
    routed = []
    real_route = router.route
    monkeypatch.setattr(router, "route", lambda c, *a, **k: routed.append(c.tier) or real_route(c, *a, **k))
    env.script = [{"visual": "drawcord_shape"}]
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True and w.run_step() is True  # final, then its check
    final = job(jid)["result"]["files"][0]
    check1 = steps(jid)[0]
    assert check1["result"]["next"] == "repair" and check1["result"]["next_code"] == "drawcord_shape"
    moved = f"out/reject/{Path(final).name}"
    assert check1["output"] == moved and env.moves == [Path(final).name]
    assert env.rows[-1] == {"ts": env.rows[-1]["ts"], "event": "moved", "file": final, "moved_to": moved,
                            "review_step": check1["id"], "job_id": jid}  # the CLI's event, tagged with its step
    assert job(jid)["review"] == "fixing" and kinds(jid)[1] == ("repair", "queued")
    assert steps(jid)[1]["input"] == moved and steps(jid)[1]["code"] == "drawcord_shape"

    routed.clear()
    drain(w)
    assert kinds(jid) == [("check", "done"), ("repair", "done"), ("check", "done")]
    assert routed == ["standard"]  # the repair was routed before it was run
    (call,) = env.repairs
    assert "drawcords" in call["prompt"] and call["refs"] == [ROOT / moved]
    assert call["extra"] == {"shot_id": SID, "phase": "repair", "root": final, "repair_of": moved,
                             "reason_code": "drawcord_shape", "requested_tier": "standard"}
    fix = steps(jid)[1]
    assert fix["cost_usd"] == GEMINI_COST and steps(jid)[2]["input"] == fix["output"]
    assert job(jid)["review"] == "ready"


def test_two_repairs_then_regenerate_then_flag(env):
    """Review Focus 2: the limits come from the ledger, counting a final made with the CLI too."""
    env.rows.append({"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "tier": "standard",
                     "provider": "gemini", "file": "out/finals/zz-chain-cli_final.jpg", "cost_usd": 0.101})
    env.script = [{"visual": "drawcord_shape"}, {"visual": "drawcord_shape"}, {"visual": "drawcord_shape"},
                  {"visual": "drape_too_thin"}]
    jid = make_final()
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check", "repair", "check", "regenerate", "check"]
    assert all(st == "done" for _, st in kinds(jid))
    nexts = [s["result"]["next"] for s in steps(jid) if s["kind"] == "check"]
    assert nexts == ["repair", "repair", "regenerate", "flag"]

    final = job(jid)["result"]["files"][0]
    assert [r["extra"]["root"] for r in env.repairs] == [final, final]  # two repairs on one render
    regen = steps(jid)[5]
    new = next(r for r in env.rows if r.get("file") == regen["output"])
    assert new["phase"] == "final" and new["shot_id"] == SID  # counted by qa.next_action as a final
    finals = [r for r in env.rows if r.get("phase") == "final" and r.get("shot_id") == SID]
    assert len(finals) == 3 and env.runs == ["final", "final"]
    assert len(env.moves) == 4  # every rejected render moved, the flagged one too
    j = job(jid)
    assert j["review"] == "needs_person" and j["review_reason"]
    assert sum(1 for r in gemini_rows(env) if r.get("status") == "ok") == 1 + 4  # CLI final, final, 2 repairs, regen


def test_cap_mid_chain_needs_person_no_spend(env):
    """Review Focus 1: a cap reached before a paid step stops the chain there; nothing is spent or retried."""
    env.script = [{"visual": "drawcord_shape"}]
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True and w.run_step() is True
    assert kinds(jid)[-1] == ("repair", "queued")
    env.rows.append({"ts": stamp(), "provider": "gemini", "tier": "premium", "cost_usd": 8.95})  # spent elsewhere
    spent = ledger.gemini_month_spend()

    assert w.run_step() is True
    assert env.repairs == [] and ledger.gemini_month_spend() == spent
    fix = steps(jid)[-1]
    assert fix["status"] == "failed" and "monthly cap" in fix["error"]
    j = job(jid)
    assert j["review"] == "needs_person" and "monthly cap" in j["review_reason"]
    assert w.run_step() is False and w.run_once() is False  # nothing re-queued


def test_cap_before_regenerate_needs_person_no_spend(env):
    env.script = [{"visual": "framing"}]
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True and w.run_step() is True
    assert kinds(jid)[-1] == ("regenerate", "queued")
    env.rows.append({"ts": stamp(), "provider": "gemini", "tier": "premium", "cost_usd": 8.95})
    assert w.run_step() is True
    assert env.runs == ["final"]
    assert job(jid)["review"] == "needs_person" and "monthly cap" in job(jid)["review_reason"]
    assert w.run_step() is False


def test_failed_step_never_retried(env, monkeypatch):
    env.script = [{"visual": "drawcord_shape"}]
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True and w.run_step() is True

    def broken(*a, **k):
        raise ProviderError("gemini: 503 from https://x/v1 GEMINI_API_KEY rejected for d:/Workspace/refs/a.png")
    monkeypatch.setattr(repair, "run", broken)
    assert w.run_step() is True
    fix = steps(jid)[-1]
    assert fix["status"] == "failed" and "503" in fix["error"]
    j = job(jid)
    assert j["review"] == "needs_person"
    assert "GEMINI_API_KEY" not in j["review_reason"] and "a setting" in j["review_reason"]
    assert "refs/" not in j["review_reason"] and "a.png" in j["review_reason"]
    assert w.run_step() is False and w.run_once() is False
    assert len([s for s in steps(jid) if s["kind"] == "repair"]) == 1


def test_any_step_exception_stops_the_chain(env, monkeypatch):
    """A SystemExit from a CLI helper (finish._spec_for) is a failed step too, never a dead worker."""
    env.script = [{"colour": COLOUR_DRIFT}]

    def exits(image):
        raise SystemExit("error: x.jpg is not in out/log.jsonl with a shot_id")
    monkeypatch.setattr(finish, "colour_correct", exits)
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    drain(w)
    assert kinds(jid) == [("check", "done"), ("finish", "failed")]
    assert job(jid)["review"] == "needs_person" and "x.jpg" in job(jid)["review_reason"]

    other = make_final()
    monkeypatch.setattr(review, "run_checks", lambda *a, **k: (_ for _ in ()).throw(ValueError("checks broke")))
    drain(w)
    assert kinds(other) == [("check", "failed")] and job(other)["review"] == "needs_person"


def test_incomplete_check_needs_person_not_moved(env):
    """A check that could not run judged nothing: the render stays where it is (ruling 2)."""
    env.script = [{"colour": ProviderError("gemini: vision 500")}]
    jid = make_final()
    drain(Worker(db.connect, runner=env.runner))
    (check,) = steps(jid)
    assert check["status"] == "done" and check["result"]["complete"] is False
    assert check["output"] == check["input"] and env.moves == []
    j = job(jid)
    assert j["review"] == "needs_person" and "not retried" in j["review_reason"]
    assert env.brain.calls == []  # nothing more spent after the failed check


def test_logo_second_failure_composites_onto_anchor_source(env):
    anchor_final = make_file(env, "anchor")
    olive_final = make_file(env, "olive")
    env.rows += [
        {"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "tier": "standard", "provider": "gemini",
         "file": anchor_final, "cost_usd": 0.101},
        {"ts": stamp(), "event": "anchor_approved", "file": anchor_final,
         "anchor": "refs/anchors/pullover-oversized_washed-black_450_front.png", "by": "owner"},
        # a later approval for another garment is not this shot's source
        {"ts": stamp(), "status": "ok", "shot_id": "pullover-oversized-olive-380-packshot-front-e83fb1b7",
         "phase": "final", "provider": "gemini", "file": olive_final, "cost_usd": 0.101},
        {"ts": stamp(), "event": "anchor_approved", "file": olive_final,
         "anchor": "refs/anchors/pullover-oversized_olive_380_front.png", "by": "owner"},
    ]
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}]
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check", "composite", "check"]
    assert all(st == "done" for _, st in kinds(jid))
    assert [s["result"]["next"] for s in steps(jid) if s["kind"] == "check"] == ["repair", "composite", "approve"]
    assert len(env.repairs) == 1 and env.repairs[0]["extra"]["reason_code"] == "logo_text"  # one logo repair

    comp = steps(jid)[3]
    assert comp["input"] == anchor_final and comp["code"] == "logo_text"
    assert env.composites == [{"image": Path(anchor_final).name, "artwork": "brand/designs/oakmere-athletic.svg",
                               "placement": "centre chest", "size_cm": 22, "preset": "3d_puff"}]
    out = next(r for r in env.rows if r.get("file") == comp["output"])
    assert out["shot_id"] == LOGO_SID and out["op"] == "composite" and out["source"] == anchor_final
    assert out["root"] == comp["output"]  # its own render: never counted against the anchor's limits
    assert (ROOT / comp["output"]).is_file() and comp["output"].startswith("out/finals/")
    assert steps(jid)[4]["input"] == comp["output"]
    assert len(env.moves) == 2 and job(jid)["review"] == "ready"
    assert comp["cost_usd"] == 0.0 and len(env.repairs) == 1  # the composite is free


def test_logo_without_anchor_source_needs_person(env):
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}]
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check"]
    assert steps(jid)[-1]["result"]["next"] == "composite" and env.composites == []
    j = job(jid)
    assert j["review"] == "needs_person"
    assert j["review_reason"] == "No approved reference packshot to place the real logo on."


def test_logo_fault_after_composite_needs_person(env):
    """No composite loop: the real artwork that still fails the logo check goes to a person."""
    anchor_final = make_file(env, "anchor")
    env.rows += [
        {"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "provider": "gemini",
         "file": anchor_final, "cost_usd": 0.101},
        {"ts": stamp(), "event": "anchor_approved", "file": anchor_final, "anchor": "refs/anchors/x.png", "by": "owner"},
    ]
    env.script = [{"logo": LOGO_TEXT}] * 3
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check", "composite", "check"]
    assert len(env.composites) == 1 and len(env.repairs) == 1
    assert job(jid)["review"] == "needs_person" and "logo" in job(jid)["review_reason"]


def test_face_without_reference_sheet_never_regenerates(env, monkeypatch):
    """Ruling 3: a face fault with no approved cast sheet goes to a person, not to a paid new image."""
    env.script = [{"visual": "face"}]
    jid = make_final(MAYA_SID)
    drain(Worker(db.connect, runner=env.runner))
    (check,) = steps(jid)
    assert check["result"]["next"] == "regenerate" and env.runs == ["final"]
    assert len(env.moves) == 1  # the check was complete and judged the render bad
    j = job(jid)
    assert j["review"] == "needs_person"
    assert j["review_reason"] == "The face could not be checked against a reference photo; please look."

    # another regenerate reason on the same shot runs within the limits (a fresh ledger: limits start again)
    env.rows.clear()
    env.script = [{"visual": "framing"}]
    other = make_final(MAYA_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(other)] == ["check", "regenerate", "check"]

    # with an approved sheet, a face fault regenerates
    env.rows.clear()
    monkeypatch.setattr(worker_mod, "has_face_reference", lambda spec: True)
    env.script = [{"visual": "face"}]
    third = make_final(MAYA_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(third)][:2] == ["check", "regenerate"]


def test_regenerate_uses_the_jobs_layout(env, monkeypatch):
    """Ruling 7: a new image goes through the final's own path, with the job's sketch if it had one."""
    seen = []
    real = worker_mod.compile_spec
    monkeypatch.setattr(worker_mod, "compile_spec", lambda spec, layout=(): seen.append(list(layout)) or real(spec, layout))
    env.script = [{"visual": "framing"}]
    make_final(MAYA_SID, layout="out/drafts/zz-sketch.png")
    drain(Worker(db.connect, runner=env.runner))
    assert env.runs == ["final", "final"]
    assert seen and all(layout == ["out/drafts/zz-sketch.png"] for layout in seen)


def test_chain_never_overrides_a_persons_verdict(env):
    env.script = [{"visual": "drawcord_shape"}]
    jid = make_final()
    w = Worker(db.connect, runner=env.runner)
    assert w.run_once() is True and w.run_step() is True
    with closing(db.connect()) as conn:
        assert db.set_review(conn, jid, "rejected", reason="no") is True
    assert w.run_step() is True
    assert env.repairs == [] and steps(jid)[-1]["status"] == "failed"
    assert job(jid)["review"] == "rejected" and job(jid)["review_reason"] == "no"


# --- db -----------------------------------------------------------------------------------------

def test_steps_wait_for_queued_jobs(env):
    first = make_final()
    with closing(db.connect()) as conn:
        sid = db.add_step(conn, first, "check", "out/finals/a.jpg")
        assert db.next_step(conn) is None  # a queued job goes first
        db.set_status(conn, first, "done")
        step = db.next_step(conn)
        assert step["id"] == sid and step["status"] == "queued" and step["kind"] == "check"
        assert db.claim_step(conn, sid) is True and db.claim_step(conn, sid) is False
        db.set_step(conn, sid, "done", output="out/finals/a.jpg", result={"next": "approve"}, cost_usd=0.01)
        (s,) = db.steps_for(conn, first)
        assert s["status"] == "done" and s["result"] == {"next": "approve"} and s["cost_usd"] == 0.01
        with pytest.raises(ValueError):
            db.add_step(conn, first, "approve", "out/finals/a.jpg")
        with pytest.raises(ValueError):
            db.set_review(conn, first, "great")


def test_restart_fails_running_steps_and_frees_stuck_items(env):
    jid, idle = make_final(), make_final()
    with closing(db.connect()) as conn:
        for j in (jid, idle):
            db.set_status(conn, j, "done")
        sid = db.add_step(conn, jid, "repair", "out/reject/a.jpg", "drawcord_shape")
        db.set_review(conn, jid, "fixing")
        db.claim_step(conn, sid)
        db.set_review(conn, idle, "checking")  # stuck: nothing queued or running for it
        assert db.mark_interrupted(conn) == 1
        (s,) = db.steps_for(conn, jid)
        assert s["status"] == "failed" and s["error"]
        for j in (jid, idle):
            assert db.get_job(conn, j)["review"] == "needs_person"
        assert db.next_step(conn) is None


# --- unintended text on a decorated shot keeps its design (ruling 4) --------------------------

def test_unintended_text_repair_keeps_the_design():
    plain = json.loads((ROOT / "specs" / f"{SID}.json").read_text(encoding="utf-8"))
    decorated = json.loads((ROOT / "specs" / f"{LOGO_SID}.json").read_text(encoding="utf-8"))
    rule = json.loads((ROOT / "brand" / "repairs.json").read_text(encoding="utf-8"))["codes"]["unintended_text"]

    prompt, refs = repair.repair_prompt("unintended_text", plain)
    assert rule["instruction"] in prompt and refs == []
    prompt, refs = repair.repair_prompt("unintended_text", decorated)
    assert rule["instruction"] not in prompt and refs == []
    assert ("remove any text, letters or logos other than the OAKMERE ATHLETIC 3D puff embroidery at the "
            "centre chest, leaving the rest of the garment and background plain") in prompt


# --- repair_image: the CLI is unchanged -------------------------------------------------------

def test_repair_image_cli_unchanged(env, monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    image = "out/finals/zz-chain-cli_final.jpg"
    env.rows.append({"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "tier": "standard",
                     "provider": "gemini", "model": "gemini-3.1-flash-image", "size": "2K", "aspect": "4:5",
                     "file": image, "cost_usd": 0.101})

    def main(*argv):
        monkeypatch.setattr(sys, "argv", ["repair", *argv])
        code = repair.main()
        return code, capsys.readouterr().out.splitlines()

    code, out = main(str(ROOT / image), "--reason", "drawcord_shape")
    first = env.repairs[0]
    made = qa.rel(next(r["file"] for r in env.rows if r.get("tag") == f"{SID}_repair1"))
    assert code == 0 and out == [f"ok    {made}  gemini-3-pro-image  ${GEMINI_COST:.4f}  (repair 1/2 on this render)",
                                 ledger.status()]
    assert first["tier"] == "standard" and first["size"] == "2K" and first["aspect"] == "4:5"
    assert first["prompt"] == repair.repair_prompt("drawcord_shape", json.loads(
        (ROOT / "specs" / f"{SID}.json").read_text(encoding="utf-8")))[0]
    assert first["refs"] == [ROOT / image] and first["tag"] == f"{SID}_repair1"
    assert first["extra"] == {"shot_id": SID, "phase": "repair", "root": image, "repair_of": image,
                              "reason_code": "drawcord_shape", "requested_tier": "standard"}

    code, out = main(str(ROOT / image), "--reason", "hands")
    assert code == 0 and out[0].endswith("(repair 2/2 on this render)") and env.repairs[1]["tag"] == f"{SID}_repair2"
    code, out = main(str(ROOT / image), "--reason", "hands")
    assert (code, out) == (2, [f"stop  2 repairs already made on {image}; regenerate instead"])
    code, out = main(str(ROOT / "out/finals/zz-unknown.jpg"), "--reason", "hands")
    assert (code, out) == (2, ["error: image not in out/log.jsonl with a shot_id"])

    other = "out/finals/zz-chain-cli2_final.jpg"
    env.rows.append({**env.rows[0], "file": other})

    def down(*a, **k):
        raise ProviderError("gemini: 503")
    monkeypatch.setattr(repair, "run", down)
    code, out = main(str(ROOT / other), "--reason", "hands")
    assert (code, out) == (1, ["FAIL  gemini: 503"])
    assert len(env.repairs) == 2


def test_repair_image_returns_path_and_result(env):
    image = "out/finals/zz-chain-fn_final.jpg"
    env.rows.append({"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "tier": "premium",
                     "provider": "gemini", "file": image, "cost_usd": 0.134})
    path, res = repair.repair_image(ROOT / image, "drawcord_shape")
    assert path.name.endswith(f"{SID}_repair1.jpg") and res.cost_usd == GEMINI_COST
    assert env.repairs[0]["tier"] == "premium"
    path, res = repair.repair_image(ROOT / image, "drawcord_shape", tier="standard")  # the routed tier
    assert env.repairs[1]["tier"] == "standard" and env.repairs[1]["extra"]["requested_tier"] == "premium"
    with pytest.raises(repair.RepairLimitReached):
        repair.repair_image(ROOT / image, "hands")
    with pytest.raises(repair.NotARender):
        repair.repair_image(ROOT / "out/finals/zz-nope.jpg", "hands")
    assert len(env.repairs) == 2


# --- helpers that need real files -------------------------------------------------------------

def make_file(env, name: str) -> str:
    """A real (tiny) image under out/finals, removed with everything derived from it after the test."""
    path = ROOT / "out" / "finals" / f"zz-chain-{name}-{uuid.uuid4().hex[:8]}_final.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9")
    env.made.append(path)
    return path.relative_to(ROOT).as_posix()


# --- fix round 1 ----------------------------------------------------------------------------

def approved_anchor(env, moved: bool = False) -> str:
    """A washed-black 450 packshot render with an anchor_approved event; optionally moved to out/reject since."""
    anchor_final = make_file(env, "anchor")
    env.rows.append({"ts": stamp(), "status": "ok", "shot_id": SID, "phase": "final", "tier": "standard",
                     "provider": "gemini", "file": anchor_final, "cost_usd": 0.101})
    if moved:  # really moved: the render exists, but in out/reject
        rejected = ROOT / "out" / "reject" / Path(anchor_final).name
        rejected.parent.mkdir(parents=True, exist_ok=True)
        (ROOT / anchor_final).replace(rejected)
        env.made.append(rejected)
        env.rows.append({"ts": stamp(), "event": "moved", "file": anchor_final,
                         "moved_to": f"out/reject/{rejected.name}"})
    env.rows.append({"ts": stamp(), "event": "anchor_approved", "file": anchor_final,
                     "anchor": "refs/anchors/pullover-oversized_washed-black_450_front.png", "by": "owner"})
    return anchor_final


def paid_calls(env, shot: str) -> list[dict]:
    """Paid image calls for one shot (an approved anchor's own final belongs to another shot)."""
    return [r for r in gemini_rows(env) if r.get("status") == "ok" and r.get("phase") in ("final", "repair")
            and r.get("shot_id") == shot]


def test_composite_with_any_other_fault_needs_person_no_paid_repair(env):
    """Important 1: a fault on the composite (a non-logo one too) never buys a repair; a person looks."""
    approved_anchor(env)
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}] + [{"visual": "drawcord_shape"}] * 12
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner), limit=80)
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check", "composite", "check"]
    assert len(env.repairs) == 1 and len(paid_calls(env, LOGO_SID)) == 2  # the final and its one logo repair
    j = job(jid)
    assert j["review"] == "needs_person" and "real logo" in j["review_reason"]


@pytest.mark.parametrize("scenario", ["repairs_everywhere", "composite_then_faults", "logo_then_faults"])
def test_paid_calls_never_exceed_the_worst_case_shown(env, scenario):
    """At most 1 final + 2 new images, each with at most 2 repairs: 9 x the base cost shown before Approve."""
    if scenario == "repairs_everywhere":
        env.script, shot = [{"visual": "drawcord_shape"}] * 40, SID
    elif scenario == "composite_then_faults":
        approved_anchor(env)
        env.script, shot = [{"logo": LOGO_TEXT}] * 2 + [{"visual": "drawcord_shape"}] * 40, LOGO_SID
    else:
        env.script, shot = [{"logo": LOGO_TEXT}] + [{"visual": "drawcord_shape"}] * 40, LOGO_SID
    jid = make_final(shot)
    drain(Worker(db.connect, runner=env.runner), limit=80)
    assert len(paid_calls(env, shot)) <= 9
    assert job(jid)["review"] == "needs_person"
    if scenario == "repairs_everywhere":
        assert len(paid_calls(env, shot)) == 9  # the worst case is reachable, and nothing beyond it


def test_composite_never_spends_the_anchors_allowance(env):
    """The composite is its own render: correcting its colour counts against it, not against the anchor."""
    anchor_final = approved_anchor(env)
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}, {"colour": COLOUR_DRIFT}]
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert [k for k, _ in kinds(jid)] == ["check", "repair", "check", "composite", "check", "finish", "check"]
    comp = steps(jid)[3]["output"]
    cc = next(r for r in env.rows if r.get("op") == "colour_correct")
    assert cc["root"] == comp
    assert not [r for r in env.rows if r.get("root") == anchor_final]
    assert job(jid)["review"] == "ready"


def test_regenerate_rechecks_the_shot_limit_when_it_runs(env):
    """Important 2: another final of the shot that lands between a check and its new image counts at spend time."""
    env.script = [{"visual": "framing"}, {"visual": "framing"}]
    a, b = make_final(), make_final()  # one shot: both finals run before either check
    drain(Worker(db.connect, runner=env.runner), limit=80)
    finals = [r for r in env.rows if r.get("phase") == "final" and r.get("shot_id") == SID]
    assert len(finals) == 3  # 1 final + 2 new images at most; the third regeneration never ran
    stopped = next(j for j in (a, b) if job(j)["review"] == "needs_person")
    regen = steps(stopped)[-1]
    assert regen["kind"] == "regenerate" and regen["status"] == "failed" and regen["cost_usd"] == 0.0
    assert job(stopped)["review_reason"] == worker_mod.LIMITS_USED


def test_step_cost_is_the_steps_own_calls(env):
    """M1: calls logged in the step carry its tag; a call logged meanwhile by another thread is not its cost."""
    class Costly(FakeBrain):
        def ask_json(self, *a, **k):
            ledger.record({"event": "brain", "provider": "claude", "user": "owner", "cost_usd": 0.02})
            other = threading.Thread(target=lambda: ledger.record(
                {"event": "brain", "provider": "claude", "user": "teammate", "cost_usd": 0.5}))
            other.start()
            other.join()
            return super().ask_json(*a, **k)
    env.brain = Costly(env)
    jid = make_final()
    drain(Worker(db.connect, runner=env.runner))
    (check,) = steps(jid)
    assert check["cost_usd"] == 0.02
    mine = next(r for r in env.rows if r.get("user") == "owner" and r.get("event") == "brain")
    theirs = next(r for r in env.rows if r.get("user") == "teammate")
    assert (mine["review_step"], mine["job_id"]) == (check["id"], jid) and "review_step" not in theirs


def test_ledger_tags_follow_the_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "LOG", tmp_path / "log.jsonl")
    with ledger.tagged(review_step=7, job_id=3):
        ledger.record({"event": "a", "cost_usd": 0.1})
        other = threading.Thread(target=lambda: ledger.record({"event": "b"}))
        other.start()
        other.join()
    ledger.record({"event": "c"})
    a, b, c = ledger.entries()
    assert (a["review_step"], a["job_id"]) == (7, 3) and "review_step" not in b and "review_step" not in c


def test_done_final_and_its_check_are_one_transaction(env):
    """M2: a final is never done without its check queued and its review set."""
    jid = make_final()
    with closing(db.connect()) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            db.complete_final(conn, jid, {"files": [], "cost_usd": 0.1}, None)  # the step insert fails
        j = db.get_job(conn, jid)
        assert j["status"] == "queued" and j["review"] is None and db.steps_for(conn, jid) == []
        db.complete_final(conn, jid, {"files": ["out/finals/a.jpg"], "cost_usd": 0.1}, "out/finals/a.jpg")
        j = db.get_job(conn, jid)
        assert (j["status"], j["review"]) == ("done", "checking")
        assert [(s["kind"], s["status"], s["input"]) for s in db.steps_for(conn, jid)] == [
            ("check", "queued", "out/finals/a.jpg")]


def test_restart_flags_finals_that_were_never_checked(env):
    """M2: a done final with no review and no steps (a crash, or a final from before the chain) needs a person."""
    legacy, checked, drafts = make_final(), make_final(), make_final()
    with closing(db.connect()) as conn:
        for j in (legacy, drafts):
            db.set_status(conn, j, "done")
        with conn:
            conn.execute("UPDATE jobs SET kind = 'drafts' WHERE id = ?", (drafts,))
        db.complete_final(conn, checked, {"files": ["out/finals/a.jpg"], "cost_usd": 0.1}, "out/finals/a.jpg")
        assert db.mark_interrupted(conn) == 0
        assert db.get_job(conn, legacy)["review"] == "needs_person" and db.get_job(conn, legacy)["review_reason"]
        assert db.get_job(conn, checked)["review"] == "checking"
        assert db.get_job(conn, drafts)["review"] is None


def test_check_decides_before_it_moves(env, monkeypatch):
    """M3: if deciding the next step fails, the render has not been moved and the step simply fails."""
    def broken(spec):
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(worker_mod, "anchor_source", broken)
    approved_anchor(env)
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}]
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert kinds(jid) == [("check", "done"), ("repair", "done"), ("check", "failed")]
    repaired = steps(jid)[1]["output"]
    assert Path(repaired).name not in env.moves and len(env.moves) == 1  # only the final, judged earlier
    assert job(jid)["review"] == "needs_person" and "ledger unreadable" in job(jid)["review_reason"]


def test_composite_source_only_from_finals(env):
    """M4: an approved anchor render that has since gone to out/reject is no source."""
    approved_anchor(env, moved=True)
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}]
    jid = make_final(LOGO_SID)
    drain(Worker(db.connect, runner=env.runner))
    assert env.composites == [] and job(jid)["review_reason"] == worker_mod.NO_SOURCE


@pytest.mark.parametrize("change", ["full back", "close-up camera", "on model"])
def test_composite_only_onto_a_front_packshot(env, change):
    """M5: the front packshot only takes a front chest logo on a front packshot shot; anything else needs a person."""
    approved_anchor(env)
    spec = json.loads((ROOT / "specs" / f"{LOGO_SID}.json").read_text(encoding="utf-8"))
    shot = spec["shot"]
    if change == "full back":
        shot["decoration"][0]["placement"] = "full back"
    elif change == "close-up camera":
        shot["camera"]["angle"] = "straight on, slightly above the decoration"
    else:
        shot["model"] = {"present": True, "cast": "Maya", "pose": "standing relaxed"}
    with closing(db.connect()) as conn:
        jid = db.create_job(conn, "owner", "final", {}, spec, {"base_usd": 0.1})
    env.script = [{"logo": LOGO_TEXT}, {"logo": LOGO_TEXT}]
    drain(Worker(db.connect, runner=env.runner))
    assert steps(jid)[-1]["result"]["next"] == "composite" and env.composites == []
    assert job(jid)["review"] == "needs_person" and job(jid)["review_reason"] == worker_mod.NO_SOURCE
