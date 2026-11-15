"""Checks engine (studio.review.run_checks) and the visual review brain task (studio.tasks.visual_review).

Fake checks and a fake brain only: no paid calls. The specs and checklists are the tracked ones in specs/ and prompts/.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import finish, ledger, logo, qa
from studio import review
from studio.brain import BrainError
from studio.tasks import visual_review

ROOT = Path(__file__).resolve().parents[1]
SID = "ovs-wblack-450-anchor-front"            # plain packshot, no decoration
LOGO_SID = "ovs-wblack-450-oakmere-puff-front"  # generate_and_verify decoration
IMAGE = ROOT / "out" / "finals" / "zz-review-test_final.jpg"  # never opened: every check is a fake
CODES = list(json.loads((ROOT / "brand" / "repairs.json").read_text(encoding="utf-8"))["codes"])

COLOUR_PASS = {"result": "pass", "code": None, "de2000": 1.23, "measured_hex": "#303032", "target_hex": "#2F2F31",
               "dL": 0.1, "da": 0.2, "db": -0.3, "mask_source": "vision (cached)", "mask_fraction": 0.4}
COLOUR_DRIFT = {**COLOUR_PASS, "result": "fail", "code": "colour_drift", "de2000": 7.5}
STRAY_PASS = {"result": "pass", "code": None, "found": [], "stray": []}
STRAY_FAIL = {"result": "fail", "code": "unintended_text", "found": ["AVENLIE"], "stray": ["AVENLIE"]}
LOGO_PASS = {"result": "pass", "decorations": [{"placement": "centre chest", "found": True, "codes": []}]}


class FakeBrain:
    """Answers every checklist line it is asked about (from the schema's enum) unless `answer` says otherwise."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def ask_json(self, task, prompt, schema, images=(), user="owner"):
        self.calls.append({"task": task, "prompt": prompt, "schema": schema, "images": list(images), "user": user})
        if self.error:
            raise BrainError(self.error)
        texts = schema["properties"]["lines"]["items"]["properties"]["text"]["enum"]
        data = self.answer(texts) if callable(self.answer) else {
            "lines": [{"text": t, "ok": True, "code": None, "note": "looks right"} for t in texts]}
        return SimpleNamespace(data=data, provider="fake", fallback=False)


def fail_line(text: str, code):
    """An answer where `text` fails with `code` and every other line passes."""
    def answer(texts):
        return {"lines": [{"text": t, "ok": t != text, "code": code if t == text else None, "note": "seen"}
                          for t in texts]}
    return answer


@pytest.fixture
def env(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(ledger, "entries", lambda: rows)
    monkeypatch.setattr(ledger, "record", lambda e: rows.append(e) or e)
    s = SimpleNamespace(rows=rows, calls=[], colour=COLOUR_PASS, stray=STRAY_PASS, logo=LOGO_PASS, brain=FakeBrain())

    def fake(name):
        def check(*args):
            s.calls.append(name)
            value = getattr(s, name)
            if isinstance(value, Exception):
                raise value
            return value
        return check

    monkeypatch.setattr(qa, "colour_check", fake("colour"))
    monkeypatch.setattr(qa, "stray_text_check", fake("stray"))
    monkeypatch.setattr(logo, "verify", fake("logo"))
    monkeypatch.setattr(review, "get_brain", lambda user, fast=False: s.brain)

    def run(shot_id=SID, user="owner"):
        rows.append({"status": "ok", "shot_id": shot_id, "phase": "final", "file": f"out/finals/{IMAGE.name}"})
        return review.run_checks(IMAGE, shot_id, user)

    s.run = run
    return s


def qa_events(rows):
    return [r for r in rows if r.get("event") == "qa"]


# --- run_checks -------------------------------------------------------------------------------

def test_all_pass_means_approve_candidate(env):
    r = env.run()
    assert r["codes"] == [] and r["complete"] is True
    assert (r["next"], r["next_code"]) == ("approve", None)
    assert r["colour"] == COLOUR_PASS and r["stray"] == STRAY_PASS and r["logo"] is None
    assert r["visual"]["lines"] and all(line["ok"] is True for line in r["visual"]["lines"])
    call = env.brain.calls[0]
    assert call["task"] == "visual_review" and call["user"] == "owner" and call["images"] == [IMAGE]
    (ev,) = qa_events(env.rows)
    assert ev["by"] == "studio" and ev["result"] == "pass" and ev["reason_codes"] == []
    assert ev["next"] == "approve" and ev["next_code"] is None and ev["colour"] == COLOUR_PASS
    assert ev["shot_id"] == SID and ev["file"] == f"out/finals/{IMAGE.name}"


def test_invalid_codes_and_lines_dropped(env):
    """Review Focus 5: an unknown checklist line or reason code from the brain is dropped, never trusted."""
    checklist = ["Two drawcords, equal length, metal aglets, through eyelets", "Hood double-layer with centre seam",
                 "Shows: rib cuffs and hem"]
    brain = FakeBrain(lambda texts: {"lines": [
        {"text": "Looks great overall", "ok": True, "code": None, "note": "invented line"},
        {"text": checklist[0], "ok": False, "code": "made_up_code", "note": "cords are round"},
        {"text": checklist[1], "ok": True, "code": "hands", "note": "fine"},
        {"text": checklist[2], "ok": "yes", "code": None, "note": "not a boolean"},
        "junk", None,
    ]})
    v = visual_review(IMAGE, checklist, brain)
    assert [line["text"] for line in v["lines"]] == checklist  # unknown line gone, order of the checklist
    by = {line["text"]: line for line in v["lines"]}
    assert by[checklist[0]]["ok"] is False and by[checklist[0]]["code"] is None  # invalid code dropped
    assert by[checklist[1]]["ok"] is True and by[checklist[1]]["code"] is None    # no code on a passing line
    assert by[checklist[2]]["ok"] is None                                         # not a verdict: not checked
    assert v["provider"] == "fake" and v["fallback"] is False

    schema = brain.calls[0]["schema"]
    item = schema["properties"]["lines"]["items"]
    assert item["properties"]["text"]["enum"] == checklist
    code_enum = [c for alt in item["properties"]["code"]["anyOf"] for c in alt.get("enum", [])]
    assert code_enum == CODES

    # through the engine: a failed line with an invalid code never approves, and adds no code
    first = qa.checklist(SID)[0]
    env.brain = FakeBrain(fail_line(first, "made_up_code"))
    r = env.run()
    assert r["codes"] == [] and r["complete"] is False
    assert (r["next"], r["next_code"]) == ("flag", None)
    assert qa_events(env.rows)[-1]["next"] == "flag"


@pytest.mark.parametrize("answer", [
    {"lines": []},
    {},
    {"lines": "everything passes"},
    {"lines": [{"text": "Two drawcords, equal length, metal aglets, through eyelets", "ok": True, "code": None,
                "note": "only one line answered"}]},
])
def test_missing_visual_lines_flag_not_approve(env, answer):
    """Review Focus 5: an empty or partial review means 'needs a person', never 'pass'."""
    env.brain = FakeBrain(lambda texts: answer)
    r = env.run()
    assert any(line["ok"] is None for line in r["visual"]["lines"])
    assert r["complete"] is False and r["codes"] == []
    assert (r["next"], r["next_code"]) == ("flag", None) and r["reason"]
    ev = qa_events(env.rows)[-1]
    assert ev["next"] == "flag" and ev["complete"] is False and ev["result"] == "incomplete"


def test_brain_error_flags_without_retry(env):
    env.brain = FakeBrain(error="claude: no answer within 60 s | then gemini: cap reached")
    r = env.run()
    assert len(env.brain.calls) == 1
    assert r["visual"]["lines"] == [] and r["visual"]["error"]
    assert r["complete"] is False and (r["next"], r["next_code"]) == ("flag", None)


def test_logo_checked_only_when_decorated(env):
    r = env.run(SID)
    assert "logo" not in env.calls and r["logo"] is None

    env.calls.clear()
    env.logo = {"result": "fail", "decorations": [{"placement": "centre chest", "found": True,
                                                   "codes": ["logo_size", "logo_text"]}]}
    r = env.run(LOGO_SID)
    assert env.calls == ["colour", "stray", "logo"]
    assert r["logo"] == env.logo and r["codes"] == ["logo_size", "logo_text"]
    assert (r["next"], r["next_code"]) == ("repair", "logo_size")


def test_codes_feed_next_action(env):
    env.colour = COLOUR_DRIFT
    r = env.run()
    assert r["codes"] == ["colour_drift"] and (r["next"], r["next_code"]) == ("finish", "colour_drift")

    env.colour = COLOUR_PASS
    env.brain = FakeBrain(fail_line(qa.checklist(SID)[0], "drawcord_shape"))
    r = env.run()
    assert r["codes"] == ["drawcord_shape"] and r["complete"] is True
    assert (r["next"], r["next_code"]) == ("repair", "drawcord_shape")
    ev = qa_events(env.rows)[-1]
    assert ev["reason_codes"] == ["drawcord_shape"] and ev["result"] == "fail" and ev["next_code"] == "drawcord_shape"

    # colour code, then stray text, then visual codes; a code found twice counts once
    env.colour, env.stray = COLOUR_DRIFT, STRAY_FAIL
    env.brain = FakeBrain(lambda texts: {"lines": [
        {"text": t, "ok": not t.startswith(("Free of", "Two drawcords")),
         "code": "unintended_text" if t.startswith("Free of") else "drawcord_count" if t.startswith("Two") else None,
         "note": ""} for t in texts]})
    r = env.run()
    assert r["codes"] == ["colour_drift", "unintended_text", "drawcord_count"]
    assert (r["next"], r["next_code"]) == ("repair", "unintended_text")


def test_measured_lines_left_to_the_scripts(env):
    """Colour, stray text and the logo are measured by scripts; the brain judges only what needs eyes."""
    env.run(LOGO_SID)
    asked = env.brain.calls[0]["schema"]["properties"]["lines"]["items"]["properties"]["text"]["enum"]
    full = qa.checklist(LOGO_SID)
    assert [t for t in full if t not in asked] == [
        "Colour matches Washed Black #2F2F31 (delta E <= 5)", "No unintended text or logos",
        "Intended logo passes verification (OCR, outline, colour)"]
    assert "Shows: the raised 3D puff logo" in asked


def test_budget_stop_spends_nothing_more(env):
    """A cap reached by any check stops the checks: no later check, no brain call, flagged with a reason."""
    env.stray = ledger.BudgetError("Gemini cap: $8.99 spent this month + $0.005 would exceed $9.00")
    r = env.run(LOGO_SID)
    assert env.calls == ["colour", "stray"] and env.brain.calls == []
    assert r["complete"] is False and (r["next"], r["next_code"]) == ("flag", None)
    assert "cap" in r["reason"] and r["stray"] is None and r["logo"] is None and r["visual"]["lines"] == []
    ev = qa_events(env.rows)[-1]
    assert ev["next"] == "flag" and ev["colour"] == COLOUR_PASS

    env.calls.clear()
    env.colour = ledger.BudgetError("Gemini cap reached")
    r = env.run(SID)
    assert env.calls == ["colour"] and r["colour"] is None and r["next"] == "flag"
    assert qa_events(env.rows)[-1]["colour"] == {}  # the contact sheet reads colour as a dict


def test_colour_not_measured_is_incomplete(env):
    env.colour = {"result": "error", "reason": "garment mask too small", "mask_source": "vision"}
    r = env.run()
    assert r["complete"] is False and r["next"] == "flag" and r["codes"] == []


# --- verdicts ---------------------------------------------------------------------------------

def test_record_verdict_by_user(env):
    env.rows.append({"status": "ok", "shot_id": SID, "phase": "final", "file": f"out/finals/{IMAGE.name}"})
    finish.record_verdict(IMAGE, "approved", "good")
    finish.record_verdict(IMAGE, "approved", "", by="owner")
    approved = [r for r in env.rows if r.get("event") == "approved"]
    assert [(a["by"], a["note"]) for a in approved] == [("user", "good"), ("owner", "")]


# --- the CLI is unchanged ---------------------------------------------------------------------

def test_qa_cli_output_and_event_unchanged(env, monkeypatch, capsys):
    import dotenv

    moves = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(qa.shutil, "move", lambda src, dest: moves.append((Path(src), Path(dest))))
    env.rows.append({"status": "ok", "shot_id": SID, "phase": "final", "file": f"out/finals/{IMAGE.name}"})

    monkeypatch.setattr(sys, "argv", ["qa", str(IMAGE), "--fail", "drawcord_shape", "--note", "round cords"])
    assert qa.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "colour  pass  dE2000 1.23  measured #303032 vs #2F2F31  (dL 0.1, da 0.2, db -0.3; mask: vision (cached))",
        "stray text: pass",
        "verdict fail  codes ['drawcord_shape']  next: repair drawcord_shape",
        f"moved   -> out/reject/{IMAGE.name}",
    ]
    rel = f"out/finals/{IMAGE.name}"
    assert qa_events(env.rows) == [{
        "event": "qa", "shot_id": SID, "file": rel, "result": "fail", "reason_codes": ["drawcord_shape"],
        "colour": COLOUR_PASS, "note": "round cords", "next": "repair", "next_code": "drawcord_shape"}]
    assert list(qa_events(env.rows)[0]) == ["event", "shot_id", "file", "result", "reason_codes", "colour", "note",
                                           "next", "next_code"]
    assert moves == [(IMAGE, ROOT / "out" / "reject" / IMAGE.name)]
    assert env.rows[-1] == {"event": "moved", "file": rel, "moved_to": f"out/reject/{IMAGE.name}"}

    env.rows.clear()
    env.rows.append({"status": "ok", "shot_id": SID, "phase": "final", "file": f"out/finals/{IMAGE.name}"})
    env.stray = STRAY_FAIL
    monkeypatch.setattr(sys, "argv", ["qa", str(IMAGE), "--visual", "pass"])
    assert qa.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "colour  pass  dE2000 1.23  measured #303032 vs #2F2F31  (dL 0.1, da 0.2, db -0.3; mask: vision (cached))",
        "stray text: FAIL ['AVENLIE']",
        "verdict pass  codes -  next: approve",
    ]
    assert qa_events(env.rows)[-1]["result"] == "pass" and len(moves) == 1
