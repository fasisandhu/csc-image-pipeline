import json
import os
import socket
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pipeline import generate, ledger, router
from pipeline.compile import compile_spec, load_json
from pipeline.specs import build_spec
from studio import __main__ as main_mod
from studio import app as app_mod
from studio import db
from studio import worker as worker_mod
from studio.brain.base import BrainError
from studio.identity import current_user
from studio.worker import Worker

OAKMERE = {"purpose": "website", "catalogue": "pullover-oversized", "colour": "washed-black", "gsm": 450,
           "recipe": "packshot-front", "decoration": "design", "design": "oakmere-athletic",
           "technique": "3D puff embroidery", "placement": "centre chest", "size_cm": 22}
FAKE_FILE = Path("out/finals/fake.jpg")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(k, "test")
    log: list[dict] = []
    monkeypatch.setattr(ledger, "entries", lambda: log)
    monkeypatch.setattr(ledger, "record", lambda e: log.append(e) or e)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "studio.db")
    calls: list[str] = []

    def fake_run(c, r):
        calls.append(r.phase)
        return FAKE_FILE, SimpleNamespace(cost_usd=0.134)

    monkeypatch.setattr(generate, "run_route", fake_run)
    return calls


def local_client() -> TestClient:
    """A browser on this PC: Host 127.0.0.1 and a client address of 127.0.0.1 (no test-only allow-list)."""
    return TestClient(app_mod.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


@pytest.fixture
def client():
    return local_client()


def spend(monkeypatch, gemini_usd: float = 0.0, neurons: float = 0.0):
    month, day = ledger.now().strftime("%Y-%m"), ledger.now().strftime("%Y-%m-%d")
    rows = [{"ts": f"{month}-01T00:00:00", "provider": "gemini", "tier": "premium", "cost_usd": gemini_usd},
            {"ts": f"{day}T00:00:00", "provider": "cloudflare", "units": {"neurons": neurons}}]
    monkeypatch.setattr(ledger, "entries", lambda: rows)


def job_count() -> int:
    with closing(db.connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def make_job(kind="final", confirmed_by=None) -> int:
    with closing(db.connect()) as conn:
        return db.create_job(conn, "owner", kind, OAKMERE, build_spec(OAKMERE), {"base_usd": 0.1374}, confirmed_by)


def get_job(job_id: int) -> dict:
    with closing(db.connect()) as conn:
        return db.get_job(conn, job_id)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "budget": ledger.status()}


def test_questions_endpoint(client):
    r = client.post("/api/questions", json={"answers": {"catalogue": "zip", "colour": ["olive"], "mood": "x"}})
    assert r.status_code == 200
    body = r.json()
    assert "purpose" in body["missing"]
    secs = {s["id"]: s for s in body["sections"]}
    assert secs["catalogue"]["value"] == "zip"
    assert secs["colour"]["value"] is None and "mood" not in secs  # invalid values dropped


def test_preview_two_costs(client):
    r = client.post("/api/preview", json={"answers": OAKMERE})
    assert r.status_code == 200, r.text
    b = r.json()
    base = round(router.route(compile_spec(build_spec(OAKMERE))).estimate["cost_usd"], 4)  # 0.1374 today
    lim = load_json("brand/repairs.json")["limits"]
    R, P = lim["regenerations_per_shot"], lim["repairs_per_render"]
    assert b["base_usd"] == base
    assert b["worst_usd"] == round(base * (1 + R) + base * P * (1 + R), 2)  # 1.24 today
    assert b["action"] == "run" and b["reason"] == ""
    assert b["shot_id"] == build_spec(app_mod.checked(OAKMERE))["shot"]["shot_id"]
    assert b["shot_id"].startswith("pullover-oversized-washed-black-450-packshot-front-oakmere-athletic-")
    assert load_json("brand/brand.json")["style_block"] in b["prompt"]
    assert isinstance(b["warnings"], list) and b["needs_anchor"] is False


def test_needs_anchor_reported_not_enforced(client):
    olive = {**OAKMERE, "colour": "olive", "decoration": "none"}
    for k in ("design", "technique", "placement", "size_cm"):
        olive.pop(k)
    assert client.post("/api/preview", json={"answers": olive}).json()["needs_anchor"] is False  # packshot-front
    on_model = {**olive, "recipe": "on-model-front", "cast": "Maya", "scene": "warm-studio"}
    r = client.post("/api/preview", json={"answers": on_model})
    assert r.status_code == 200 and r.json()["needs_anchor"] is True


def test_preview_oversize_is_422(client, monkeypatch):
    big = {**OAKMERE, "recipe": "packshot-back", "placement": "full back", "size_cm": 34}
    r = client.post("/api/preview", json={"answers": big})
    assert r.status_code == 422 and "size_cm" in r.json()["error"]
    # the compiler is the backstop when the answer check is bypassed
    monkeypatch.setattr(app_mod, "valid_value", lambda *a, **k: True)
    r = client.post("/api/preview", json={"answers": big})
    assert r.status_code == 422 and "exceeds max 30" in r.json()["error"]


@pytest.mark.parametrize("patch, field", [
    ({"colour": ["olive"]}, "colour"),
    ({"catalogue": "../x"}, "catalogue"),
    ({"mood": "happy"}, "mood"),
    ({"decoration": True}, "decoration"),
    ({"colour": {"name": "Teal", "hex": "#1F6F78", "extra": "x"}}, "colour"),
])
def test_bad_answers_are_422_before_build_spec(client, monkeypatch, patch, field):
    def never(*a, **k):
        raise AssertionError("build_spec called with unvalidated answers")
    monkeypatch.setattr(app_mod, "build_spec", never)
    for url in ("/api/preview", "/api/jobs"):
        r = client.post(url, json={"answers": {**OAKMERE, **patch}})
        assert r.status_code == 422 and field in r.json()["error"], (url, r.text)
    assert job_count() == 0


def test_incomplete_answers_are_422(client):
    r = client.post("/api/preview", json={"answers": {"catalogue": "zip"}})
    assert r.status_code == 422 and "purpose" in r.json()["error"]


def test_job_over_one_dollar_needs_confirm(client, monkeypatch):
    est = {"base_usd": 1.5, "worst_usd": 13.5, "action": "run", "reason": "", "tier": "premium", "model": "m"}
    monkeypatch.setattr(app_mod, "estimate", lambda spec, kind="final": dict(est))
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final"})
    assert r.status_code == 409 and "confirm" in r.json()["error"]
    assert job_count() == 0
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "final", "confirm": True})
    assert r.status_code == 201
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["confirmed_by"] == "owner" and job["status"] == "queued" and job["user"] == "owner"
    assert job["answers"] == OAKMERE and job["estimate"] == est


def test_job_refused_when_route_asks(client, monkeypatch):
    spend(monkeypatch, gemini_usd=8.95)
    reason = router.route(compile_spec(build_spec(OAKMERE))).reason
    r = client.post("/api/jobs", json={"answers": OAKMERE})
    assert r.status_code == 409 and r.json()["error"] == reason and "monthly cap" in reason
    assert job_count() == 0


def test_drafts_job_checks_neurons_not_dollars(client, monkeypatch):
    spend(monkeypatch, gemini_usd=8.95)
    assert client.post("/api/jobs", json={"answers": OAKMERE, "kind": "drafts"}).status_code == 201
    spend(monkeypatch, neurons=9400)
    r = client.post("/api/jobs", json={"answers": OAKMERE, "kind": "drafts"})
    assert r.status_code == 409 and "queue for 00:00 UTC" in r.json()["error"]
    assert job_count() == 1


def test_worker_runs_once_and_never_requeues(isolated):
    final, drafts = make_job("final"), make_job("drafts")
    w = Worker(db.connect)  # default runner: generate.run_route, looked up at call time
    assert w.run_once() is True and w.run_once() is True and w.run_once() is False
    assert get_job(final)["status"] == "done"
    assert get_job(final)["result"] == {"files": ["out/finals/fake.jpg"], "cost_usd": 0.134}
    assert get_job(drafts)["status"] == "done" and len(get_job(drafts)["result"]["files"]) == 3
    assert isolated == ["final", "draft", "draft", "draft"]

    def boom(c, r):
        raise RuntimeError("provider down")
    failing = make_job("final")
    w = Worker(db.connect, runner=boom)
    assert w.run_once() is True
    job = get_job(failing)
    assert job["status"] == "failed" and job["error"] == "provider down" and job["result"] is None
    assert w.run_once() is False


def test_failed_drafts_job_keeps_the_drafts_it_made(isolated):
    made = []

    def third_fails(c, r):
        if len(made) == 2:
            raise RuntimeError("provider down")
        made.append(r.phase)
        return Path(f"out/drafts/d{len(made)}.png"), SimpleNamespace(cost_usd=0.0)
    jid = make_job("drafts")
    assert Worker(db.connect, runner=third_fails).run_once() is True
    job = get_job(jid)
    assert job["status"] == "failed" and job["error"] == "provider down"
    assert job["result"] == {"files": ["out/drafts/d1.png", "out/drafts/d2.png"], "cost_usd": 0.0}


def test_worker_rechecks_one_dollar_approval_at_run_time(isolated, monkeypatch):
    real = worker_mod.routes_for

    def pricey(c, kind):  # the estimate grew past $1 after the job was queued
        routes = real(c, kind)
        for r in routes:
            r.estimate = {**r.estimate, "cost_usd": 1.5}
        return routes
    monkeypatch.setattr(worker_mod, "routes_for", pricey)
    unconfirmed, confirmed = make_job("final"), make_job("final", confirmed_by="owner")
    w = Worker(db.connect)
    assert w.run_once() is True and w.run_once() is True
    job = get_job(unconfirmed)
    assert job["status"] == "failed" and "$1.50 needs confirmation" in job["error"] and job["result"] is None
    assert get_job(confirmed)["status"] == "done"
    assert isolated == ["final"]  # only the confirmed job spent


def test_worker_rechecks_budget_at_run_time(isolated, monkeypatch):
    jid = make_job("final")
    spend(monkeypatch, gemini_usd=8.95)  # spent elsewhere after the job was queued
    assert Worker(db.connect).run_once() is True
    job = get_job(jid)
    assert job["status"] == "failed" and "monthly cap" in job["error"] and isolated == []


def test_restart_marks_running_interrupted():
    jid = make_job()
    with closing(db.connect()) as conn:
        db.set_status(conn, jid, "running")
        assert db.mark_interrupted(conn) == 1
        assert db.get_job(conn, jid)["status"] == "interrupted"
        assert db.next_queued(conn) is None
    assert Worker(db.connect).run_once() is False


def test_remote_host_forbidden():
    with pytest.raises(HTTPException) as e:
        current_user(SimpleNamespace(client=SimpleNamespace(host="10.0.0.5")))
    assert e.value.status_code == 403
    assert current_user(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))) == "owner"
    assert TestClient(app_mod.app, base_url="http://127.0.0.1", client=("10.0.0.5", 50000)).get(
        "/api/health").status_code == 403


def test_no_test_hosts_in_production_allow_lists():
    assert local_client().get("/api/health").status_code == 200
    assert TestClient(app_mod.app).get("/api/health").status_code == 400  # Host "testserver"
    assert TestClient(app_mod.app, base_url="http://127.0.0.1").get("/api/health").status_code == 403  # "testclient"
    with pytest.raises(HTTPException):
        current_user(SimpleNamespace(client=SimpleNamespace(host="testclient")))


def test_browser_cross_site_requests_blocked(client):
    r = client.get("/api/health", headers={"host": "rebind.example"})  # DNS rebinding
    assert r.status_code == 400
    r = client.post("/api/jobs", content=json.dumps({"answers": OAKMERE}), headers={"content-type": "text/plain"})
    assert r.status_code == 422 and job_count() == 0  # a no-preflight form post is not parsed


def test_no_secrets_in_job(client, monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", "tok-SECRET")
    r = client.post("/api/jobs", json={"answers": OAKMERE})
    assert r.status_code == 201
    jid = r.json()["id"]

    def leaky(c, r):
        raise RuntimeError(f"auth failed with {os.environ['STUDIO_OWNER_CLAUDE_OAUTH_TOKEN']}")
    assert Worker(db.connect, runner=leaky).run_once() is True
    body = client.get(f"/api/jobs/{jid}").text
    assert "failed" in body and "tok-SECRET" not in body
    assert b"tok-SECRET" not in (tmp_path / "studio.db").read_bytes()


def test_missing_job_is_404(client):
    r = client.get("/api/jobs/99")
    assert r.status_code == 404 and "error" in r.json()


def test_brief_endpoint(client, monkeypatch):
    seen = []

    class FakeBrain:
        def ask_json(self, task, prompt, schema, images=(), user="owner"):
            return SimpleNamespace(data={"colour": "olive"}, provider="fake", fallback=False)

    def fake_get_brain(user, fast=False):
        seen.append((user, fast))
        return FakeBrain()
    monkeypatch.setattr(app_mod, "get_brain", fake_get_brain)
    r = client.post("/api/brief", json={"text": "olive hoodie", "answers": {"gsm": 450, "mood": "x"}})
    assert r.status_code == 200, r.text
    assert r.json()["answers"] == {"gsm": 450, "colour": "olive"} and seen == [("owner", True)]


def test_brief_brain_error_is_503(client, monkeypatch):
    class Broken:
        def ask_json(self, *a, **k):
            raise BrainError("claude: offline | then gemini: offline")
    monkeypatch.setattr(app_mod, "get_brain", lambda user, fast=False: Broken())
    r = client.post("/api/brief", json={"text": "olive hoodie", "answers": {}})
    assert r.status_code == 503 and r.json() == {"error": "claude: offline | then gemini: offline"}


@pytest.fixture
def startup(monkeypatch):
    """Record the startup steps; nothing loads .env, touches the database or serves."""
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, connect_fn):
            pass

        def start(self):
            calls.append("worker")

    def connect(*a, **k):
        calls.append("db")
        raise AssertionError("database touched")
    monkeypatch.setattr(main_mod, "load_dotenv", lambda *a, **k: calls.append("dotenv"))
    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(main_mod, "Worker", FakeWorker)
    monkeypatch.setattr(main_mod, "serve", lambda sock: calls.append(("serve", sock.getsockname())))
    return calls


def test_second_copy_does_not_start(startup, capsys):
    with socket.socket() as other:  # a running server already holds the port
        other.bind(("127.0.0.1", 0))
        other.listen()
        assert main_mod.main(other.getsockname()[1]) != 0
    assert startup == []
    assert "already running" in capsys.readouterr().err


def test_startup_binds_first_then_marks_then_starts_worker_then_serves(startup, monkeypatch):
    real_bind = main_mod.bind

    def bind(port):
        sock = real_bind(port)
        startup.append("bind")
        return sock
    monkeypatch.setattr(main_mod, "bind", bind)
    monkeypatch.setattr(db, "connect", lambda *a, **k: startup.append("db") or SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "mark_interrupted", lambda conn: startup.append("mark") or 0)
    assert main_mod.main(0) == 0
    serve = startup.pop()
    assert startup == ["bind", "dotenv", "db", "mark", "worker"]
    assert serve[0] == "serve" and serve[1][0] == "127.0.0.1" and serve[1][1] > 0
