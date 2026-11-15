import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import ledger
from pipeline.compile import load_json
from studio import app as app_mod
from studio import guide
from studio.worker import COLOUR_CORRECTIONS_PER_RENDER, DRAFTS


def test_checks_page_uses_live_thresholds():
    page = guide.render("read-the-checks")
    for s in ("under 5", "15", "70%", "125%", "2 repairs"):
        assert s in page
    assert "{{" not in page


def test_guide_claims_match_what_runs_today():
    """The review chain checks every image Studio makes, after it is shown on Create image (so never "before you
    see" it); no page still says the checks do not run, or sends people to a step that has arrived."""
    checks, start = guide.render("read-the-checks"), guide.render("start-here")
    assert "before you see" not in checks and "before you see" not in start
    assert "ignores case, spaces and punctuation" in checks  # logo.normalise_text
    for p in guide.PAGES:
        page = guide.render(p)
        for stale in ("does not yet run", "for now", "For now", "next step", "ask the owner"):
            assert stale not in page, (p, stale)
    assert "Review and approve" in checks and "Review" in start


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
    assert guide.PAGES == ("start-here", "make-a-website-image", "review-and-approve", "read-the-checks", "rules")


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


def test_reference_packshot_approval_is_not_a_dead_end():
    """The Review tab is live: the page and the guide send people there, to the button that does it, and no longer
    to the owner (plan 2's stopgap)."""
    js = static_text("app.js")
    assert ("anchorDone: 'Your shot can use this reference packshot once it is approved: in the Review tab, press "
            "“Approve as reference packshot”, then come back and create your shot.'") in js
    assert "ask the owner" not in js and "arrives in the next step" not in js
    step5 = guide.render("make-a-website-image").split("## 5.")[1].split("## 6.")[0]
    assert "**Review**" in step5 and "**Approve as reference packshot**" in step5
    assert "ask the owner" not in step5 and "next step" not in step5
    step6 = guide.render("make-a-website-image").split("## 6.")[1]
    assert "Review and approve" in step6 and "next step" not in step6


# --- plan 3 task 7: the "Review and approve" page and the tour's Review tab stop ---------------
REVIEW_STEPS = (  # the sections of "Review and approve", in order
    "1. The checks run by themselves",
    "2. Fixes run by themselves, within limits",
    "3. Your verdict",
    "4. Reference packshots",
    "5. Rejection notes become rules",
    "6. Export from the Library",
)

UI_WORDS = (  # the page's own words that "Review and approve" names: buttons, states, headings and tabs
    "Review", "Library", "Approve & create", "Approve", "Approve as reference packshot", "Ask for a change",
    "Reject with a note", "Change my choices", "Run the checks", "Replace", "Needs a person",
    "Show what ran automatically", "Rules from rejection notes", "Where it applies", "Switch on", "Dismiss",
    "Export for the website", "Approval PDF",
)


def page_words() -> str:
    """Every word the page can show: index.html (entities decoded) and app.js."""
    return html.unescape(static_text("index.html")) + static_text("app.js")


def test_review_and_approve_page_renders():
    page = guide.render("review-and-approve")
    assert "{{" not in page and "}}" not in page
    at = [page.find("## " + s) for s in REVIEW_STEPS]
    assert all(i >= 0 for i in at), [s for s, i in zip(REVIEW_STEPS, at) if i < 0]
    assert at == sorted(at) and page.count("\n## ") == len(REVIEW_STEPS)
    lim = load_json("brand/repairs.json")["limits"]
    assert f"up to {lim['repairs_per_render']} repairs per image" in page
    assert f"up to {lim['regenerations_per_shot']} new images per shot" in page
    assert f"up to {COLOUR_CORRECTIONS_PER_RENDER} times per image" in page
    for s in UI_WORDS:
        assert f"**{s}" in page, s  # each button, state and heading it names, in bold as the guide marks them
    # the rule scopes the server offers (studio.library.scope_options): the kinds of image, or one style
    assert "all images, packshots, on-model, or one style" in page


def test_review_and_approve_numbers_come_from_the_rule_files(monkeypatch):
    """The limits are placeholders filled from brand/repairs.json and the worker, never typed into the page."""
    real = guide.load_json

    def fake(path):
        doc = real(path)
        if path == "brand/repairs.json":
            doc = {**doc, "limits": {"repairs_per_render": 7, "regenerations_per_shot": 9}}
        return doc
    monkeypatch.setattr(guide, "load_json", fake)
    monkeypatch.setattr(guide, "COLOUR_CORRECTIONS_PER_RENDER", 4)
    page = guide.render("review-and-approve")
    assert "up to 7 repairs per image" in page and "up to 9 new images per shot" in page
    assert "up to 4 times per image" in page
    assert guide.values()["limits.colour_corrections_per_render"] == "4"


def test_review_and_approve_names_only_words_on_the_page():
    """The buttons, states and headings the guide names are the page's own words (no drift)."""
    words = page_words()
    built = {"Show what ran automatically": "'▸ Show') + ' what ran automatically'"}  # a label the page puts together
    assert not [w for w in UI_WORDS if built.get(w, w) not in words]


def test_review_and_approve_endpoint():
    r = local_client().get("/api/guide/review-and-approve")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "## " + REVIEW_STEPS[0] in r.text


def test_tour_ends_on_the_review_tab_while_it_is_live():
    js, page = static_text("app.js"), static_text("index.html")
    tour = re.search(r"const TOUR = \[(.*?)\n  \];", js, re.S).group(1)
    assert re.findall(r"title: '([^']+)'", tour) == [
        "Start with your idea", "Pick the rest", "Your image", "Free sketches (optional)", "Approve & create",
        "Review tab"]
    assert "{ anchor: '#tab-review', tab: 'review', title: 'Review tab'," in tour
    assert ':id="\'tab-\' + t.id"' in page  # the tab links carry the ids the tour points at
    tabs = re.search(r"const TABS = \[(.*?)\n  \];", js, re.S).group(1)
    assert "{ id: 'review', label: 'Review' }," in tabs  # live today: not "later", not dimmed
    avail = re.search(r"function tourAvailable\(i\) \{(.*?)\n  \}", js, re.S).group(1)
    assert "tabLive(" in avail  # a stop on a tab is skipped while that tab is not live
    live = re.search(r"function tabLive\(id\) \{([^\n]*)\}\n", js).group(1)  # a one-line function
    assert "later" in live and "dim" in live


# --- final fix wave: the guide and the page say what really happens ------------------------------
FIXES_MAY_FOLLOW = "If the checks find a fault, the automatic fixes run within this shot's limits, as when it was made."


def test_run_the_checks_says_fixes_may_follow():
    """Minor 5: a check that finds a fault queues the usual (paid) fixes, so nothing says "no new image is made"."""
    js, page = static_text("app.js"), guide.render("review-and-approve")
    doc = " ".join(app_mod.recheck.__doc__.split())
    for where, text in (("app.js", js), ("guide", page), ("docstring", doc)):
        assert FIXES_MAY_FOLLOW in text, where
        assert "No new image is made" not in text and "no new image is made" not in text, where
    line = next(x for x in page.splitlines() if x.startswith("- **Run the checks"))
    assert "only while the image is still waiting among the finished images" in line  # library.in_finals


def test_worst_case_is_worded_plainly():
    """Minor 4: the worst case leaves out the checks' few cents, and says so."""
    js = static_text("app.js")
    assert "about ' + money(this.preview.worst_usd) + ' at most, plus a few cents for the checks'" in js
    for p in ("review-and-approve", "make-a-website-image"):
        assert "at most, plus a few cents for the checks" in guide.render(p), p
    assert "The most it could cost was shown" not in guide.render("review-and-approve")


def test_review_guide_matches_the_page():
    """T7: the marks, the boxes and the logo placement read as the page and the chain behave."""
    page, rules, js = guide.render("review-and-approve"), guide.render("rules"), static_text("app.js")
    assert "↻ fixed automatically" in page and "being fixed" not in page
    assert "'Fixed automatically · compare before and after'" in js  # the page's own words for ↻
    assert "A box on the image marks where the logo check found the logo." in page
    assert "where a check found something" not in page
    assert "reviewBoxes(it) {  // the logo boxes the check found" in js  # boxes are drawn for logos only
    for text in (page, rules):
        assert "front packshot with a chest logo" in text  # worker.front_packshot
    # the logo gate (library.logo_cleared): no note gets a failed logo approved
    assert "logo did not pass its check cannot be approved or exported, with or without a note" in page
    # the face line (review.run_checks): left to a person while the model has no reference photo
    assert "Studio cannot check the face. Its line shows ? Not checked" in page


def test_web_sizes_follow_the_export_widths(monkeypatch):
    """T7: the number and widths of the web sizes come from finish.WEBP_WIDTHS, never typed into the guide."""
    from pipeline import finish

    widths = finish.WEBP_WIDTHS
    page = guide.render("review-and-approve")
    listed = ", ".join(map(str, widths[:-1])) + " and " + str(widths[-1])
    assert f"up to {len(widths)} web sizes ({listed} pixels wide" in page and "three" not in page
    monkeypatch.setattr(finish, "WEBP_WIDTHS", (1200, 600))
    assert "up to 2 web sizes (1200 and 600 pixels wide" in guide.render("review-and-approve")


def test_each_pending_rule_asks_for_a_catalogue_check():
    """Minor 10: an AI draft can add a garment fact the note never gave; each pending rule asks the person to check."""
    page = static_text("index.html")
    pending = re.search(r'<template x-for="r in \$store\.studio\.pendingRules\(\)".*?</template>\s*</ul>', page, re.S)
    assert pending, "pending rules list missing"
    assert "Check every garment detail against our catalogue before switching on." in pending.group(0)
