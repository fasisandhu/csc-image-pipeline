"""Checks engine: the automatic QA of one render, as the dashboard's check step runs it.

Scripts measure what they can: the garment colour against the spec hex, stray text, and the logo of a
decoration the model rendered itself. The brain judges the rest of the checklist by eye (tasks.visual_review).
qa.next_action then says what happens next under the limits in brand/repairs.json.

Nothing here approves. A clean result is only a candidate for a person's approval; an incomplete one (a check
that could not run, a line the brain did not judge, a fault without a known reason code) is always "flag".
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline import ledger, logo, qa
from pipeline.compile import ROOT, is_missing, load_json, resolve_colour
from pipeline.providers import ProviderError

from .brain import BrainError, get_brain
from .tasks import visual_review

# brand/qa_checklist.json items measured by a script (colour_check, stray_text_check, logo.verify), not by eye
MEASURED = ("colour", "no_text", "logo")
FACE = "face"  # the item judged against the cast member's approved reference sheet
STEP_WORDS = {"colour": "colour check", "stray": "text check", "logo": "logo check"}


def _template(text: str) -> re.Pattern:
    """A checklist item's text as a pattern: each {field} matches any filled-in value."""
    return re.compile(".+?".join(re.escape(part) for part in re.split(r"\{[^{}]*\}", text)))


def _items() -> dict[str, re.Pattern]:
    """brand/qa_checklist.json's items as patterns, by id. Raises ValueError when an id this engine relies on is gone:
    a renamed id would quietly hand the face to the AI to judge."""
    items = {i["id"]: _template(i["text"]) for i in load_json("brand/qa_checklist.json")["items"]}
    gone = [i for i in (FACE,) if i not in items]
    if gone:
        raise ValueError(f"brand/qa_checklist.json has no item with id {', '.join(gone)}; the checks cannot run")
    return items


def visual_checklist(shot_id: str, skip: tuple[str, ...] = ()) -> list[str]:
    """The shot's checklist without the lines a script measures, nor the items whose ids are in `skip`."""
    items = _items()
    left_out = [items[i] for i in (*MEASURED, *skip) if i in items]
    return [line for line in qa.checklist(shot_id) if not any(m.fullmatch(line) for m in left_out)]


def face_line(shot_id: str) -> str | None:
    """The shot's face line (checklist id "face"), or None when it has none."""
    pattern = _items()[FACE]
    return next((line for line in qa.checklist(shot_id) if pattern.fullmatch(line)), None)


def has_face_reference(spec: dict) -> bool:
    """Whether the shot's cast member has an approved reference sheet (brand/cast.json) on disk."""
    cast = (spec["shot"].get("model") or {}).get("cast")
    sheet = load_json("brand/cast.json")["cast"].get(cast, {}).get("reference_sheet")
    return not is_missing(sheet) and (ROOT / sheet).is_file()


def _incomplete(colour: dict | None, visual: dict) -> str | None:
    """Why the checks cannot stand as a verdict, in plain words; None when every check gave one."""
    if colour is None or colour.get("result") == "error":
        why = (colour or {}).get("reason")
        return "The garment colour could not be measured" + (f" ({why})." if why else ".")
    if visual.get("error"):
        return visual["error"]
    lines = visual["lines"]
    if not lines or any(line["ok"] is None for line in lines):
        return "The AI visual review did not judge every checklist line."
    if any(line["ok"] is False and not line["code"] for line in lines):
        return "The AI visual review found a fault that matches no known reason code."
    return None


def run_checks(image: Path, shot_id: str, user: str) -> dict:
    """Check one render. Returns {colour, stray, logo, visual, codes, next, next_code, complete, reason,
    face_unchecked}.

    Each check goes through its own ledgered call; none is retried. A budget stop or a failed provider call
    ends the checks there (nothing more is spent) and the result is "flag" with a plain reason. Records one
    ledger `qa` event (by "studio"); moving the image is left to the caller.

    On a model shot whose cast member has no approved reference sheet, the AI is not asked about the face (there
    is nothing to compare it with). Its line is added after the verdict as "not checked" and face_unchecked is
    set, so the rest of the check still stands and its fixes run; the worker then hands the face to a person.
    """
    image = Path(image).resolve()
    entry = ledger.find(image)
    if entry is None or entry.get("shot_id") != shot_id:
        raise ValueError(f"{image.name} is not in out/log.jsonl as a render of {shot_id}")
    spec = json.loads(qa.spec_path(shot_id).read_text(encoding="utf-8"))
    shot = spec["shot"]
    on_model = bool(shot.get("model", {}).get("present"))
    target = resolve_colour(shot["garment"]["colour"])["hex"]
    decorated = any(d.get("render_mode") == "generate_and_verify" for d in shot.get("decoration", []))
    face_unchecked = on_model and not has_face_reference(spec)
    checklist = visual_checklist(shot_id, skip=(FACE,) if face_unchecked else ())
    face = face_line(shot_id) if face_unchecked else None
    brain = get_brain(user)  # a settings error stops here, before anything is spent

    found: dict = {"colour": None, "stray": None, "logo": None}
    stopped, step = None, "colour"
    try:
        found["colour"] = qa.colour_check(image, target, on_model)
        step = "stray"
        found["stray"] = qa.stray_text_check(image, qa.allowed_texts(spec))
        if decorated:
            step = "logo"
            found["logo"] = logo.verify(image)
    except ledger.BudgetError as e:
        stopped = f"The checks stopped at the {STEP_WORDS[step]} because the budget is reached: {e}"
    except ProviderError as e:
        stopped = f"The {STEP_WORDS[step]} failed and was not retried: {e}"

    if stopped:
        visual = {"lines": [], "error": "The AI visual review did not run because the checks stopped."}
    elif not checklist:
        visual = {"lines": [], "error": "There were no checklist lines for the AI visual review."}
    else:
        try:
            visual = visual_review(image, checklist, brain, user=user)
        except BrainError:  # already recorded in the ledger by the brain, with its detail
            visual = {"lines": [], "error": "The AI visual review did not answer."}

    reason = stopped or _incomplete(found["colour"], visual)
    complete = reason is None
    if face:  # after _incomplete: a face nobody could compare is for a person, not a reason the check is partial
        cast = (shot.get("model") or {}).get("cast")
        visual = {**visual, "lines": [*visual["lines"], {"text": face, "ok": None, "code": None,
                                                         "note": f"No approved reference photo of {cast} yet."}]}
    colour, stray, logo_result = found["colour"], found["stray"], found["logo"]
    codes = [c for c in ((colour or {}).get("code"), (stray or {}).get("code")) if c]
    codes += [c for d in (logo_result or {}).get("decorations", []) for c in d.get("codes", [])]
    codes += [line["code"] for line in visual["lines"] if line["ok"] is False and line["code"]]
    codes = list(dict.fromkeys(codes))
    action, code = qa.next_action(entry, codes) if complete else ("flag", None)  # never approve on a partial check

    qa.record_qa(entry, image, codes, colour or {}, reason or "", action, code,
                 result=None if complete or codes else "incomplete",
                 by="studio", user=user, complete=complete, stray=stray, visual=visual,
                 face_unchecked=face_unchecked)
    return {"colour": colour, "stray": stray, "logo": logo_result, "visual": visual, "codes": codes,
            "next": action, "next_code": code, "complete": complete, "reason": reason,
            "face_unchecked": face_unchecked}
