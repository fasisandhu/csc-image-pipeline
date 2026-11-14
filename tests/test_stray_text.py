import json
from pathlib import Path

import pytest

from pipeline import ledger, qa
from pipeline.providers import vision as vision_mod
from pipeline.providers.vision import VisionResult

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scan(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(ledger, "entries", lambda: rows)
    monkeypatch.setattr(ledger, "record", lambda e: rows.append(e))
    monkeypatch.setattr(ledger, "check", lambda provider, est: None)
    monkeypatch.setattr(vision_mod.GeminiVision, "__init__", lambda self: None)

    def run(found, allowed):
        items = [{"text": t, "box": [0, 0, 10, 10]} for t in found]
        monkeypatch.setattr(vision_mod.GeminiVision, "read_all_text",
                            lambda self, image: VisionResult(items, 0.004, {"input_tokens": 1}))
        return qa.stray_text_check("out/finals/x.jpg", allowed)

    run.rows = rows
    return run


def test_design_text_parts_allowed(scan):
    assert scan(["OAKMERE", "ATHLETIC"], ["OAKMERE ATHLETIC"])["result"] == "pass"


def test_invented_label_fails(scan):
    r = scan(["AVENLIE CO."], [])
    assert r["result"] == "fail" and r["code"] == "unintended_text"
    assert r["stray"] == ["AVENLIE CO."]


def test_blank_reads_ignored(scan):
    assert scan(["", "  ", "--"], [])["result"] == "pass"


def test_allowed_texts_from_spec():
    load = lambda n: json.loads((ROOT / "specs" / f"{n}.json").read_text(encoding="utf-8"))
    assert qa.allowed_texts(load("ovs-wblack-450-oakmere-puff-front")) == ["OAKMERE ATHLETIC"]
    assert qa.allowed_texts(load("ovs-wblack-450-anchor-front")) == []


def test_scan_is_logged(scan):
    scan(["X"], [])
    assert [r["purpose"] for r in scan.rows if r.get("event") == "vision"] == ["stray_text"]
