import json

import pytest

from pipeline import ledger, router
from pipeline.compile import CompileError, compile_file, compile_spec, load_json

ANCHOR = "specs/ovs-wblack-450-anchor-front.json"
ON_MODEL = "specs/ovs-wblack-450-maya-front.json"
STYLE = load_json("brand/brand.json")["style_block"]


@pytest.fixture(autouse=True)
def keys_and_empty_log(monkeypatch):
    for k in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(k, "test")
    monkeypatch.setattr(ledger, "entries", lambda: [])


def spend(monkeypatch, gemini: dict | None = None, neurons: float = 0.0):
    month, day = ledger.now().strftime("%Y-%m"), ledger.now().strftime("%Y-%m-%d")
    rows = [{"ts": f"{month}-01T00:00:00", "provider": "gemini", "tier": t, "cost_usd": v} for t, v in (gemini or {}).items()]
    rows.append({"ts": f"{day}T00:00:00", "provider": "cloudflare", "units": {"neurons": neurons}})
    monkeypatch.setattr(ledger, "entries", lambda: rows)


def test_one_spec_gives_both_formats():
    c = compile_file(ANCHOR)
    para, flux = c.prompts["paragraph"], json.loads(c.prompts["flux_json"])
    assert set(flux) == {"subject", "garment", "decoration", "scene", "camera", "lighting", "style"}
    assert STYLE in para and flux["style"] == STYLE
    assert "Washed Black" in para and "stands away from the body" in para
    assert "dropped shoulder" in para and "2x2 rib" in para
    assert 120 <= c.word_count <= 220


def test_on_model_uses_cast_verbatim_and_creases():
    c = compile_file(ON_MODEL)
    desc = load_json("brand/cast.json")["cast"]["Maya"]["description"]
    assert desc in c.prompts["paragraph"] and desc in json.loads(c.prompts["flux_json"])["subject"]
    assert "creases at the elbows" in c.prompts["paragraph"]
    assert any("Face matches" in q for q in c.qa) and any("Hands" in q for q in c.qa)
    assert c.refs[0]["path"].startswith("refs/anchors/") and "Image 1 is the exact garment" in c.prompts["paragraph"]


def test_decoration_over_max_size_rejected():
    spec = json.loads(open(ANCHOR, encoding="utf-8").read())
    spec["shot"]["decoration"] = [{"type": "3D puff embroidery", "artwork": "brand/designs/oakmere-athletic.svg",
                                   "placement": "left chest", "size_cm": 12, "render_mode": "generate_and_verify"}]
    with pytest.raises(CompileError, match="exceeds max"):
        compile_spec(spec)


def test_logo_shot_attaches_artwork_and_numbers_it():
    spec = json.loads(open(ANCHOR, encoding="utf-8").read())
    spec["shot"]["decoration"] = [{"type": "3D puff embroidery", "artwork": "brand/designs/oakmere-athletic.svg",
                                   "placement": "centre chest", "size_cm": 22, "render_mode": "generate_and_verify"}]
    c = compile_spec(spec)
    assert c.refs[0]["path"] == "brand/designs/png/oakmere-athletic_ref.png"
    assert "logo shown in Image 1" in c.prompts["paragraph"] and "Image 1 is the logo artwork" in c.prompts["paragraph"]
    assert any("Intended logo" in q for q in c.qa)


def test_route_standard_runs_on_standard():
    r = router.route(compile_file(ANCHOR))
    assert (r.action, r.tier, r.model, r.size) == ("run", "standard", "gemini-3.1-flash-image", "2K")


def test_route_standard_falls_back_to_premium_when_split_used(monkeypatch):
    spend(monkeypatch, {"standard": 3.95})
    r = router.route(compile_file(ANCHOR))
    assert (r.action, r.tier) == ("run", "premium")


def test_route_asks_before_crossing_cap(monkeypatch):
    spend(monkeypatch, {"standard": 3.95, "premium": 4.95})
    r = router.route(compile_file(ANCHOR))
    assert r.action == "ask" and "cap" in r.reason


def test_premium_never_downgrades(monkeypatch):
    spend(monkeypatch, {"premium": 5.95})
    c = compile_file(ANCHOR)
    c.tier = "premium"
    r = router.route(c)
    assert (r.action, r.tier) == ("ask", "premium")


def test_draft_queues_past_neuron_cap(monkeypatch):
    spend(monkeypatch, neurons=9450)
    r = router.route(compile_file(ANCHOR), phase="draft")
    assert (r.action, r.provider) == ("queue", "cloudflare")


def test_plan_counts_pending_and_flags_approval():
    c = compile_file(ANCHOR)
    routes, summary = router.plan([c] * 12, drafts_per_shot=0)
    assert summary["total_usd"] > ledger.RUN_APPROVAL_USD and summary["needs_approval"]
    _, small = router.plan([c], drafts_per_shot=3)
    assert not small["needs_approval"] and small["total_neurons"] > 300


def test_palette_colour_brings_hex():
    c = compile_file(ANCHOR)
    assert "Washed Black (#2F2F31)" in c.prompts["paragraph"]
    assert any("#2F2F31" in q for q in c.qa)


def test_custom_colour_allowed_and_validated():
    spec = json.loads(open(ANCHOR, encoding="utf-8").read())
    spec["shot"]["garment"]["colour"] = {"name": "Customer Lilac", "hex": "#B9A6D3"}
    assert "Customer Lilac (#B9A6D3)" in compile_spec(spec).prompts["paragraph"]
    spec["shot"]["garment"]["colour"] = {"name": "Lilac", "hex": "lilac"}
    with pytest.raises(CompileError, match="#RRGGBB"):
        compile_spec(spec)
    spec["shot"]["garment"]["colour"] = "teal"
    with pytest.raises(CompileError, match="palette"):
        compile_spec(spec)


def test_ledger_record_holds_a_lock(monkeypatch, tmp_path):
    """The worker thread and request threads spend at the same time: each append runs under one lock."""
    import threading

    monkeypatch.setattr(ledger, "LOG", tmp_path / "log.jsonl")
    seen = []

    class Spy:
        def __enter__(self):
            seen.append("enter")

        def __exit__(self, *exc):
            seen.append("exit")

    monkeypatch.setattr(ledger, "_LOCK", Spy())
    entry = ledger.record({"provider": "gemini", "cost_usd": 0.1})
    assert seen == ["enter", "exit"] and entry["cost_usd"] == 0.1

    monkeypatch.setattr(ledger, "_LOCK", threading.Lock())
    monkeypatch.setattr(ledger, "LOG", tmp_path / "threads.jsonl")
    big = "x" * 20000  # long lines: an unlocked append could interleave
    threads = [threading.Thread(target=lambda i=i: [ledger.record({"i": i, "j": j, "pad": big}) for j in range(25)])
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (tmp_path / "threads.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200 and all(json.loads(line)["pad"] == big for line in lines)
