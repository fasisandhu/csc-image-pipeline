import numpy as np
import pytest

from pipeline import ledger
from pipeline.colour import de2000, hex_to_rgb, lab_to_srgb, srgb_to_lab
from pipeline.logo import normalise_text, outline_iou
from pipeline.providers.vision import _looks_yx
from pipeline.qa import next_action


def test_de2000_reference_pairs():
    # Sharma et al. (2005) test data, pairs 1 and 17
    assert de2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) == pytest.approx(2.0425, abs=1e-3)
    assert de2000((50, 2.5, 0), (73, 25, -18)) == pytest.approx(27.1492, abs=1e-3)


def test_lab_roundtrip():
    rgb = np.array([hex_to_rgb(h) for h in ("#2F2F31", "#EDE6D6", "#5A1F2B")], float)
    assert np.allclose(lab_to_srgb(srgb_to_lab(rgb)), rgb, atol=0.5)


def test_polygon_axis_order_detection():
    box = [100, 400, 300, 900]  # ymin, xmin, ymax, xmax
    xy = [[400, 100], [900, 100], [900, 300], [400, 300]]
    assert not _looks_yx(xy, box)
    assert _looks_yx([[y, x] for x, y in xy], box)


def test_text_normalisation():
    assert normalise_text("OAKMERE\nATHLETIC") == normalise_text("Oakmere Athletic.")
    assert normalise_text("0AKMERE") != normalise_text("OAKMERE")


def test_outline_iou_matches_same_shape_and_rejects_other():
    art = np.zeros((100, 200), np.uint8)
    art[30:70, 20:180] = 255  # a bar
    crop = np.full((120, 240, 3), 40, np.uint8)
    crop[38:82, 30:210] = 230  # same bar, slightly offset and scaled
    assert outline_iou(crop, art)[0] > 0.8
    other = np.full((120, 240, 3), 40, np.uint8)
    other[10:110, 100:140] = 230  # a vertical bar instead
    assert outline_iou(other, art)[0] < 0.5


@pytest.fixture
def fake_log(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(ledger, "entries", lambda: rows)
    return rows


def final(n=1):
    return [{"status": "ok", "shot_id": "s", "phase": "final", "file": f"out/finals/f{i}.jpg"} for i in range(n)]


def test_next_action_rules(fake_log):
    fake_log += final(1)
    e = fake_log[0]
    assert next_action(e, []) == ("approve", None)
    assert next_action(e, ["colour_drift"]) == ("finish", "colour_drift")
    assert next_action(e, ["drawcord_shape", "rib_width"]) == ("repair", "drawcord_shape")  # one change per repair
    assert next_action(e, ["face"]) == ("regenerate", None)
    fake_log.append({"status": "ok", "phase": "repair", "root": e["file"], "shot_id": "s"})
    assert next_action(e, ["logo_text"]) == ("composite", "logo_text")  # logo: repair once, then composite
    assert next_action(e, ["hands"]) == ("repair", "hands")
    fake_log.append({"status": "ok", "phase": "repair", "root": e["file"], "shot_id": "s"})
    assert next_action(e, ["hands"]) == ("regenerate", None)  # two repairs used


def test_next_action_flags_after_two_regenerations(fake_log):
    fake_log += final(3)
    assert next_action(fake_log[-1], ["face"]) == ("flag", None)


def test_export_requires_approval(fake_log, tmp_path):
    from pipeline.finish import export
    with pytest.raises(SystemExit, match="no approval"):
        export(tmp_path / "x.jpg")


def test_as_shot_spec_reads_a_dashboard_spec_from_out_specs():
    """`python -m pipeline.logo composite --as-shot <id>` finds a dashboard final's spec in out/specs/ as well as
    specs/ (qa.spec_path), not only hand-written specs (final review minor)."""
    import json
    import uuid

    from pipeline import logo, qa
    sid = f"zz-test-as-shot-{uuid.uuid4().hex[:8]}"
    target = qa.ROOT / "out" / "specs" / f"{sid}.json"
    assert not (qa.ROOT / "specs" / f"{sid}.json").exists()
    spec = {"set": {"set_id": sid}, "shot": {"shot_id": sid, "garment": {"catalogue": "zip"}}}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(spec), encoding="utf-8")
        assert logo.as_shot_spec(sid) == spec
    finally:
        target.unlink(missing_ok=True)
    with pytest.raises(FileNotFoundError):
        logo.as_shot_spec(sid)
