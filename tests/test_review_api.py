"""Review, rules and library API (studio.app endpoints, studio.library, studio.tasks.draft_rule).

Fake brain, in-memory ledger and a temporary database, anchors folder, rules file and specs folder: no paid calls,
nothing written to refs/anchors or brand/. Images are small real files under out/ (git-ignored) with zz-test names,
removed afterwards together with their out/export folders.
"""
import json
import shutil
import threading
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline import compile as compiler
from pipeline import finish, ledger, qa
from pipeline import specs as specs_mod
from pipeline.compile import WORD_RANGE, compile_spec, load_json
from pipeline.specs import build_spec
from studio import app as app_mod
from studio import db, library
from studio import worker as worker_mod
from studio.brain import BrainError
from studio.tasks import draft_rule, rule_scopes

ROOT = Path(__file__).resolve().parents[1]
PLAIN = {"purpose": "website", "catalogue": "pullover-oversized", "colour": "olive", "gsm": 380,
         "recipe": "packshot-front", "decoration": "none"}
ON_MODEL = {**PLAIN, "recipe": "on-model-front", "cast": "Maya", "scene": "warm-studio"}
DECORATED = {**PLAIN, "decoration": "design", "design": "oakmere-athletic", "technique": "3D puff embroidery",
             "placement": "centre chest", "size_cm": 22}
TEAL = {**PLAIN, "colour": {"name": "Teal", "hex": "#1F6F78"}}
CAPTIONS = load_json("brand/brand.json")["captions"]
STYLE_BLOCK = load_json("brand/brand.json")["style_block"]
ALREADY = {"error": "already decided"}
FIX_RUNNING = {"error": "a fix is already running"}
CHECKS_RUNNING = {"error": "checks are already running"}
LIMIT = {"error": "the repair limit for this image is reached; ask for a new image instead"}
EXISTS = "a reference packshot already exists for this colour"
NOT_FOUND = {"error": "image not found"}
ANCHOR = "pullover-oversized_olive_380_front.png"

COLOUR_PASS = {"result": "pass", "code": None, "de2000": 2.14, "measured_hex": "#5A5A3D", "target_hex": "#5B5B3C",
               "dL": 0.1, "da": 0.2, "db": -0.3, "mask_source": "vision (cached)", "mask_fraction": 0.4}
STRAY_PASS = {"result": "pass", "code": None, "found": [], "stray": []}
LINES = ["Two drawcords, equal length, metal aglets, through eyelets", "Hood double-layer with centre seam"]


def check(codes=(), nxt="approve", code=None, complete=True, reason=None, bad_line=None, note="two flat cords"):
    """A run_checks result; bad_line: (line index, ok, code) for the AI review's one line that is not a pass."""
    lines = [{"text": t, "ok": True, "code": None, "note": note} for t in LINES]
    if bad_line:
        i, ok, c = bad_line
        lines[i] = {**lines[i], "ok": ok, "code": c}
    return {"colour": COLOUR_PASS, "stray": STRAY_PASS, "logo": None,
            "visual": {"lines": lines, "provider": "fake", "fallback": False}, "codes": list(codes),
            "next": nxt, "next_code": code, "complete": complete, "reason": reason}


class FakeBrain:
    def __init__(self):
        self.calls, self.error = [], None
        self.answer = {"prompt_text": "Show the hood standing up in a full, rounded shape.", "applies_to": "packshot"}

    def ask_json(self, task, prompt, schema, images=(), user="owner"):
        self.calls.append({"task": task, "prompt": prompt, "schema": schema, "user": user})
        if self.error:
            raise BrainError(self.error)
        return SimpleNamespace(data=self.answer, provider="fake", fallback=False)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(k, "test")
    rows: list[dict] = []
    lock = threading.Lock()

    def record(e):
        with lock:
            rows.append({"ts": ledger.now().isoformat(timespec="seconds"), **ledger.tags(), **e})
            return rows[-1]
    monkeypatch.setattr(ledger, "entries", lambda: rows)
    monkeypatch.setattr(ledger, "record", record)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "studio.db")
    specs = tmp_path / "specs"
    monkeypatch.setattr(worker_mod, "SPEC_DIR", specs)
    monkeypatch.setattr(library, "ANCHORS_DIR", tmp_path / "anchors")
    monkeypatch.setattr(compiler, "RULES_PATH", tmp_path / "prompt_rules.json")
    real_spec_path = qa.spec_path

    def spec_path(shot_id):
        p = specs / f"{shot_id}.json"
        return p if p.exists() else real_spec_path(shot_id)
    monkeypatch.setattr(qa, "spec_path", spec_path)
    monkeypatch.setattr(finish, "spec_path", spec_path)
    brain = FakeBrain()
    monkeypatch.setattr(app_mod, "get_brain", lambda user, fast=False: brain)

    s = SimpleNamespace(rows=rows, record=record, brain=brain, anchors=tmp_path / "anchors",
                        rules=tmp_path / "prompt_rules.json", made=[], sids=[])

    def image(folder="finals", name=None, size=(40, 50), colour=(90, 90, 60), suffix=".jpg") -> str:
        path = ROOT / "out" / folder / f"{name or 'zz-test-' + uuid.uuid4().hex[:8]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, colour).save(path)
        s.made.append(path)
        return path.relative_to(ROOT).as_posix()
    s.image = image

    def final(answers=PLAIN, review="ready", layout=None, user="owner", size=(40, 50), colour=(90, 90, 60),
              result=None, reason=None, cost=0.101):
        """A done final job: its render logged and its check queued (review "checking"); with `review` other than
        "checking" the check is done with `result` (a pass by default) and the review set."""
        sid = f"zz-test-{uuid.uuid4().hex[:8]}"
        spec = build_spec(answers)
        spec["shot"]["shot_id"] = spec["set"]["set_id"] = sid
        specs.mkdir(parents=True, exist_ok=True)
        (specs / f"{sid}.json").write_text(json.dumps(spec), encoding="utf-8")
        file = image("finals", f"{sid}_final", size, colour)
        record({"shot_id": sid, "tier": "standard", "provider": "gemini", "model": "gemini-3.1-flash-image",
                "phase": "final", "status": "ok", "file": file, "cost_usd": cost, "size": "2K", "aspect": "4:5"})
        with closing(db.connect()) as conn:
            jid = db.create_job(conn, user, "final", answers, spec, {"base_usd": cost}, layout=layout)
            db.complete_final(conn, jid, {"files": [file], "cost_usd": cost}, file)
            (step,) = db.steps_for(conn, jid)
            if review != "checking":
                db.set_step(conn, step["id"], "done", output=file, result=result or check(), cost_usd=0.01)
                db.set_review(conn, jid, review, reason=reason)
        s.sids.append(sid)
        return SimpleNamespace(id=jid, sid=sid, file=file, spec=spec)
    s.final = final

    def step(jid, kind, input, output=None, status="done", code=None, result=None, cost=0.0, error=None):
        with closing(db.connect()) as conn:
            sid = db.add_step(conn, jid, kind, input, code)
            if status != "queued":
                fields = {"output": output, "result": result, "cost_usd": cost}
                if error:
                    fields["error"] = error
                db.set_step(conn, sid, status, **fields)
        return sid
    s.step = step

    def move(file) -> str:
        """As qa.move_to_reject: the render goes to out/reject and the move is logged."""
        dest = ROOT / "out" / "reject" / Path(file).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(ROOT / file, dest)
        s.made.append(dest)
        record({"event": "moved", "file": file, "moved_to": dest.relative_to(ROOT).as_posix()})
        return dest.relative_to(ROOT).as_posix()
    s.move = move
    s.events = lambda kind: [r for r in rows if r.get("event") == kind]

    yield s
    for p in s.made:
        p.unlink(missing_ok=True)
    for sid in s.sids:
        shutil.rmtree(ROOT / "out" / "export" / sid, ignore_errors=True)


@pytest.fixture
def client():
    return TestClient(app_mod.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def job(jid: int) -> dict:
    with closing(db.connect()) as conn:
        return db.get_job(conn, jid)


def steps(jid: int) -> list[dict]:
    with closing(db.connect()) as conn:
        return db.steps_for(conn, jid)


def race(*calls):
    """Run the calls at the same moment, one thread each; their responses in call order."""
    barrier, out = threading.Barrier(len(calls)), [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        out[i] = fn()
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def post(client, jid, verb, body=None):
    return client.post(f"/api/reviews/{jid}/{verb}", json=body if body is not None else {})


# --- reviews -------------------------------------------------------------------------------------
def test_review_list_and_item(env, client):
    # a final judged bad, moved to out/reject, repaired once, then passed
    j = env.final(PLAIN, review="checking")
    moved = env.move(j.file)
    (first,) = steps(j.id)
    with closing(db.connect()) as conn:
        db.set_step(conn, first["id"], "done", output=moved, cost_usd=0.01, result=check(
            ["drawcord_shape"], "repair", "drawcord_shape", bad_line=(0, False, "drawcord_shape")))
    fixed = env.image("finals", f"{j.sid}_repair1")
    env.record({"shot_id": j.sid, "tier": "standard", "provider": "gemini", "model": "gemini-3.1-flash-image",
                "phase": "repair", "status": "ok", "file": fixed, "root": j.file, "reason_code": "drawcord_shape",
                "cost_usd": 0.101})
    env.step(j.id, "repair", moved, fixed, code="drawcord_shape", cost=0.101)
    env.step(j.id, "check", fixed, fixed, result=check(), cost=0.01)
    with closing(db.connect()) as conn:
        db.set_review(conn, j.id, "ready")

    # needs a person after a failed step whose words name a folder and a setting
    k = env.final(ON_MODEL, review="checking")
    (kc,) = steps(k.id)
    with closing(db.connect()) as conn:
        db.set_step(conn, kc["id"], "failed", error="stopped: brand/designs/x.png needs STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
        db.set_review(conn, k.id, "needs_person", reason="The check stopped: see out/specs/zz.json and MY_API_KEY")

    # not listed: another user's final, a sketch job, a final still being made
    env.final(PLAIN, user="teammate")
    with closing(db.connect()) as conn:
        db.create_job(conn, "owner", "drafts", PLAIN, build_spec(PLAIN), {})
        db.create_job(conn, "owner", "final", PLAIN, build_spec(PLAIN), {})

    r = client.get("/api/reviews")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [k.id, j.id]
    item = items[1]
    assert item["review"] == "ready" and item["reason"] is None and item["shot_id"] == j.sid
    assert item["latest"] == fixed and item["images"] == [moved, fixed]
    assert [(s["kind"], s["status"], s["output"]) for s in item["steps"]] == [
        ("check", "done", moved), ("repair", "done", fixed), ("check", "done", fixed)]
    rep = item["steps"][1]
    assert rep["code"] == "drawcord_shape" and rep["fault"] == "the drawcords" and rep["label"] == "repair"
    assert rep["cost_usd"] == 0.101 and rep["error"] is None
    assert item["cost_usd"] == round(0.101 + 0.01 + 0.101 + 0.01, 4)
    assert item["limits_left"] == {"repairs": 1, "regenerations": 2}
    assert item["caption"] == CAPTIONS["concept_render"]
    assert item["words"].startswith("Packshot, front") and "Olive" in item["words"]
    checks = item["checks"]
    assert checks["complete"] is True and checks["reason"] is None and checks["fixed"] == ["the drawcords"]
    assert checks["colour"] == {"ok": True, "difference": 2.1, "limit": 5, "words": "Colour difference 2.1 (limit 5)"}
    assert checks["text"]["ok"] is True and checks["logo"] == []
    assert checks["lines"] == [{"text": t, "ok": True, "note": "two flat cords", "fault": None} for t in LINES]
    assert item["actions"] == {"approve": True, "note_required": False, "reject": True, "anchor": True,
                               "anchor_exists": False, "change": True, "check": False}
    codes = [c["code"] for c in item["changes"]]
    assert {"code": "drawcord_shape", "label": "Fix the drawcords"} in item["changes"]
    assert "hands" not in codes and "zip" not in codes and not any(c.startswith("logo_") for c in codes)
    assert "drape_too_thin" not in codes  # a new image, not a repair

    other = items[0]
    assert other["review"] == "needs_person" and other["caption"] == CAPTIONS["on_model"]
    assert other["reason"] == "The check stopped: see zz.json and a setting"
    assert other["steps"][0]["error"] == "stopped: x.png needs a setting" and other["checks"] is None
    assert other["actions"]["note_required"] is True and other["actions"]["anchor"] is False
    assert "hands" in [c["code"] for c in other["changes"]]
    for leak in ("brand/", "out/specs", "refs/", "STUDIO_", "API_KEY"):
        assert leak not in r.text, leak

    assert client.get(f"/api/reviews/{j.id}").json() == item
    with closing(db.connect()) as conn:
        drafts = db.create_job(conn, "owner", "drafts", PLAIN, build_spec(PLAIN), {})
    for missing in (drafts, 9999):
        assert client.get(f"/api/reviews/{missing}").status_code == 404


def test_review_steps_name_their_output_file(env, client):
    """The page names each image of the chain by the step that made it. A render the checks moved to out/reject is
    no longer at the step's output path (so that path is not served), but it keeps its file name: each step carries
    that name, never a folder. A step with no output has none."""
    j = env.final(PLAIN, review="checking")
    moved = env.move(j.file)
    (first,) = steps(j.id)
    with closing(db.connect()) as conn:
        db.set_step(conn, first["id"], "done", output=moved, cost_usd=0.01, result=check(
            ["drawcord_shape"], "repair", "drawcord_shape", bad_line=(0, False, "drawcord_shape")))
    fixed = env.image("finals", f"{j.sid}_repair1")
    env.step(j.id, "repair", moved, fixed, code="drawcord_shape")
    fixed_moved = env.move(fixed)  # the next check set the repair aside too
    env.step(j.id, "check", fixed, fixed_moved, result=check(
        ["hood_shape"], "regenerate", bad_line=(1, False, "hood_shape")))
    env.step(j.id, "regenerate", fixed_moved, None, status="failed", error="stopped")
    with closing(db.connect()) as conn:
        db.set_review(conn, j.id, "needs_person", reason="please look")

    item = client.get(f"/api/reviews/{j.id}").json()
    assert item["images"] == [moved, fixed_moved]
    rep = item["steps"][1]
    assert rep["kind"] == "repair" and rep["output"] is None  # its path is gone: not served
    assert rep["file"] == Path(fixed_moved).name == Path(fixed).name
    assert [s["file"] for s in item["steps"]] == [Path(moved).name, Path(fixed).name, Path(fixed_moved).name, None]
    assert all("/" not in (s["file"] or "") and "\\" not in (s["file"] or "") for s in item["steps"])


def test_approve_once_then_409(env, client):
    j = env.final()
    r = post(client, j.id, "approve", {"note": ""})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["review"] == "approved" and job(j.id)["review"] == "approved"
    (ev,) = env.events("approved")
    assert (ev["file"], ev["by"], ev["shot_id"]) == (j.file, "owner", j.sid)
    for verb, body in (("approve", {}), ("reject", {"note": "too dark"}), ("anchor", {"replace": True}),
                       ("change", {"code": "drawcord_shape"})):
        r = post(client, j.id, verb, body)
        assert (r.status_code, r.json()) == (409, ALREADY), verb
    assert len(env.events("approved")) == 1 and not env.events("rejected") and not env.events("anchor_approved")
    assert len(steps(j.id)) == 1 and not env.rules.exists() and not env.anchors.exists()

    # an image that needs a person is approved only with a note saying why
    n = env.final(review="needs_person", reason="The face could not be checked against a reference photo; please look.")
    r = post(client, n.id, "approve", {"note": "  "})
    assert r.status_code == 422 and job(n.id)["review"] == "needs_person"
    r = post(client, n.id, "approve", {"note": "face is fine"})
    assert r.status_code == 200 and env.events("approved")[-1]["note"] == "face is fine"

    # no verdict while the checks or a fix run
    c = env.final(review="checking")
    r = post(client, c.id, "approve")
    assert r.status_code == 409 and "still running" in r.json()["error"] and job(c.id)["review"] == "checking"

    # two people at once: exactly one verdict
    for _ in range(4):
        t = env.final()
        a, b = race(lambda: post(client, t.id, "approve"), lambda: post(client, t.id, "reject", {"note": "too dark"}))
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        assert (a if a.status_code == 409 else b).json() == ALREADY
        verdicts = [e for e in env.rows if e.get("event") in ("approved", "rejected") and e["file"] == t.file]
        assert len(verdicts) == 1 and job(t.id)["review"] == verdicts[0]["event"]


def test_reject_creates_pending_rule_with_draft(env, client):
    j = env.final()
    assert post(client, j.id, "reject", {"note": " "}).status_code == 422
    assert job(j.id)["review"] == "ready" and not env.events("rejected")

    r = post(client, j.id, "reject", {"note": "the hood looks flat"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["review"] == "rejected" and job(j.id)["review"] == "rejected"
    (ev,) = env.events("rejected")
    assert (ev["file"], ev["by"], ev["note"]) == (j.file, "owner", "the hood looks flat")
    (call,) = env.brain.calls
    assert call["task"] == "draft_rule" and "the hood looks flat" in call["prompt"]
    assert "Packshot, front" in call["prompt"] and "style id pullover-oversized" in call["prompt"]
    assert call["schema"]["properties"]["applies_to"]["enum"] == rule_scopes()
    rules = json.loads(env.rules.read_text(encoding="utf-8"))["rules"]
    assert len(rules) == 1
    assert rules[0] | {"date": None} == {"status": "pending", "applies_to": "packshot", "note": "the hood looks flat",
                                         "prompt_text": "Show the hood standing up in a full, rounded shape.",
                                         "from_shot": j.sid, "date": None}
    assert r.json()["rule"]["index"] == 0 and r.json()["rule"]["prompt_text"] == rules[0]["prompt_text"]

    # the brain does not answer: still rejected, the rule waits with an empty draft for the person to write
    env.brain.error = "busy"
    k = env.final()
    r = post(client, k.id, "reject", {"note": "colour too green"})
    assert r.status_code == 200 and job(k.id)["review"] == "rejected"
    rule = json.loads(env.rules.read_text(encoding="utf-8"))["rules"][1]
    assert (rule["status"], rule["prompt_text"], rule["applies_to"], rule["note"]) == (
        "pending", "", "all", "colour too green")

    # an answer that breaks the rules is not kept either
    env.brain.error = None
    env.brain.answer = {"prompt_text": "Show the hood. Make it big.", "applies_to": "everything"}
    m = env.final()
    assert post(client, m.id, "reject", {"note": "hood"}).status_code == 200
    rule = json.loads(env.rules.read_text(encoding="utf-8"))["rules"][2]
    assert (rule["status"], rule["prompt_text"], rule["applies_to"]) == ("pending", "", "all")


def test_reject_records_both_or_neither_and_a_lost_draft_is_no_error(env, client, monkeypatch):
    j = env.final()

    def no_rejected_event(e):
        if e.get("event") == "rejected":
            raise OSError("disk full")
        return env.record(e)

    def broken(*args, **kwargs):
        raise OSError("disk full")
    for where, name, fn in ((ledger, "record", no_rejected_event), (compiler, "save_rules", broken)):
        with monkeypatch.context() as m:
            m.setattr(where, name, fn)
            r = post(client, j.id, "reject", {"note": "too dark"})
        assert (r.status_code, r.json()) == (500, {"error": app_mod.NOT_SAVED}), name
        assert job(j.id)["review"] == "ready" and not env.events("rejected"), name
        assert compiler.load_rules()["rules"] == [], name  # the rule goes with the rejection it came from
    assert not env.brain.calls

    # the brain's draft cannot be stored: the rejection stands and the rule waits for the person's words
    with monkeypatch.context() as m:
        m.setattr(library, "store_draft", broken)
        r = post(client, j.id, "reject", {"note": "too dark"})
    assert r.status_code == 200, r.text
    assert job(j.id)["review"] == "rejected" and len(env.events("rejected")) == 1
    assert r.json()["rule"] | {"date": None} == {
        "index": 0, "status": "pending", "note": "too dark", "prompt_text": "", "applies_to": "all",
        "from_shot": j.sid, "date": None, "activated_by": None, "dismissed_by": None}


def test_draft_rule_one_positive_sentence_and_known_scope():
    brain = FakeBrain()
    brain.answer = {"prompt_text": '  "The hood stands up\n in a full, rounded shape."  ', "applies_to": "zip"}
    assert draft_rule("hood flat", brain) == {"prompt_text": "The hood stands up in a full, rounded shape.",
                                              "applies_to": "zip"}
    assert rule_scopes() == ["all", "packshot", "on_model", "pullover-classic", "pullover-oversized", "zip"]
    for text in (" ".join(["word"] * 41), "Show the hood. Make it big.", "Do not show a flat hood.",
                 "Never flatten the hood.", "", None, 42):
        brain.answer = {"prompt_text": text, "applies_to": "all"}
        assert draft_rule("hood flat", brain)["prompt_text"] == "", text
    brain.answer = {"prompt_text": " ".join(["word"] * 40), "applies_to": "on_model"}
    assert draft_rule("x", brain) == {"prompt_text": " ".join(["word"] * 40), "applies_to": "on_model"}
    brain.answer = {"prompt_text": "Show a full hood.", "applies_to": "everything"}
    assert draft_rule("x", brain) == {"prompt_text": "Show a full hood.", "applies_to": "all"}
    brain.answer = ["not", "a", "dict"]
    assert draft_rule("x", brain) == {"prompt_text": "", "applies_to": "all"}
    brain.error = "down"
    assert draft_rule("x", brain) == {"prompt_text": "", "applies_to": "all"}


def test_change_respects_limits_and_single_running(env, client):
    j = env.final()
    for code in ("drape_too_thin", "colour_drift", "face", "nope", "logo_text", "hands", "zip"):
        r = post(client, j.id, "change", {"code": code})
        assert r.status_code == 422, code
    assert len(steps(j.id)) == 1 and job(j.id)["review"] == "ready"

    r = post(client, j.id, "change", {"code": "drawcord_shape"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["review"] == "fixing" and job(j.id)["review"] == "fixing"
    fix = steps(j.id)[-1]
    assert (fix["kind"], fix["status"], fix["input"], fix["code"]) == ("repair", "queued", j.file, "drawcord_shape")
    assert (post(client, j.id, "change", {"code": "rib_width"}).json()) == FIX_RUNNING
    r = post(client, j.id, "approve")
    assert r.status_code == 409 and "still running" in r.json()["error"]
    assert len(steps(j.id)) == 2

    # the render already has its two repairs: no third, and nothing queued
    k = env.final()
    for n in (1, 2):
        env.record({"shot_id": k.sid, "phase": "repair", "status": "ok", "root": k.file, "cost_usd": 0.101,
                    "file": f"out/finals/{k.sid}_repair{n}.jpg", "provider": "gemini"})
    r = post(client, k.id, "change", {"code": "drawcord_shape"})
    assert (r.status_code, r.json()) == (409, LIMIT)
    assert len(steps(k.id)) == 1 and job(k.id)["review"] == "ready"
    item = client.get(f"/api/reviews/{k.id}").json()
    assert item["limits_left"]["repairs"] == 0 and item["actions"]["change"] is False

    # two clicks at once: one repair queued
    m = env.final()
    a, b = race(lambda: post(client, m.id, "change", {"code": "drawcord_shape"}),
                lambda: post(client, m.id, "change", {"code": "hood_shape"}))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert (a if a.status_code == 409 else b).json() == FIX_RUNNING
    assert [s["kind"] for s in steps(m.id)] == ["check", "repair"]

    # the decorated shot may ask for its logo, the on-model one for its hands
    d = env.final(DECORATED)
    assert post(client, d.id, "change", {"code": "logo_size"}).status_code == 200
    h = env.final(ON_MODEL)
    assert post(client, h.id, "change", {"code": "hands"}).status_code == 200


def test_queue_step_takes_one_of_two_at_once(env):
    """Two requests queue a step for one job at the same moment, each on its own connection: exactly one does."""
    for _ in range(8):
        j = env.final()

        def queue():
            with closing(db.connect()) as conn:
                return db.queue_step(conn, j.id, "repair", j.file, "hood_shape", library.ACTIONABLE, "fixing")
        got = race(queue, queue)
        assert sum(g is not None for g in got) == 1
        assert [s["status"] for s in steps(j.id)] == ["done", "queued"] and job(j.id)["review"] == "fixing"


def test_check_queues_once_on_unchecked_or_needs_person(env, client):
    legacy = env.final(review="checking")
    with closing(db.connect()) as conn:  # a final from before the review chain: no review, no steps
        conn.execute("DELETE FROM review_steps WHERE job_id = ?", (legacy.id,))
        conn.execute("UPDATE jobs SET review = NULL WHERE id = ?", (legacy.id,))
        conn.commit()
    r = post(client, legacy.id, "check")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["review"] == "checking"
    assert [(s["kind"], s["status"], s["input"]) for s in steps(legacy.id)] == [("check", "queued", legacy.file)]
    assert post(client, legacy.id, "check").json() == CHECKS_RUNNING
    assert len(steps(legacy.id)) == 1

    n = env.final(review="needs_person", result=check(complete=False, reason="The AI visual review did not answer.",
                                                       nxt="flag", bad_line=(1, None, None)))
    assert post(client, n.id, "check").status_code == 200 and job(n.id)["review"] == "checking"
    assert [s["status"] for s in steps(n.id)] == ["done", "queued"]

    ready = env.final()
    r = post(client, ready.id, "check")
    assert r.status_code == 409 and job(ready.id)["review"] == "ready"
    approved = env.final()
    post(client, approved.id, "approve")
    assert (post(client, approved.id, "check").json()) == ALREADY

    gone = env.final(review="needs_person")  # set aside by the checks: out/reject is not checked again
    with closing(db.connect()) as conn:
        db.set_step(conn, steps(gone.id)[0]["id"], "done", output=env.move(gone.file), result=check(nxt="flag"))
    r = post(client, gone.id, "check")
    assert r.status_code == 409 and len(steps(gone.id)) == 1

    twice = env.final(review="needs_person")
    a, b = race(lambda: post(client, twice.id, "check"), lambda: post(client, twice.id, "check"))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert [s["status"] for s in steps(twice.id)] == ["done", "queued"]


# --- reference packshots -------------------------------------------------------------------------
def test_anchor_refuses_sketch_decorated_or_other_recipe(env, client):
    sketch = env.image("drafts")
    sketched = env.final(PLAIN, layout=sketch)  # like out/finals/20261115T111133_...: made from a sketch
    decorated, on_model = env.final(DECORATED), env.final(ON_MODEL)
    composite = env.final(PLAIN)
    placed = env.image("finals", f"{composite.sid}_logo")
    env.record({"shot_id": composite.sid, "provider": "local", "phase": "finish", "op": "composite",
                "status": "ok", "file": placed, "source": composite.file, "root": placed, "cost_usd": 0.0})
    env.step(composite.id, "composite", composite.file, placed)
    env.step(composite.id, "check", placed, placed, result=check())
    cases = [(sketched, "sketch"), (decorated, "plain front packshot"), (on_model, "plain front packshot"),
             (composite, "logo placement")]
    for case, why in cases:
        item = client.get(f"/api/reviews/{case.id}").json()
        assert item["actions"]["anchor"] is False
        # a plain front packshot says why it has no reference-packshot button; any other shot says nothing
        assert (item["anchor_note"] is None) if why == "plain front packshot" else (why in item["anchor_note"])
        for body in ({}, {"replace": True}):
            r = post(client, case.id, "anchor", body)
            assert r.status_code == 409 and why in r.json()["error"], (why, r.text)
        assert job(case.id)["review"] == "ready"
    assert not env.anchors.exists() or not any(env.anchors.iterdir())
    assert not env.events("anchor_approved") and not env.events("approved")


def test_anchor_writes_png_and_needs_replace(env, client):
    a = env.final(PLAIN, colour=(90, 90, 60))
    assert client.get(f"/api/reviews/{a.id}").json()["anchor_note"] is None  # it can: the button says it all
    r = post(client, a.id, "anchor")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["anchor_note"] is None  # decided: nothing more to explain
    target = env.anchors / ANCHOR
    with Image.open(target) as im:
        assert im.format == "PNG" and im.size == (40, 50)
    assert sorted(p.name for p in env.anchors.iterdir()) == [ANCHOR]  # no temporary file left behind
    assert r.json()["anchor"] == ANCHOR and r.json()["item"]["review"] == "anchor" and "refs/" not in r.text
    (ev,) = env.events("anchor_approved")
    assert (ev["file"], ev["anchor"], ev["by"]) == (a.file, f"refs/anchors/{ANCHOR}", "owner")
    (ap,) = env.events("approved")
    assert (ap["file"], ap["by"]) == (a.file, "owner")
    assert job(a.id)["review"] == "anchor"
    # the chain's logo placement uses the render this event names
    assert worker_mod.anchor_source(build_spec(DECORATED)) == ROOT / a.file

    b = env.final(PLAIN, colour=(150, 40, 40))
    assert client.get(f"/api/reviews/{b.id}").json()["actions"]["anchor_exists"] is True
    before = target.read_bytes()
    r = post(client, b.id, "anchor", {"replace": False})
    assert (r.status_code, r.json()) == (409, {"error": EXISTS, "anchor_exists": True})
    assert target.read_bytes() == before and job(b.id)["review"] == "ready"
    assert len(env.events("anchor_approved")) == 1 and len(env.events("approved")) == 1
    r = post(client, b.id, "anchor", {"replace": True})
    assert r.status_code == 200 and target.read_bytes() != before
    assert job(b.id)["review"] == "anchor" and env.events("anchor_approved")[-1]["replaced"] is True
    assert worker_mod.anchor_source(build_spec(DECORATED)) == ROOT / b.file
    refs = {i["id"]: i["reference"] for i in client.get("/api/library").json()["items"]}
    assert refs == {a.id: False, b.id: True}  # the replaced packshot's image is no longer the reference

    teal = env.final({**PLAIN, "colour": {"name": "Teal", "hex": "#1F6F78"}})
    assert post(client, teal.id, "anchor").json()["anchor"] == "pullover-oversized_teal-1f6f78_380_front.png"


def test_anchor_needs_a_note_from_needs_person_and_an_image_in_finals(env, client):
    n = env.final(PLAIN, review="needs_person", reason="The AI visual review did not answer.")
    item = client.get(f"/api/reviews/{n.id}").json()
    assert item["actions"]["anchor"] is True and item["actions"]["note_required"] is True
    r = post(client, n.id, "anchor", {"replace": True})
    assert (r.status_code, r.json()) == (422, {"error": app_mod.NOTE_NEEDED})
    assert job(n.id)["review"] == "needs_person" and not env.anchors.exists() and not env.events("approved")
    r = post(client, n.id, "anchor", {"note": "garment checked by eye"})
    assert r.status_code == 200 and env.events("approved")[-1]["note"] == "garment checked by eye"

    # set aside by the checks (moved to out/reject): never a colour's reference packshot, with a note or not
    gone = env.final(TEAL, review="needs_person", reason="The automatic repairs are used up; please look.")
    with closing(db.connect()) as conn:
        db.set_step(conn, steps(gone.id)[0]["id"], "done", output=env.move(gone.file), result=check(nxt="flag"))
    assert client.get(f"/api/reviews/{gone.id}").json()["actions"]["anchor"] is False
    for body in ({"note": "fine"}, {"note": "fine", "replace": True}):
        r = post(client, gone.id, "anchor", body)
        assert r.status_code == 409 and "set aside" in r.json()["error"], r.text
    assert job(gone.id)["review"] == "needs_person"
    assert sorted(p.name for p in env.anchors.iterdir()) == [ANCHOR]
    assert [e["file"] for e in env.events("anchor_approved")] == [n.file]


def test_anchor_failure_puts_back_the_file_and_the_review(env, client, monkeypatch):
    a = env.final(PLAIN, colour=(90, 90, 60))
    assert post(client, a.id, "anchor").status_code == 200
    target = env.anchors / ANCHOR
    before = target.read_bytes()

    def broken(*args, **kwargs):
        raise OSError("disk full")

    def no_anchor_event(e):
        if e.get("event") == "anchor_approved":
            raise OSError("disk full")
        return env.record(e)
    for where, name, fn in ((library.Image, "open", broken), (finish, "record_verdict", broken),
                            (ledger, "record", no_anchor_event)):
        b = env.final(PLAIN, colour=(150, 40, 40))
        with monkeypatch.context() as m:
            m.setattr(where, name, fn)
            r = post(client, b.id, "anchor", {"replace": True})
        assert (r.status_code, r.json()) == (500, {"error": app_mod.NOT_SAVED}), name
        assert target.read_bytes() == before and job(b.id)["review"] == "ready", name
        assert sorted(p.name for p in env.anchors.iterdir()) == [ANCHOR], name  # no temporary or backup file
        assert [e["file"] for e in env.events("anchor_approved")] == [a.file], name
    assert worker_mod.anchor_source(build_spec(DECORATED)) == ROOT / a.file

    # no reference packshot for this colour before: a failure leaves none
    teal = env.final(TEAL)
    with monkeypatch.context() as m:
        m.setattr(finish, "record_verdict", broken)
        assert post(client, teal.id, "anchor").status_code == 500
    assert sorted(p.name for p in env.anchors.iterdir()) == [ANCHOR] and job(teal.id)["review"] == "ready"
    assert post(client, teal.id, "anchor").status_code == 200  # and the next click works


# --- rules ---------------------------------------------------------------------------------------
def test_rules_activate_validates_text_and_scope(env, client):
    assert client.get("/api/rules").json()["rules"] == []
    j, k, m = env.final(), env.final(), env.final()
    post(client, j.id, "reject", {"note": "the hood looks flat in brand/x.png"})
    env.brain.error = "busy"
    post(client, k.id, "reject", {"note": "too shiny"})
    post(client, m.id, "reject", {"note": "cuffs too loose"})

    r = client.get("/api/rules")
    assert r.status_code == 200
    rules, scopes = r.json()["rules"], r.json()["scopes"]
    assert [s["value"] for s in scopes] == rule_scopes()
    assert {"value": "pullover-oversized", "label": "Oversized Pullover Hoodie"} in scopes
    assert rules[0] == {"index": 0, "status": "pending", "note": "the hood looks flat in x.png",
                        "prompt_text": "Show the hood standing up in a full, rounded shape.",
                        "applies_to": "packshot", "from_shot": j.sid, "date": ledger.now().date().isoformat(),
                        "activated_by": None, "dismissed_by": None}
    assert rules[1]["prompt_text"] == "" and rules[1]["applies_to"] == "all"

    for bad in ({"prompt_text": "", "applies_to": "all"}, {"prompt_text": "   ", "applies_to": "all"},
                {"prompt_text": " ".join(["word"] * 41), "applies_to": "all"},
                {"prompt_text": "Show the cuffs snug.", "applies_to": "everything"},
                {"prompt_text": "Show the cuffs snug.", "applies_to": None}, {"applies_to": "all"}):
        assert client.post("/api/rules/1/activate", json=bad).status_code == 422, bad
    assert json.loads(env.rules.read_text(encoding="utf-8"))["rules"][1]["status"] == "pending"
    for i in (3, -1):
        assert client.post(f"/api/rules/{i}/activate", json={"prompt_text": "x", "applies_to": "all"}).status_code == 404
        assert client.post(f"/api/rules/{i}/dismiss").status_code == 404

    r = client.post("/api/rules/0/activate", json={"prompt_text": "  The hood stands up\n in a full, rounded shape. ",
                                                   "applies_to": "pullover-oversized"})
    assert r.status_code == 200, r.text
    assert r.json()["rule"] | {"date": None} == {
        "index": 0, "status": "active", "note": "the hood looks flat in x.png", "date": None,
        "prompt_text": "The hood stands up in a full, rounded shape.", "applies_to": "pullover-oversized",
        "from_shot": j.sid, "activated_by": "owner", "dismissed_by": None}
    stored = json.loads(env.rules.read_text(encoding="utf-8"))["rules"][0]
    assert (stored["status"], stored["activated_by"], stored["note"]) == ("active", "owner",
                                                                          "the hood looks flat in brand/x.png")
    assert client.post("/api/rules/0/activate", json={"prompt_text": "x", "applies_to": "all"}).status_code == 409
    assert client.post("/api/rules/0/dismiss").status_code == 409

    r = client.post("/api/rules/1/dismiss")
    assert r.status_code == 200 and r.json()["rule"]["status"] == "dismissed"
    assert r.json()["rule"]["dismissed_by"] == "owner"
    assert client.post("/api/rules/1/activate", json={"prompt_text": "x", "applies_to": "all"}).status_code == 409
    r = client.post("/api/rules/2/activate", json={"prompt_text": " ".join(["snug"] * 40), "applies_to": "all"})
    assert r.status_code == 409 and "too long" in r.json()["error"]
    r = client.post("/api/rules/2/activate", json={"prompt_text": "The cuffs sit snug at the wrist.",
                                                   "applies_to": "on_model"})
    assert r.status_code == 200
    assert [x["status"] for x in client.get("/api/rules").json()["rules"]] == ["active", "dismissed", "active"]


def test_rule_activation_keeps_every_prompt_inside_220_words(env, client):
    for note in ("hood flat", "cuffs loose", "colour off"):
        post(client, env.final().id, "reject", {"note": note})

    def words(n):
        return " ".join(["snug"] * n)

    def too_long(n):
        return {"error": f"This rule would make some prompts too long; keep it to {n} words or fewer."}
    room = WORD_RANGE[1] - specs_mod.longest_prompt_words("all")
    assert 0 < room < 40
    r = client.post("/api/rules/0/activate", json={"prompt_text": words(room + 1), "applies_to": "all"})
    assert (r.status_code, r.json()) == (409, too_long(room))
    assert compiler.load_rules()["rules"][0]["status"] == "pending"
    r = client.post("/api/rules/0/activate", json={"prompt_text": words(room), "applies_to": "all"})
    assert r.status_code == 200

    # the next rule gets only what is left in its own scope
    left = WORD_RANGE[1] - specs_mod.longest_prompt_words("packshot")
    assert 0 < left < room
    r = client.post("/api/rules/1/activate", json={"prompt_text": words(left + 1), "applies_to": "packshot"})
    assert (r.status_code, r.json()) == (409, too_long(left))
    r = client.post("/api/rules/1/activate", json={"prompt_text": words(left), "applies_to": "packshot"})
    assert r.status_code == 200
    assert specs_mod.longest_prompt_words("all") <= WORD_RANGE[1]


def test_activated_rule_reaches_compiled_prompts_of_its_scope_only(env, client):
    hood, zipped, cuffs = "The hood stands up in a full, rounded shape.", "The zip pull sits centred at the neck.", \
        "The cuffs sit snug at the wrist."
    for text, scope in ((hood, "packshot"), (zipped, "zip"), (cuffs, "on_model")):
        j = env.final()
        post(client, j.id, "reject", {"note": text})
    rules = client.get("/api/rules").json()["rules"]
    zip_packshot = {**PLAIN, "catalogue": "zip"}
    assert all(r["status"] == "pending" for r in rules)
    for answers in (PLAIN, ON_MODEL, zip_packshot):  # pending rules reach no prompt
        assert not any(t in compile_spec(build_spec(answers)).prompts["paragraph"] for t in (hood, zipped, cuffs))
    for i, (text, scope) in enumerate(((hood, "packshot"), (zipped, "zip"))):
        assert client.post(f"/api/rules/{i}/activate", json={"prompt_text": text, "applies_to": scope}).status_code == 200
    client.post("/api/rules/2/dismiss")
    plain = compile_spec(build_spec(PLAIN)).prompts["paragraph"]
    on_model = compile_spec(build_spec(ON_MODEL)).prompts["paragraph"]
    zip_shot = compile_spec(build_spec(zip_packshot)).prompts["paragraph"]
    assert hood in plain and zipped not in plain
    assert hood not in on_model and zipped not in on_model and cuffs not in on_model
    assert hood in zip_shot and zipped in zip_shot


def test_plugin_skill_never_switches_a_rule_on():
    """I3: only a person's click in the studio switches a rule on (activated_by, the 220-word check). The plugin's
    skill drafts the rule's words and scope, leaves it pending and sends the user to the Rules panel."""
    skill = (ROOT / "plugins" / "csc-studio" / "skills" / "finish-and-export" / "SKILL.md").read_text(encoding="utf-8")
    for leak in ("status: active", '"status": "active"', "`active`", "set_status", "activate-rule"):
        assert leak not in skill, leak
    assert "`pending`" in skill and "`prompt_text`" in skill and "`applies_to`" in skill
    assert "Rules from rejection notes" in skill and "Switch on" in skill and "Review" in skill
    assert "Never set or change its `status`" in skill and "stays `pending`" in skill


# --- library -------------------------------------------------------------------------------------
def test_library_lists_only_approved_with_record(env, client):
    a = env.final(PLAIN)
    post(client, a.id, "approve")
    b = env.final(ON_MODEL)
    env.step(b.id, "finish", b.file, b.file, cost=0.0)
    env.step(b.id, "check", b.file, b.file, result=check(), cost=0.012)
    post(client, b.id, "approve", {"note": ""})
    env.final(PLAIN)                                     # ready, never approved
    d = env.final(PLAIN)
    post(client, d.id, "reject", {"note": "too dark"})
    e = env.final(PLAIN)                                 # approved with the CLI: the ledger event counts
    env.record({"event": "approved", "file": e.file, "shot_id": e.sid, "by": "user", "note": ""})
    f = env.final(PLAIN)                                 # its first render was approved, but a newer one followed
    env.record({"event": "approved", "file": f.file, "shot_id": f.sid, "by": "user", "note": ""})
    newer = env.image("finals", f"{f.sid}_repair1")
    env.record({"shot_id": f.sid, "phase": "repair", "status": "ok", "file": newer, "root": f.file, "cost_usd": 0.1,
                "provider": "gemini", "tier": "standard"})
    env.step(f.id, "repair", f.file, newer, code="hood_shape", cost=0.1)
    undone = env.final(PLAIN)                            # approved with the CLI, then rejected
    env.record({"event": "approved", "file": undone.file, "shot_id": undone.sid, "by": "user", "note": ""})
    env.record({"event": "rejected", "file": undone.file, "shot_id": undone.sid, "by": "user", "note": "no"})
    g = env.final(PLAIN, user="teammate")               # one shared library
    post(client, g.id, "anchor")
    teal = env.final({**PLAIN, "colour": {"name": "Teal", "hex": "#1F6F78"}, "purpose": "client"})
    post(client, teal.id, "approve")

    r = client.get("/api/library")
    assert r.status_code == 200, r.text
    items = {i["id"]: i for i in r.json()["items"]}
    assert set(items) == {a.id, b.id, e.id, g.id, teal.id}
    item = items[b.id]
    assert item["shot_id"] == b.sid and item["image"] == b.file and item["caption"] == CAPTIONS["on_model"]
    assert item["words"].startswith("On model, front") and "Maya" in item["words"]
    assert (item["kind"], item["style"], item["colour"], item["model"]) == ("on_model", "pullover-oversized", "olive",
                                                                           "Maya")
    assert item["technique"] is None and item["placement"] is None and item["ai_model"] == "gemini-3.1-flash-image"
    assert item["cost_usd"] == round(0.101 + 0.01 + 0.0 + 0.012, 4)
    assert item["approved_by"] == "owner" and item["approved_at"].startswith(ledger.now().date().isoformat())
    assert STYLE_BLOCK in item["prompt"] and item["reference"] is False and item["export"] is None
    assert items[g.id]["reference"] is True and items[e.id]["approved_by"] == "user"
    # what it is for: the page offers the approval PDF for a client mockup only
    assert item["purpose"] == "website" and items[teal.id]["purpose"] == "client"
    for leak in ("brand/", "out/specs", "refs/", "specs/"):
        assert leak not in r.text, leak

    def ids(query):
        return {i["id"] for i in client.get(f"/api/library?{query}").json()["items"]}
    assert ids("kind=on_model") == ids("model=Maya") == {b.id}
    assert ids("kind=packshot") == {a.id, e.id, g.id, teal.id}
    assert ids("colour=olive&style=pullover-oversized") == set(items) - {teal.id}
    assert ids("colour=teal-1f6f78") == {teal.id}
    assert ids("style=zip") == set() and ids("technique=screen%20print") == set()
    facets = r.json()["facets"]
    assert facets["kind"] == [{"value": "on_model", "label": "On model"}, {"value": "packshot", "label": "Packshot"}]
    assert facets["model"] == [{"value": "Maya", "label": "Maya"}]
    assert facets["style"] == [{"value": "pullover-oversized", "label": "Oversized Pullover Hoodie"}]
    assert facets["colour"] == [{"value": "olive", "label": "Olive"}, {"value": "teal-1f6f78", "label": "Teal"}]


def test_export_requires_approval_and_returns_files(env, client):
    j = env.final(PLAIN, size=(820, 1025))
    r = client.post(f"/api/library/{j.id}/export", json={"pdf": False})
    assert r.status_code == 409 and "approve" in r.json()["error"].lower()
    assert not (ROOT / "out" / "export" / j.sid).exists()
    assert client.post("/api/library/9999/export", json={}).status_code == 404
    gone = env.final(PLAIN)
    post(client, gone.id, "approve")
    (ROOT / gone.file).unlink()
    r = client.post(f"/api/library/{gone.id}/export", json={})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)

    post(client, j.id, "approve")
    r = client.post(f"/api/library/{j.id}/export", json={"pdf": False})
    assert r.status_code == 200, r.text
    body = r.json()
    base = f"out/export/{j.sid}/{j.sid}"
    assert body["files"] == [{"path": f"{base}-800.webp", "width": 800}, {"path": f"{base}-400.webp", "width": 400}]
    assert body["pdf"] is None and body["caption"] == CAPTIONS["concept_render"]
    assert body["alt"] == finish.alt_text(j.spec)
    for f in body["files"]:
        got = client.get(f"/api/files/{f['path']}")
        assert got.status_code == 200 and got.headers["content-type"] == "image/webp"

    body = client.post(f"/api/library/{j.id}/export", json={"pdf": True}).json()
    assert body["pdf"] == f"{base}-approval.pdf"
    got = client.get(f"/api/files/{body['pdf']}")
    assert got.status_code == 200 and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("attachment")
    assert client.get(f"/api/files/{base}.json").json() == NOT_FOUND  # the record names repo paths
    (item,) = [i for i in client.get("/api/library").json()["items"] if i["id"] == j.id]
    assert item["export"] == {"files": body["files"], "pdf": body["pdf"], "alt": body["alt"],
                              "caption": body["caption"]}


# --- the logo gate: a design the model rendered is approved or exported only once its logo passed ---------------
LOGO_REFUSED = {"error": "The logo on this image did not pass its check. Ask for a change, or reject it."}


def logo_result(codes=(), found=True):
    """A check whose logo verification found the centre-chest design with these codes (none: it passed)."""
    logo = {"result": "fail" if codes or not found else "pass",
            "decorations": [{"placement": "centre chest", "found": found, "codes": list(codes)}]}
    return {**check(list(codes), "flag" if codes else "approve"), "logo": logo}


def test_failed_logo_cannot_be_approved(env, client):
    """CLAUDE.md: a logo is never published unverified. A person's note does not clear a logo that failed its check
    (or was never checked), on either approve route."""
    failed = env.final(DECORATED, review="needs_person", result=logo_result(["logo_text"]),
                       reason="No approved reference packshot to place the real logo on.")
    never = env.final(DECORATED, review="needs_person", result={**check(), "logo": None},
                      reason="The checks stopped at the logo check because the budget is reached.")
    lost = env.final(DECORATED, review="needs_person", result=logo_result(["logo_shape"], found=False))
    for case in (failed, never, lost):
        item = client.get(f"/api/reviews/{case.id}").json()
        assert item["actions"]["approve"] is False and item["actions"]["anchor"] is False
        assert item["actions"]["reject"] is True and item["logo_note"] == LOGO_REFUSED["error"]
        for verb, body in (("approve", {"note": "the logo looks fine to me"}), ("approve", {}),
                           ("anchor", {"note": "fine", "replace": True})):
            r = post(client, case.id, verb, body)
            assert r.status_code == 409, (verb, r.text)
            if verb == "approve":
                assert r.json() == LOGO_REFUSED
        assert job(case.id)["review"] == "needs_person"
    assert not env.events("approved")

    # the person can still ask for the logo to be fixed, or reject it
    assert post(client, failed.id, "change", {"code": "logo_text"}).status_code == 200
    assert post(client, never.id, "reject", {"note": "logo misspelled"}).status_code == 200


def test_failed_logo_cannot_be_exported(env, client):
    """An approval recorded outside the studio (the CLI) does not let an unverified logo out either."""
    j = env.final(DECORATED, review="needs_person", result=logo_result(["logo_size"]), size=(820, 1025))
    env.record({"event": "approved", "file": j.file, "shot_id": j.sid, "by": "user", "note": "cli"})
    for pdf in (False, True):
        r = client.post(f"/api/library/{j.id}/export", json={"pdf": pdf})
        assert (r.status_code, r.json()) == (409, LOGO_REFUSED)
    assert not (ROOT / "out" / "export" / j.sid).exists() and not env.events("exported")


def test_verified_or_composited_logo_can_be_approved(env, client):
    ok = env.final(DECORATED, result=logo_result())  # the logo check passed: ready
    assert client.get(f"/api/reviews/{ok.id}").json()["logo_note"] is None
    assert post(client, ok.id, "approve").status_code == 200

    # the real artwork placed on the reference packshot, then checked again and passed
    comp = env.final(DECORATED, review="needs_person", result=logo_result(["logo_text"]))
    placed = env.image("finals", f"{comp.sid}_logo")
    env.record({"shot_id": comp.sid, "provider": "local", "phase": "finish", "op": "composite",
                "status": "ok", "file": placed, "source": comp.file, "root": placed, "cost_usd": 0.0})
    env.step(comp.id, "composite", comp.file, placed)
    env.step(comp.id, "check", placed, placed, result=logo_result())
    with closing(db.connect()) as conn:
        db.set_review(conn, comp.id, "ready")
    item = client.get(f"/api/reviews/{comp.id}").json()
    assert item["latest"] == placed and item["actions"]["approve"] is True and item["logo_note"] is None
    assert post(client, comp.id, "approve").status_code == 200
    assert env.events("approved")[-1]["file"] == placed


def test_logo_gate_leaves_undecorated_shots_alone(env, client):
    """No design the model rendered: no logo to clear, whatever the logo check says (it never ran)."""
    j = env.final(PLAIN, review="needs_person", result={**check(), "logo": None}, size=(820, 1025),
                  reason="The face could not be checked against a reference photo; please look.")
    item = client.get(f"/api/reviews/{j.id}").json()
    assert item["actions"]["approve"] is True and item["logo_note"] is None
    assert post(client, j.id, "approve", {"note": "garment checked by eye"}).status_code == 200
    r = client.post(f"/api/library/{j.id}/export", json={"pdf": False})
    assert r.status_code == 200 and len(r.json()["files"]) == 2


def test_reject_folder_and_export_served_but_contained(env, client):
    rej_jpg, rej_png = env.image("reject"), env.image("reject", suffix=".png")
    for path, kind in ((rej_jpg, "image/jpeg"), (rej_png, "image/png")):
        got = client.get(f"/api/files/{path}")
        assert got.status_code == 200 and got.headers["content-type"] == kind
    sub = f"zz-test-{uuid.uuid4().hex[:8]}"
    env.sids.append(sub)  # its out/export folder is removed afterwards
    folder = f"export/{sub}"
    for suffix, kind in ((".webp", "image/webp"), (".png", "image/png"), (".jpg", "image/jpeg")):
        got = client.get(f"/api/files/{env.image(folder, suffix=suffix)}")
        assert got.status_code == 200 and got.headers["content-type"] == kind
        assert "content-disposition" not in got.headers
    pdf = ROOT / "out" / folder / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    got = client.get(f"/api/files/out/{folder}/x.pdf")
    assert got.status_code == 200 and got.headers["content-disposition"].startswith("attachment")

    def text_file(folder: str, suffix: str) -> str:
        path = ROOT / "out" / folder / f"zz-test-{uuid.uuid4().hex[:8]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")
        env.made.append(path)
        return path.relative_to(ROOT).as_posix()
    sheet, mask = env.image("sheets"), env.image("cache", suffix=".png")
    sheet_name = sheet.rsplit("/", 1)[1]
    refused = [
        text_file("reject", ".txt"), text_file(folder, ".json"), env.image(folder, suffix=".jpeg"),
        text_file("reject", ".pdf"),                     # a download only from out/export
        sheet, mask, text_file("specs", ".json"),
        f"out/export/%2E%2E/sheets/{sheet_name}", f"out/reject/%2E%2E/sheets/{sheet_name}",
        f"out/export/{sub}/%2E%2E/%2E%2E/sheets/{sheet_name}", f"out/exportx/{sub}/x.webp",
        "out/reject", f"out/export/{sub}",
    ]
    for p in refused:
        got = client.get(f"/api/files/{p}")
        assert (got.status_code, got.json()) == (404, NOT_FOUND), p
    remote = TestClient(app_mod.app, base_url="http://127.0.0.1", client=("10.0.0.5", 50000))
    assert remote.get(f"/api/files/{rej_jpg}").status_code == 403


def test_ledger_snapshot_reads_the_log_once(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "LOG", tmp_path / "log.jsonl")
    ledger.record({"n": 1})
    with ledger.snapshot():
        first = ledger.entries()
        with (tmp_path / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"n": 2}) + "\n")
        assert ledger.entries() is first and [e["n"] for e in first] == [1]
    assert [e["n"] for e in ledger.entries()] == [1, 2]
