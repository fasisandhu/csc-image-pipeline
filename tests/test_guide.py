import pytest
from fastapi.testclient import TestClient

from studio import app as app_mod
from studio import guide


def test_checks_page_uses_live_thresholds():
    page = guide.render("read-the-checks")
    for s in ("under 5", "15", "70%", "125%", "2 repairs"):
        assert s in page
    assert "{{" not in page


def test_guide_claims_match_what_runs_today():
    checks, start = guide.render("read-the-checks"), guide.render("start-here")
    assert "before you see" not in checks and "before you see" not in start  # the worker does not run QA yet
    assert "ignores case, spaces and punctuation" in checks  # logo.normalise_text


def test_all_pages_render_without_placeholders():
    for p in guide.PAGES:
        assert "{{" not in guide.render(p)
    assert "$9.00" in guide.render("rules")


def test_unknown_placeholder_raises(monkeypatch):
    monkeypatch.setattr(guide, "_read", lambda page: "Hello {{nope.key}}")
    with pytest.raises(KeyError):
        guide.render("start-here")


def test_unknown_page_raises():
    with pytest.raises(KeyError):
        guide.render("../app")


def local_client() -> TestClient:
    return TestClient(app_mod.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_guide_endpoint():
    c = local_client()
    r = c.get("/api/guide/read-the-checks")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "under 5" in r.text


@pytest.mark.parametrize("page", ["nope", "..%2Fapp", "%2e%2e/app", "app.py"])
def test_guide_endpoint_unknown_page_404(page):
    r = local_client().get(f"/api/guide/{page}")
    assert r.status_code == 404
    assert "error" in r.json()
