"""Review items, the library and prompt rules: what the Review and Library tabs show, and the files a verdict writes.

Read from the jobs database, each final's review steps and out/log.jsonl (callers read the log once per request with
ledger.snapshot). A job's images are its final, then each step's new output; a check that moved its render to
out/reject swaps in the moved path. The last one is the job's latest image: every verdict, change request, export
and reference packshot acts on it.

Everything a browser gets passes through app.public_text, and an image path is sent only when the files route serves
it (app.servable), so no path outside the served folders and no setting's name ever leaves the server.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Callable

from PIL import Image

from pipeline import compile as compiler
from pipeline import finish, ledger, qa, repair
from pipeline.compile import ROOT, CompileError, compile_spec, load_json, resolve_colour
from pipeline.specs import colour_id, load_recipes

from .tasks import rule_scopes
from .worker import STEP_WORDS, _composite_made

ANCHORS_DIR = ROOT / "refs" / "anchors"  # where find_anchor looks; a test points this elsewhere
ANCHORS_REL = "refs/anchors"             # the same folder as recorded in the ledger
ANCHOR_LOCK = threading.Lock()  # a reference packshot is written by one request at a time: "exists" holds meanwhile
RULES_LOCK = threading.Lock()   # brand/prompt_rules.json: one read-modify-write at a time in this server
ACTIONABLE = ("ready", "needs_person")  # where a person's verdict or change request starts from
FINALS_DIR = ROOT / "out" / "finals"
FRONT = "packshot-front"

# Plain words for the reason codes in brand/repairs.json: "Fixed: the drawcords", "Fix the drawcords".
CODE_WORDS = {
    "drawcord_shape": "the drawcords", "drawcord_count": "the number of drawcords", "rib_width": "the cuffs and hem",
    "shoulder_seam": "the shoulder seams", "pocket_shape": "the pocket", "hood_shape": "the hood", "zip": "the zip",
    "hands": "the hands", "unintended_text": "stray text", "logo_text": "the logo text",
    "logo_shape": "the logo shape", "logo_colour": "the logo colours", "logo_size": "the logo size",
    "drape_too_thin": "the fabric weight", "face": "the face", "framing": "the framing",
    "colour_wrong": "the colour", "colour_drift": "the colour shade",
}
CHANGE_LABELS = {"unintended_text": "Remove stray text"}
KIND_LABELS = {"packshot": "Packshot", "on_model": "On model"}
SCOPE_LABELS = {"all": "All images", "packshot": "Packshots", "on_model": "On-model"}

NOT_PLAIN = "Only a plain front packshot, with no design, can become a reference packshot."
FROM_SKETCH = ("This image was built on a sketch, so it cannot become a reference packshot: "
               "a reference packshot's garment comes only from our catalogue.")
FROM_COMPOSITE = "A logo placement cannot become a reference packshot."
NO_RECORD = "This image has no generation record, so it cannot become a reference packshot."
SET_ASIDE = "This image was set aside by the checks, so it cannot become a reference packshot."
LOGO_NOT_CLEARED = "The logo on this image did not pass its check. Ask for a change, or reject it."


def _public(text):
    from .app import public_text  # lazy: app imports this module
    return public_text(text) if isinstance(text, str) else text


def served(path: str | None) -> str | None:
    """`path` if the files route would serve it to the browser, else None."""
    from .app import servable  # lazy: app imports this module
    return path if path and servable(path) is not None else None


def code_words(code: str | None) -> str | None:
    return None if not code else CODE_WORDS.get(code, code.replace("_", " "))


def change_label(code: str) -> str:
    return CHANGE_LABELS.get(code) or f"Fix {code_words(code)}"


# --- a job's images ------------------------------------------------------------------------------
def chain_images(job: dict, steps: list[dict]) -> list[str]:
    """The job's images, oldest first: the final, then each finished step's new output. A check's output is its
    input, or the input moved to out/reject, which takes the input's place."""
    images = list((job.get("result") or {}).get("files") or [])[:1]
    for s in steps:
        if s["status"] != "done" or not s["output"]:
            continue
        if s["kind"] == "check":
            images = [s["output"] if x == s["input"] else x for x in images]
        elif s["output"] not in images:
            images.append(s["output"])
    return images


def latest_image(job: dict, steps: list[dict]) -> str | None:
    images = chain_images(job, steps)
    return images[-1] if images else None


def _root(entry: dict | None) -> str | None:
    return (entry.get("root") or entry.get("file")) if entry else None


def limits_left(entry: dict | None, shot_id: str | None) -> dict:
    """Repairs left on the latest render and new images left for the shot, counted from the ledger exactly as
    qa.next_action counts them. A render with no generation record cannot be repaired."""
    lim = load_json("brand/repairs.json")["limits"]
    root = _root(entry)
    repairs = lim["repairs_per_render"] - len(repair.repairs_made(root)) if root else 0
    return {"repairs": max(0, repairs),
            "regenerations": max(0, lim["regenerations_per_shot"] - qa.regenerations_used(shot_id))}


def change_codes(spec: dict) -> list[str]:
    """The repair codes a person may ask for on this shot: logo codes only for a design the model renders, hands only
    with a model, the zip only on a zip style."""
    shot = spec.get("shot") or {}
    rendered = any(d.get("render_mode") == "generate_and_verify" for d in shot.get("decoration") or [])
    model = bool((shot.get("model") or {}).get("present"))
    try:
        cat = load_json(f"catalogue/{(shot.get('garment') or {}).get('catalogue')}.json")
        has_zip = bool(cat["construction"].get("zip"))
    except (OSError, KeyError, ValueError):
        has_zip = False
    codes = []
    for code, rule in load_json("brand/repairs.json")["codes"].items():
        if rule.get("fix") != "repair":
            continue
        if (code.startswith("logo_") and not rendered) or (code == "hands" and not model) or \
                (code == "zip" and not has_zip):
            continue
        codes.append(code)
    return codes


# --- reference packshots -------------------------------------------------------------------------
def anchor_name(spec: dict) -> str:
    g = spec["shot"]["garment"]
    return f"{g['catalogue']}_{colour_id(g['colour'])}_{int(g['gsm'])}_front.png"


def in_finals(path: str | None) -> bool:
    """Whether a repo-relative image path is under out/finals (where a render the checks set aside is not)."""
    return bool(path) and (ROOT / path).resolve().is_relative_to(FINALS_DIR.resolve())


def anchor_refusal(job: dict, latest: str | None, entry: dict | None) -> str | None:
    """Why this job's latest image cannot become its colour's reference packshot, in plain words; None if it can.
    Only a plain packshot-front final made without a sketch, from a logged render in out/finals (not one the checks
    set aside) that is no logo placement. Every later shot of the colour copies it as "the exact garment"."""
    answers = job.get("answers") or {}
    shot = (job.get("spec") or {}).get("shot") or {}
    if answers.get("recipe") != FRONT or answers.get("decoration") != "none" or shot.get("decoration"):
        return NOT_PLAIN
    if job.get("layout"):
        return FROM_SKETCH
    if not in_finals(latest):
        return SET_ASIDE
    if entry is None:
        return NO_RECORD
    if _composite_made(entry):
        return FROM_COMPOSITE
    return None


def anchor_exists(spec: dict) -> bool:
    return (ANCHORS_DIR / anchor_name(spec)).exists()


def publish_anchor(image: Path, name: str, replace: bool, record: Callable[[bool], None]) -> bool | None:
    """Save `image` as PNG at ANCHORS_DIR/name and run record(replaced) to log it, as one step under ANCHOR_LOCK (so
    the log follows the order of the file writes). The PNG goes to a temporary file, then replaces the target, so
    find_anchor never sees half a file. If anything fails, the earlier file is put back (from a backup), or the new
    one removed when there was none, and the error raised. Returns whether an existing file was replaced; None
    (nothing written) when one exists and `replace` is not set."""
    target = ANCHORS_DIR / name
    with ANCHOR_LOCK:
        existed = target.exists()
        if existed and not replace:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        tmp, backup = target.with_name(f".{name}.{tag}.tmp"), target.with_name(f".{name}.{tag}.bak")
        written = keep_backup = False
        try:
            if existed:
                shutil.copy2(target, backup)
            with Image.open(image) as im:
                im.convert("RGB").save(tmp, "PNG")
            os.replace(tmp, target)
            written = True
            record(existed)
        except BaseException:
            try:
                if written and existed:
                    os.replace(backup, target)
                elif written:
                    target.unlink(missing_ok=True)
            except OSError:  # could not put it back: the earlier file stays beside it for a person to restore
                keep_backup = True
            raise
        finally:
            tmp.unlink(missing_ok=True)
            if not keep_backup:
                backup.unlink(missing_ok=True)
    return existed


def current_anchor_source(spec: dict) -> str | None:
    """The render the colour's reference packshot file was last written from (its latest anchor_approved event),
    while that file exists."""
    name = anchor_name(spec)
    if not (ANCHORS_DIR / name).exists():
        return None
    events = [e for e in ledger.entries() if e.get("event") == "anchor_approved"
              and e.get("anchor") == f"{ANCHORS_REL}/{name}"]
    return events[-1].get("file") if events else None


# --- the logo gate -------------------------------------------------------------------------------
def latest_check(steps: list[dict], latest: str | None) -> dict | None:
    """The newest finished check of the latest image (its input, or the render it moved to out/reject)."""
    done = [s for s in steps if s["kind"] == "check" and s["status"] == "done" and s.get("result")]
    return next((s for s in reversed(done) if latest and latest in (s["input"], s["output"])), None)


def logo_cleared(spec: dict, steps: list[dict], latest: str | None) -> bool:
    """Whether the latest image's logo may be approved or exported (CLAUDE.md: every logo either passes verification
    or is composited from the real vector file). True when the shot has no design the model rendered
    (generate_and_verify), when the latest image is a studio composite of the real artwork, or when the latest
    image's newest finished check verified every rendered design: found, with no reason codes. A logo check that
    never ran (a budget stop, an older image) does not clear it. Call inside ledger.snapshot()."""
    rendered = [d for d in (spec.get("shot") or {}).get("decoration") or []
                if d.get("render_mode") == "generate_and_verify"]
    if not rendered:
        return True
    if not latest:
        return False
    entry = ledger.find(latest)
    if entry is not None and _composite_made(entry):
        return True
    check = latest_check(steps, latest)
    decos = (((check or {}).get("result") or {}).get("logo") or {}).get("decorations") or []
    return len(decos) == len(rendered) and all(d.get("found") and not d.get("codes") for d in decos)


# --- checks in plain words -----------------------------------------------------------------------
def _colour_words(colour: dict | None, tolerance: float) -> dict | None:
    if colour is None:
        return None
    if colour.get("de2000") is None:
        return {"ok": None, "difference": None, "limit": tolerance,
                "words": "The garment colour could not be measured; please look."}
    de = round(float(colour["de2000"]), 1)
    return {"ok": colour.get("result") == "pass", "difference": de, "limit": tolerance,
            "words": f"Colour difference {de:g} (limit {tolerance:g})"}


def _text_words(stray: dict | None) -> dict | None:
    if stray is None:
        return None
    found = [_public(t) for t in stray.get("stray") or []]
    words = "No stray text" if not found else "Text that should not be there: " + ", ".join(f"“{t}”" for t in found)
    return {"ok": stray.get("result") == "pass", "found": found, "words": words}


def _logo_words(logo: dict | None) -> list[dict]:
    out = []
    for d in (logo or {}).get("decorations") or []:
        faults = [code_words(c) for c in d.get("codes") or []]
        if not d.get("found"):
            words = f"No logo found on the {d.get('placement')}"
        else:
            words = "The logo matches" if not faults else "The logo needs a look: " + ", ".join(faults)
        out.append({"placement": d.get("placement"), "ok": bool(d.get("found")) and not faults,
                    "box": d.get("box"), "words": words})
    return out


def checks_summary(steps: list[dict], latest: str | None) -> dict | None:
    """The latest image's check in plain words, or None while it has none: the colour, stray text, logo and each
    AI-review line, plus the faults earlier checks in this chain found that this one no longer finds ("fixed")."""
    done = [s for s in steps if s["kind"] == "check" and s["status"] == "done" and s.get("result")]
    last = latest_check(steps, latest)
    if last is None:
        return None
    r = last["result"]
    tolerance = load_json("brand/qa_checklist.json")["colour"]["tolerance"]
    visual = r.get("visual") or {}
    codes = set(r.get("codes") or [])
    before = [c for s in done if s is not last for c in (s["result"].get("codes") or [])]
    return {
        "complete": bool(r.get("complete")),
        "reason": _public(r.get("reason")),
        "colour": _colour_words(r.get("colour"), tolerance),
        "text": _text_words(r.get("stray")),
        "logo": _logo_words(r.get("logo")),
        "lines": [{"text": _public(line.get("text")), "ok": line.get("ok"), "note": _public(line.get("note") or ""),
                   "fault": code_words(line.get("code"))} for line in visual.get("lines") or []],
        "visual_error": _public(visual.get("error")),
        "fixed": [code_words(c) for c in dict.fromkeys(before) if c not in codes],
    }


# --- review items --------------------------------------------------------------------------------
def shot_words(job: dict) -> str:
    """The shot in plain words: recipe label, then the garment as its alt text says it, and the cast member."""
    answers, spec = job.get("answers") or {}, job.get("spec") or {}
    label = (load_recipes()["recipes"].get(answers.get("recipe")) or {}).get("label")
    try:
        what = finish.alt_text(spec)
    except (KeyError, TypeError, ValueError, OSError, CompileError):
        what = ""
    cast = ((spec.get("shot") or {}).get("model") or {})
    if cast.get("present") and cast.get("cast"):
        what = f"{what} ({cast['cast']})" if what else cast["cast"]
    return _public(": ".join(p for p in (label, what) if p))


def cost_so_far(job: dict, steps: list[dict]) -> float:
    """The final's own cost plus each review step's recorded cost."""
    return round(float((job.get("result") or {}).get("cost_usd") or 0.0)
                 + sum(float(s.get("cost_usd") or 0.0) for s in steps), 4)


def _step(s: dict) -> dict:
    """A review step as the browser gets it. file: its output's file name only (never a folder), kept when a check
    moved that render to out/reject (its output path is then gone, so `output` is None); the page names each image
    of the chain by the step whose file it is."""
    return {"id": s["id"], "kind": s["kind"], "label": STEP_WORDS.get(s["kind"], s["kind"]), "status": s["status"],
            "code": s["code"], "fault": code_words(s["code"]), "output": served(s["output"]),
            "file": Path(s["output"]).name if s["output"] else None,
            "cost_usd": round(float(s.get("cost_usd") or 0.0), 4), "error": _public(s.get("error"))}


def review_item(job: dict, steps: list[dict]) -> dict:
    """One final job as the Review tab shows it. Call inside ledger.snapshot()."""
    from .app import caption_for  # lazy: app imports this module
    spec = job.get("spec") or {}
    shot_id = (spec.get("shot") or {}).get("shot_id")
    latest = latest_image(job, steps)
    entry = ledger.find(latest) if latest else None
    review, running = job.get("review"), any(s["status"] in ("queued", "running") for s in steps)
    limits = limits_left(entry, shot_id)
    refusal = anchor_refusal(job, latest, entry)
    open_ = review in ACTIONABLE
    codes = change_codes(spec)
    logo_ok = logo_cleared(spec, steps, latest)
    return {
        "id": job["id"], "user": job["user"], "created": job["created"], "updated": job["updated"],
        "answers": job.get("answers") or {}, "shot_id": shot_id, "words": shot_words(job),
        "caption": caption_for(job.get("answers")), "layout": served(job.get("layout")),
        "review": review, "reason": _public(job.get("review_reason")),
        # why a plain front packshot open to a verdict has no "Approve as reference packshot" (None for other shots)
        "anchor_note": refusal if open_ and refusal not in (None, NOT_PLAIN) else None,
        # why an image open to a verdict has no "Approve": its logo did not pass (or never had) its check
        "logo_note": LOGO_NOT_CLEARED if open_ and not logo_ok else None,
        "latest": served(latest), "images": [served(i) for i in chain_images(job, steps)],
        "steps": [_step(s) for s in steps],
        "checks": checks_summary(steps, latest),
        "limits_left": limits,
        "cost_usd": cost_so_far(job, steps),
        "actions": {
            "approve": open_ and logo_ok, "note_required": review == "needs_person", "reject": open_,
            "anchor": open_ and refusal is None and logo_ok,
            "anchor_exists": refusal is None and anchor_exists(spec),
            "change": open_ and not running and entry is not None and limits["repairs"] > 0 and bool(codes),
            "check": (review in (None, "needs_person") and not running and bool(served(latest))
                      and in_finals(latest)),
        },
        "changes": [{"code": c, "label": change_label(c)} for c in codes],
    }


# --- library -------------------------------------------------------------------------------------
def approval(latest: str | None) -> dict | None:
    """The image's approval: its latest `approved` ledger event, unless a rejection was recorded after it."""
    if not latest:
        return None
    name = Path(latest).name
    verdicts = [e for e in ledger.entries() if e.get("event") in ("approved", "rejected")
                and Path(e.get("file", "")).name == name]
    return verdicts[-1] if verdicts and verdicts[-1]["event"] == "approved" else None


def _facts(job: dict) -> dict:
    """The library's filter values for a job: style, colour, technique, placement, model and kind."""
    answers, shot = job.get("answers") or {}, (job.get("spec") or {}).get("shot") or {}
    g, model = shot.get("garment") or {}, shot.get("model") or {}
    deco = (shot.get("decoration") or [{}])[0]
    on_model = bool(model.get("present"))
    colour = g.get("colour", answers.get("colour"))
    try:
        colour_name = resolve_colour(colour)["name"] if colour else None
    except (CompileError, KeyError, TypeError):
        colour_name = None
    return {"style": g.get("catalogue"), "colour": colour_id(colour) if colour else None, "colour_name": colour_name,
            "technique": deco.get("type"), "placement": deco.get("placement"),
            "model": model.get("cast") if on_model else None, "kind": "on_model" if on_model else "packshot"}


def _prompt(job: dict) -> str | None:
    """The shot's prompt as compile_spec writes it from its spec (out/specs, else the job's own copy)."""
    spec = job.get("spec") or {}
    shot_id = (spec.get("shot") or {}).get("shot_id")
    try:
        spec = json.loads(qa.spec_path(shot_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, TypeError):
        pass
    try:
        layout = [job["layout"]] if job.get("layout") else []
        return _public(compile_spec(spec, layout).prompts["paragraph"])
    except (CompileError, KeyError, TypeError, ValueError, OSError):
        return None


def export_record(latest: str) -> dict | None:
    """The latest export of this image: its web files and approval PDF (only those the files route serves), alt
    text and caption."""
    events = ledger.events(latest, "exported")
    if not events:
        return None
    e = events[-1]
    return {"files": [{"path": p, "width": _width(p)} for p in e.get("files") or [] if served(p)],
            "pdf": served(e.get("pdf")), "alt": _public(e.get("alt")), "caption": _public(e.get("caption"))}


def _width(path: str) -> int | None:
    m = re.search(r"-(\d+)\.webp$", path)
    return int(m.group(1)) if m else None


def _is_reference(job: dict, latest: str) -> bool:
    """Whether this image is what its colour's reference packshot file was written from (a replaced one is not)."""
    try:
        source = current_anchor_source(job["spec"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(source) and Path(source).name == Path(latest).name


def library_item(job: dict, steps: list[dict]) -> dict | None:
    """A job whose latest image is approved (an `approved` ledger event, no rejection after it), with its record;
    None for any other job. Call inside ledger.snapshot()."""
    from .app import caption_for  # lazy: app imports this module
    latest = latest_image(job, steps)
    approved = approval(latest)
    if approved is None:
        return None
    entry = ledger.find(latest) or {}
    facts = _facts(job)
    return {
        "id": job["id"], "shot_id": ((job.get("spec") or {}).get("shot") or {}).get("shot_id"),
        "words": shot_words(job), "caption": caption_for(job.get("answers")), "image": served(latest),
        "purpose": (job.get("answers") or {}).get("purpose"),  # "website" or "client": a client mockup gets a PDF
        **facts, "ai_model": entry.get("model"),
        "reference": _is_reference(job, latest),
        "cost_usd": cost_so_far(job, steps),
        "approved_by": _public(approved.get("by")), "approved_at": approved.get("ts"),
        "prompt": _prompt(job), "export": export_record(latest),
    }


def style_label(style: str) -> str:
    try:
        return load_json(f"catalogue/{style}.json")["style_name"]
    except (OSError, KeyError, ValueError):
        return style


def facets(items: list[dict]) -> dict:
    """The filter values present among the library's items, each with a plain label (a custom colour's own name)."""
    out = {}
    for key in ("style", "colour", "technique", "placement", "model", "kind"):
        values = sorted({i[key] for i in items if i.get(key)})
        labels = {"style": style_label, "kind": lambda v: KIND_LABELS.get(v, v),
                  "colour": lambda v: next((i["colour_name"] for i in items
                                            if i["colour"] == v and i.get("colour_name")), v)}
        out[key] = [{"value": v, "label": labels.get(key, str)(v)} for v in values]
    return out


# --- prompt rules --------------------------------------------------------------------------------
def scope_options() -> list[dict]:
    out = []
    for s in rule_scopes():
        out.append({"value": s, "label": SCOPE_LABELS.get(s) or style_label(s)})
    return out


def public_rule(index: int, rule: dict) -> dict:
    return {"index": index, "status": rule.get("status"), "note": _public(rule.get("note") or ""),
            "prompt_text": _public(rule.get("prompt_text") or ""), "applies_to": rule.get("applies_to"),
            "from_shot": rule.get("from_shot"), "date": rule.get("date"),
            "activated_by": rule.get("activated_by"), "dismissed_by": rule.get("dismissed_by")}


def store_draft(index: int, note: str, draft: dict) -> dict | None:
    """Put the brain's draft on pending rule `index`, if that rule is still the untouched pending rule of `note`.
    Returns the rule as stored."""
    with RULES_LOCK:
        doc = compiler.load_rules()
        rules = doc["rules"]
        if not 0 <= index < len(rules):
            return None
        rule = rules[index]
        if rule.get("status") == "pending" and rule.get("note") == note and not rule.get("prompt_text"):
            rule.update(prompt_text=draft["prompt_text"], applies_to=draft["applies_to"])
            compiler.save_rules(doc)
        return rule
