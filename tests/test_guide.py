import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import ledger
from studio import app as app_mod
from studio import guide
from studio.worker import DRAFTS


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


# --- task 8: the guide view, the "Make a website image" page and the tour ----------------------
STEPS = (  # the six steps of "Make a website image", in order, as they read on the page
    "1. Type your idea, or skip and pick",
    "2. Answer what is left",
    '3. Check "Your image" and the cost',
    f"4. Optional: see {DRAFTS} free sketches",
    "5. Approve & create",
    "6. See your image",
)
STATIC = Path(app_mod.STATIC_DIR)


def static_text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_guide_pages_list():
    assert guide.PAGES == ("start-here", "make-a-website-image", "read-the-checks", "rules")


def test_make_a_website_image_page_renders():
    page = guide.render("make-a-website-image")
    assert "{{" not in page and "}}" not in page
    at = [page.find("## " + s) for s in STEPS]
    assert all(i >= 0 for i in at), [s for s, i in zip(STEPS, at) if i < 0]
    assert at == sorted(at)  # in order
    assert page.count("\n## ") == len(STEPS)  # six steps, no more
    assert f"${ledger.RUN_APPROVAL_USD:.2f}" in page  # the ask-first rule, from the ledger
    assert "Show the full prompt" in page and "Approve & create" in page  # the builder's own button words


def test_make_a_website_image_endpoint():
    r = local_client().get("/api/guide/make-a-website-image")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "## " + STEPS[0] in r.text


def test_sketch_count_matches_the_builder():
    """The guide's sketch count comes from the worker; the button words in the page say the same."""
    assert f"see {DRAFTS} free sketches" in guide.render("make-a-website-image")
    assert f"See {DRAFTS} free sketches" in static_text("app.js")


def test_page_lists_the_same_guide_pages_as_the_server():
    block = re.search(r"const GUIDE_PAGES = \[(.*?)\];", static_text("app.js"), re.S)
    assert block, "GUIDE_PAGES missing from app.js"
    assert tuple(re.findall(r"id: '([a-z-]+)'", block.group(1))) == guide.PAGES


def test_guide_content_never_reaches_the_page_as_html():
    """Markdown is drawn from marked's tokens as DOM nodes with text content: no HTML string is injected."""
    js, html = static_text("app.js"), static_text("index.html")
    for name, text in (("app.js", js), ("index.html", html)):
        for sink in ("x-html", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser",
                     "createContextualFragment", "srcdoc"):
            assert sink not in text, f"{sink} in {name}"
    assert "marked.lexer(" in js  # tokens only
    assert not re.search(r"marked\s*\.\s*(parse|parseInline|parser)\b|marked\s*\(", js)  # never marked's HTML output
    assert 'id="guide-doc"' in html


def test_tour_flag_is_kept_in_storage_inside_try():
    js = static_text("app.js")
    assert "storage.set('tourSeen', true)" in js and "storage.get('tourSeen', false)" in js
    store = re.search(r"const storage = \{(.*?)\n  \};", js, re.S).group(1)
    assert store.count("try {") == 2 and store.count("catch (e)") == 2  # every localStorage call is in try/catch
    calls = r"localStorage\s*\."
    assert len(re.findall(calls, js)) == len(re.findall(calls, store)) == 2  # and nothing else touches it
