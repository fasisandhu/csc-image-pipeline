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


@pytest.fixture
def pinned_anchors(monkeypatch):
    """Only the washed-black reference packshot exists, whatever refs/anchors holds on this PC."""
    fake = lambda cat, colour, gsm: "refs/anchors/x.png" if colour == "washed-black" else None  # noqa: E731
    monkeypatch.setattr(app_mod, "find_anchor", fake)
    monkeypatch.setattr("pipeline.specs.find_anchor", fake)


def test_final_needs_anchor_409_with_anchor_answers(client, pinned_anchors):
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


def test_packshot_front_final_not_gated(client, pinned_anchors):
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


NO_SKETCH = {"error": "Reference packshots are made without a sketch, so the garment comes only from our catalogue."}
PLAIN_PACKSHOT = {"purpose": "website", "catalogue": "pullover-oversized", "colour": "washed-black", "gsm": 450,
                  "recipe": "packshot-front", "decoration": "none"}


def test_reference_packshot_final_takes_no_sketch(client):
    """A plain front packshot may become its colour's reference packshot, which later shots copy as "the exact
    garment": its garment comes only from the catalogue, never from a sketch (final review I1c)."""
    done_drafts(PLAIN_PACKSHOT, ["out/drafts/p1.png"])  # a real sketch of this very shot
    before = job_count()
    for layout in ("out/drafts/p1.png", "out/drafts/zz-none.png", ""):
        r = client.post("/api/jobs", json={"answers": PLAIN_PACKSHOT, "kind": "final", "layout": layout})
        assert (r.status_code, r.json()) == (422, NO_SKETCH), layout
    assert job_count() == before
    r = client.post("/api/jobs", json={"answers": PLAIN_PACKSHOT, "kind": "final", "layout": None})
    assert r.status_code == 201 and get_job(r.json()["id"])["layout"] is None
    # sketches of it are still free to make (for looking), and a decorated packshot still builds on one
    assert client.post("/api/jobs", json={"answers": PLAIN_PACKSHOT, "kind": "drafts"}).status_code == 201
    done_drafts(OAKMERE, ["out/drafts/o1.png"])
    assert client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final",
                                          "layout": "out/drafts/o1.png"}).status_code == 201


def test_preview_says_whether_a_final_takes_a_sketch(client):
    assert client.post("/api/preview", json={"answers": PLAIN_PACKSHOT}).json()["takes_sketch"] is False
    assert client.post("/api/preview", json={"answers": OAKMERE}).json()["takes_sketch"] is True
    assert client.post("/api/preview", json={"answers": OLIVE_ON_MODEL}).json()["takes_sketch"] is True


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


# --- fix round 1: no repo paths outside out/drafts and out/finals reach the browser ------------
def test_job_dicts_carry_shot_id_not_repo_paths(client):
    shot_id = client.post("/api/preview", json={"answers": OAKMERE}).json()["shot_id"]
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final"})
    assert r.status_code == 201
    jid = r.json()["id"]
    spec = get_job(jid)["spec"]  # the database keeps the full spec for the worker
    assert spec["set"]["reference_images"][0]["path"].startswith("refs/anchors/")
    assert spec["shot"]["decoration"][0]["artwork"].startswith("brand/")
    one, listed = client.get(f"/api/jobs/{jid}"), client.get("/api/jobs")
    for body in (one.text, listed.text):
        assert "refs/" not in body and "brand/" not in body
    assert "spec" not in one.json() and one.json()["shot_id"] == shot_id
    assert listed.json()["jobs"] == [one.json()]


def test_job_error_hides_setting_names(client):
    jid = client.post("/api/jobs", json={"answers": OAKMERE}).json()["id"]

    def no_key(c, r):
        raise RuntimeError("gemini: GEMINI_API_KEY not set")
    assert Worker(db.connect, runner=no_key).run_once() is True
    for body in (client.get(f"/api/jobs/{jid}").json(), client.get("/api/jobs").json()["jobs"][0]):
        assert body["error"] == "gemini: a setting not set"
    with closing(db.connect()) as conn:
        assert "GEMINI_API_KEY" in db.get_job(conn, jid)["error"]  # the DB keeps the original


def test_public_text():
    assert app_mod.public_text("STUDIO_TOKEN and CF_API_TOKEN, MY_SECRET") == "a setting and a setting, a setting"
    assert app_mod.public_text("GSM 450 and HTML stay") == "GSM 450 and HTML stay"
    assert app_mod.public_text("CLOUDFLARE_ACCOUNT_ID missing, bad ACCOUNT_ID") == "a setting missing, bad a setting"
    assert app_mod.public_text("see brand/x.png") == "see x.png"


def test_job_error_names_files_without_folders(client):
    jid = client.post("/api/jobs", json={"answers": OAKMERE}).json()["id"]

    def missing_ref(c, r):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{ROOT / 'refs' / 'anchors' / 'a.png'}'")
    assert Worker(db.connect, runner=missing_ref).run_once() is True
    for body in (client.get(f"/api/jobs/{jid}").json(), client.get("/api/jobs").json()["jobs"][0]):
        assert body["error"] == "[Errno 2] No such file or directory: 'a.png'"


def test_preview_warnings_name_files_without_folders(client, monkeypatch):
    real = app_mod.compile_spec

    def with_warnings(spec, refs=()):
        c = real(spec, refs)
        c.warnings += ["reference brand/designs/x_ref.png not found (not yet approved?), skipped",
                       "set style_block differs from brand/brand.json"]
        return c
    monkeypatch.setattr(app_mod, "compile_spec", with_warnings)
    r = client.post("/api/preview", json={"answers": OAKMERE})
    assert r.status_code == 200
    warnings = r.json()["warnings"]
    assert "reference x_ref.png not found (not yet approved?), skipped" in warnings
    assert not any("style_block" in w for w in warnings)  # not meant for the page
    assert len(warnings) == len(set(warnings)) and None not in warnings
    assert "brand/" not in r.text and "refs/" not in r.text
    assert not any(w.startswith("catalogue ") for w in warnings)  # plain sentence instead


# --- page shell (task 4) ---------------------------------------------------------------------
STATIC = Path(app_mod.STATIC_DIR)


def test_static_app_js_served_as_javascript(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert client.get("/static/styles.css").status_code == 200


def test_vendor_versions_are_exact_pins_with_files(client):
    import re
    lines = [ln for ln in (STATIC / "vendor" / "VERSIONS.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines and all(re.fullmatch(r"[a-z0-9.-]+==\d+\.\d+\.\d+", ln) for ln in lines)
    for ln in lines:
        name, version = ln.split("==")
        assert (STATIC / "vendor" / f"{name}-{version}.min.js").is_file()
        assert client.get(f"/static/vendor/{name}-{version}.min.js").status_code == 200


def test_index_loads_only_local_assets(client):
    import re
    html = client.get("/").text
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs
    assert all(r.startswith(("/static/", "#")) for r in refs), refs
    assert "http" not in html.lower().replace("http-equiv", "")
    assert not re.search(r"\b(alert|confirm|prompt)\(", (STATIC / "app.js").read_text(encoding="utf-8"))


# --- builder sections (task 5) ---------------------------------------------------------------
def test_every_section_has_a_tip(client):
    for answers in ({}, {"catalogue": "pullover-oversized", "colour": "olive", "gsm": 380,
                         "recipe": "on-model-back", "decoration": "design"}):
        r = client.post("/api/questions", json={"answers": answers})
        assert r.status_code == 200
        sections = r.json()["sections"]
        assert sections and all(isinstance(s.get("tip"), str) and s["tip"].strip() for s in sections), \
            [s["id"] for s in sections if not (s.get("tip") or "").strip()]
    every = [s["id"] for s in json.loads((ROOT / "studio" / "questions" / "images.json").read_text(
        encoding="utf-8"))["sections"]]
    assert [s["id"] for s in sections] == every  # every section in the file, visible or not, carries one


# --- fill from my words and the live summary (task 6) ----------------------------------------
def test_brief_reports_provider_and_fallback(client, monkeypatch):
    from studio.brain import BrainResult

    class StandIn:  # what FallbackBrain returns when Gemini answers for Claude
        def ask_json(self, task, prompt, schema, images=(), user="owner", **kw):
            return BrainResult(data={"colour": "olive"}, provider="gemini", model="m",
                               credential="subscription", seconds=0.1, cost_usd=0.001, fallback=True)
    monkeypatch.setattr(app_mod, "get_brain", lambda user, fast=False: StandIn())
    r = client.post("/api/brief", json={"text": "olive hoodie", "answers": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["provider"], body["fallback"]) == ("gemini", True)  # the page says "Gemini (backup)"
    assert "credential" not in body and "subscription" not in r.text  # never the credential type


def test_preview_reports_route_tier(client):
    """The summary's tier note comes from the router, never guessed by the page."""
    plain = {**OAKMERE, "decoration": "none"}
    for k in ("design", "technique", "placement", "size_cm"):
        plain.pop(k)
    assert client.post("/api/preview", json={"answers": OAKMERE}).json()["tier"] == "premium"
    assert client.post("/api/preview", json={"answers": plain}).json()["tier"] == "standard"


# --- sketches, approve & create, job panel (task 7) ------------------------------------------
def test_job_dicts_carry_caption_from_brand(client):
    """The page shows the job's caption under every image; it comes from brand.json, never from the page."""
    captions = json.loads((ROOT / "brand" / "brand.json").read_text(encoding="utf-8"))["captions"]
    assert captions["on_model"] == "AI model, concept render"
    packshot = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final"}).json()["id"]
    on_model = client.post("/api/jobs", json={"answers": OLIVE_ON_MODEL, "kind": "drafts"}).json()["id"]
    want = {packshot: captions["concept_render"], on_model: captions["on_model"]}
    for jid, caption in want.items():
        assert client.get(f"/api/jobs/{jid}").json()["caption"] == caption
    assert {j["id"]: j["caption"] for j in client.get("/api/jobs").json()["jobs"]} == want


def test_over_one_dollar_409_says_needs_confirm(client, monkeypatch):
    est = {"base_usd": 1.5, "worst_usd": 13.5, "action": "run", "reason": "", "tier": "premium", "model": "m"}
    monkeypatch.setattr(app_mod, "estimate", lambda spec, kind="final", layout_refs=(): dict(est))
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final"})
    assert r.status_code == 409
    body = r.json()
    assert body["needs_confirm"] is True and body["estimate_usd"] == 1.5 and "confirm" in body["error"]
    assert job_count() == 0
    # other refusals do not ask for a confirm: the page shows their error instead
    spend(monkeypatch, gemini_usd=8.95)
    monkeypatch.setattr(app_mod, "estimate", lambda spec, kind="final", layout_refs=(): {
        **est, "action": "stop", "reason": "monthly cap reached"})
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "confirm": True})
    assert r.status_code == 409 and "needs_confirm" not in r.json()


# --- task 8 fix: x-bind objects are bound once ------------------------------------------------
def test_x_bind_objects_use_only_values_fixed_for_their_element():
    """Alpine evaluates the object form of x-bind once, when the element is made. So its argument may only
    be a loop variable (fixed for that element), never store state that changes while the element lives:
    the big preview kept the chosen sketch's src under the "Final image" label, and its drawing kept its
    first style. A changing image or drawing is a keyed x-for, so a new value makes a new element."""
    import re
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    binds = re.findall(r'x-bind="([^"]*)"', html)
    assert binds
    for b in binds:
        args = re.fullmatch(r"\$store\.studio\.\w+\((.*)\)", b)
        assert args, b
        assert "$store" not in args.group(1), b
    assert 'x-for="f in $store.studio.shownFiles()" :key="f"' in html
    assert 'x-for="c in $store.studio.drawnStyle()" :key="c"' in html


def test_422_hides_setting_names(client, monkeypatch):
    def boom(spec, refs=()):
        raise app_mod.CompileError("GEMINI_API_KEY and CLOUDFLARE_ACCOUNT_ID in brand/x.json")
    monkeypatch.setattr(app_mod, "compile_spec", boom)
    r = client.post("/api/preview", json={"answers": OAKMERE})
    assert r.status_code == 422 and r.json() == {"error": "a setting and a setting in x.json"}
