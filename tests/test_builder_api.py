import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import generate, ledger, qa
from pipeline.specs import build_spec
from studio import db
from studio.worker import Worker

from test_app import OAKMERE, isolated, make_job  # noqa: F401  (autouse fixture)


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
