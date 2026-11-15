import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pipeline import generate, ledger, qa
from pipeline.compile import ROOT
from pipeline.specs import build_spec
from studio import app as app_mod
from studio import db
from studio.worker import Worker

from test_app import (OAKMERE, client, get_job, isolated, job_count, make_job,  # noqa: F401  (fixtures)
                      spend)

OLIVE_ON_MODEL = {"purpose": "website", "catalogue": "pullover-oversized", "colour": "olive", "gsm": 450,
                  "recipe": "on-model-front", "decoration": "none", "cast": "Maya", "scene": "warm-studio"}
ANCHOR_FIRST = ("This colour has no approved reference packshot yet. "
                "Make it first, then this shot can use it.")
PICK_A_SKETCH = {"error": "pick a sketch from this shot"}
NOT_FOUND = {"error": "image not found"}
PNG = b"\x89PNG\r\n\x1a\n-test-image"


def test_spec_path_prefers_specs_then_out_specs():
    handwritten = next(Path(qa.ROOT, "specs").glob("*.json")).stem
    assert qa.spec_path(handwritten) == qa.ROOT / "specs" / f"{handwritten}.json"
    sid = "zz-test-dashboard-spec"
    target = qa.ROOT / "out" / "specs" / f"{sid}.json"
    assert not target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        assert qa.spec_path(sid) == target
    finally:
        target.unlink(missing_ok=True)


def test_spec_path_error_names_both_places():
    with pytest.raises(FileNotFoundError) as e:
        qa.spec_path("zz-no-such-shot")
    assert "specs" in str(e.value) and "out" in str(e.value)


def test_worker_writes_spec_for_final(isolated, tmp_path):
    spec = build_spec(OAKMERE)
    target = tmp_path / "specs" / f"{spec['shot']['shot_id']}.json"
    seen = {}

    def runner(c, r):
        seen["exists"] = target.exists()
        return Path("out/finals/fake.jpg"), SimpleNamespace(cost_usd=0.1)

    make_job("final")
    assert Worker(db.connect, runner=runner).run_once() is True
    assert seen["exists"] is True
    assert json.loads(target.read_text(encoding="utf-8")) == spec
    assert target.read_text(encoding="utf-8").startswith('{\n  "')  # pretty JSON


def test_worker_writes_no_spec_for_drafts(isolated, tmp_path):
    make_job("drafts")
    Worker(db.connect, runner=lambda c, r: (Path("out/drafts/d.png"), SimpleNamespace(cost_usd=0.0))).run_once()
    assert not (tmp_path / "specs").exists()


# --- Task 2: server additions for the builder ------------------------------------------------
@pytest.fixture
def out_file():
    """Real files under out/ (git-ignored) with unique names, removed afterwards. Returns repo-relative posix."""
    made: list[Path] = []

    def make(folder: str = "drafts", suffix: str = ".png", data: bytes = PNG) -> str:
        path = ROOT / "out" / folder / f"zz-test-{uuid.uuid4().hex[:8]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        made.append(path)
        return path.relative_to(ROOT).as_posix()
    yield make
    for path in made:
        path.unlink(missing_ok=True)


def done_drafts(answers: dict, files: list[str], user: str = "owner", status: str = "done",
                kind: str = "drafts") -> int:
    with closing(db.connect()) as conn:
        jid = db.create_job(conn, user, kind, answers, build_spec(answers), {"base_usd": 0.0})
        db.set_status(conn, jid, status, result={"files": files, "cost_usd": 0.0})
    return jid


def remote_client() -> TestClient:
    return TestClient(app_mod.app, base_url="http://127.0.0.1", client=("10.0.0.5", 50000))


def test_health_has_budget_figures(client, monkeypatch):
    spend(monkeypatch, gemini_usd=2.5, neurons=1200)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "budget": ledger.status(), "gemini_usd": 2.5,
                        "cap_usd": ledger.GEMINI_MONTHLY_CAP_USD, "cf_neurons": 1200,
                        "cf_cap": ledger.CF_DAILY_NEURON_CAP}


def test_layout_column_added_to_old_database(tmp_path):
    old = tmp_path / "old.db"
    with closing(sqlite3.connect(old)) as conn, conn:  # a plan-1 database: no layout column
        conn.execute(db.SCHEMA)
        conn.execute("INSERT INTO jobs (user, kind, status, answers, spec, estimate, created, updated)"
                     " VALUES ('owner', 'final', 'done', '{}', '{}', '{}', 't', 't')")
    for _ in range(2):  # adding it is idempotent
        with closing(db.connect(old)) as conn:
            assert "layout" in {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
            assert db.get_job(conn, 1)["layout"] is None
    with closing(db.connect(old)) as conn:
        jid = db.create_job(conn, "owner", "final", {}, {}, {}, layout="out/drafts/a.png")
        assert db.get_job(conn, jid)["layout"] == "out/drafts/a.png"


def test_job_list_newest_first_and_per_user(client):
    first = make_job("final")
    with closing(db.connect()) as conn:
        theirs = db.create_job(conn, "teammate", "final", OAKMERE, build_spec(OAKMERE), {})
    running, newest = make_job("drafts"), make_job("final")
    with closing(db.connect()) as conn:
        db.set_status(conn, running, "running")
    r = client.get("/api/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert [j["id"] for j in jobs] == [newest, running, first]  # the teammate's job is not listed
    assert jobs[1]["status"] == "running"  # a reloaded page finds the running job and polls it again
    assert jobs == [client.get(f"/api/jobs/{j}").json() for j in (newest, running, first)]
    assert [j["id"] for j in client.get("/api/jobs?limit=2").json()["jobs"]] == [newest, running]
    assert [j["id"] for j in client.get("/api/jobs?limit=0").json()["jobs"]] == [newest]  # at least one
    with closing(db.connect()) as conn:
        assert [j["id"] for j in db.list_jobs(conn, "teammate")] == [theirs]
        assert [j["id"] for j in db.list_jobs(conn, "owner", limit=1)] == [newest]


def test_job_list_has_no_secrets(client, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", "tok-SECRET")
    assert client.post("/api/jobs", json={"answers": OAKMERE}).status_code == 201

    def leaky(c, r):
        raise RuntimeError(f"auth failed with {os.environ['STUDIO_OWNER_CLAUDE_OAUTH_TOKEN']}")
    assert Worker(db.connect, runner=leaky).run_once() is True
    body = client.get("/api/jobs").text
    assert "failed" in body and "tok-SECRET" not in body


def test_files_serves_draft_image(client, out_file):
    draft = out_file()
    r = client.get(f"/api/files/{draft}")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    for folder, suffix in (("finals", ".jpg"), ("finals", ".jpeg"), ("drafts", ".webp")):
        assert client.get(f"/api/files/{out_file(folder, suffix)}").status_code == 200
    assert remote_client().get(f"/api/files/{draft}").status_code == 403


def test_files_refuses_traversal_and_other_dirs(client, out_file):
    draft = out_file()  # a real, servable image: every spelling below that reaches it is still refused
    name = draft.rsplit("/", 1)[1]
    spec = out_file("specs", ".json", b"{}")
    handwritten = next((ROOT / "specs").glob("*.json")).relative_to(ROOT).as_posix()
    paths = [
        ".env",
        "out/drafts/%2E%2E/%2E%2E/.env",           # encoded traversal (httpx would collapse a plain ..)
        "out/%2e%2e/.env",
        f"out/drafts/%2E%2E/drafts/{name}",        # .. even when it lands back inside
        f"out%5Cdrafts%5C{name}",                  # backslashes
        f"{ROOT.as_posix()}/{draft}",              # absolute path to the real file
        f"/{draft}",
        f"{draft}%00.png",                         # NUL byte
        f"{draft}:hidden.png",                     # Windows alternate data stream
        "out/log.jsonl",
        spec,                                      # out/specs/*.json
        handwritten,                               # specs/*.json
        "refs/anchors/pullover-oversized_washed-black_450_front.png",  # an image, wrong folder
        out_file("drafts", ".txt", b"x"),          # right folder, wrong type
        "out/drafts/zz-test-missing.png",          # right folder, no such file
        "out/drafts",
        "out/draftsx/a.png",
        "",
    ]
    for p in paths:
        r = client.get(f"/api/files/{p}")
        assert (r.status_code, r.json()) == (404, NOT_FOUND), p  # one answer: never says what exists


def test_final_needs_anchor_409_with_anchor_answers(client):
    for colour in ("olive", {"name": "Teal", "hex": "#1F6F78"}):
        answers = {**OLIVE_ON_MODEL, "colour": colour}
        r = client.post("/api/jobs", json={"answers": answers, "kind": "final", "confirm": True})
        assert r.status_code == 409
        assert r.json() == {"error": ANCHOR_FIRST, "needs_anchor": True, "anchor_answers": {
            "purpose": "website", "catalogue": "pullover-oversized", "colour": colour, "gsm": 450,
            "recipe": "packshot-front", "decoration": "none"}}
    assert job_count() == 0
    # sketches are free and never published: allowed without an anchor
    assert client.post("/api/jobs", json={"answers": OLIVE_ON_MODEL, "kind": "drafts"}).status_code == 201


def test_packshot_front_final_not_gated(client):
    anchor = client.post("/api/jobs", json={"answers": OLIVE_ON_MODEL}).json()["anchor_answers"]
    r = client.post("/api/jobs", json={"answers": anchor, "kind": "final"})
    assert r.status_code == 201, r.text
    assert get_job(r.json()["id"])["answers"] == anchor
    # an on-model final whose colour already has an approved anchor is not gated either
    on_model = {**OAKMERE, "recipe": "on-model-front", "cast": "Maya", "scene": "warm-studio"}
    assert client.post("/api/jobs", json={"answers": on_model}).status_code == 201


def test_layout_must_be_a_draft_of_this_shot(client, monkeypatch):
    done_drafts(OAKMERE, ["out/drafts/a1.png", "out/drafts/a2.png"])
    done_drafts({**OAKMERE, "size_cm": 20}, ["out/drafts/b1.png"])        # another shot
    done_drafts(OAKMERE, ["out/drafts/c1.png"], user="teammate")           # another user
    done_drafts(OAKMERE, ["out/drafts/d1.png"], status="failed")           # not done
    done_drafts(OAKMERE, ["out/finals/f1.jpg"], kind="final")              # a final, not a sketch
    before = job_count()
    opened = []
    real_compile = app_mod.compile_spec
    monkeypatch.setattr(app_mod, "compile_spec", lambda spec, refs=(): opened.append(list(refs)) or
                        real_compile(spec, refs))
    for bad in ("out/drafts/b1.png", "out/drafts/c1.png", "out/drafts/d1.png", "out/finals/f1.jpg",
                "out/drafts/zz-none.png", "out/drafts/../drafts/a1.png", "out\\drafts\\a1.png",
                "/out/drafts/a1.png", "../out/drafts/a1.png", "", "../../.env"):
        r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "layout": bad})
        assert (r.status_code, r.json()) == (422, PICK_A_SKETCH), bad
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "drafts", "layout": "out/drafts/a1.png"})
    assert (r.status_code, r.json()) == (422, PICK_A_SKETCH)  # only a final takes a sketch
    assert job_count() == before and opened == []  # nothing compiled with a path the client sent
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "layout": "out//drafts/./a2.png"})
    assert r.status_code == 201, r.text
    assert get_job(r.json()["id"])["layout"] == "out/drafts/a2.png"  # the stored sketch path, not the client's


def test_final_job_passes_layout_to_compile(client, out_file):
    sketch = out_file()
    done_drafts(OAKMERE, [sketch])
    seen = []

    def runner(c, route):
        seen.append([r["path"] for r in c.refs])
        return Path("out/finals/fake.jpg"), SimpleNamespace(cost_usd=0.1)
    with_layout = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "layout": sketch})
    without = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final"})
    assert with_layout.status_code == 201 and without.status_code == 201
    w = Worker(db.connect, runner=runner)
    assert w.run_once() is True and w.run_once() is True
    with_refs, without_refs = seen
    assert with_refs[0] == without_refs[0] == "refs/anchors/pullover-oversized_washed-black_450_front.png"
    assert sketch in with_refs and [p for p in with_refs if p != sketch] == without_refs  # only the sketch added
    assert get_job(with_layout.json()["id"])["status"] == "done"


def test_index_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>CSC Studio</title>" in r.text
    assert client.get("/static/index.html").text == r.text
    assert client.get("/static/%2E%2E/app.py").status_code == 404
    for url in ("/", "/static/index.html"):
        assert remote_client().get(url).status_code == 403  # same identity rule as the API
        assert client.get(url, headers={"host": "rebind.example"}).status_code == 400  # same host rule


def test_over_one_dollar_still_needs_confirm_with_layout(client, monkeypatch):
    done_drafts(OAKMERE, ["out/drafts/a1.png"])
    est = {"base_usd": 1.5, "worst_usd": 13.5, "action": "run", "reason": "", "tier": "premium", "model": "m"}
    seen = []
    monkeypatch.setattr(app_mod, "estimate",
                        lambda spec, kind="final", layout_refs=(): seen.append(list(layout_refs)) or dict(est))
    body = {"answers": OAKMERE, "kind": "final", "layout": "out/drafts/a1.png"}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 409 and "confirm" in r.json()["error"]
    assert job_count() == 1  # only the sketches
    r = client.post("/api/jobs", json={**body, "confirm": True})
    assert r.status_code == 201
    job = get_job(r.json()["id"])
    assert job["confirmed_by"] == "owner" and job["layout"] == "out/drafts/a1.png"
    assert seen == [["out/drafts/a1.png"], ["out/drafts/a1.png"]]  # the sketch is costed as a reference image


def test_cap_still_refuses_with_layout(client, monkeypatch):
    done_drafts(OAKMERE, ["out/drafts/a1.png"])
    spend(monkeypatch, gemini_usd=8.95)
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "layout": "out/drafts/a1.png",
                                       "confirm": True})
    assert r.status_code == 409 and "monthly cap" in r.json()["error"] and job_count() == 1
