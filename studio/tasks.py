"""Brain tasks. The brain may only choose option ids; every value is re-validated here."""
from __future__ import annotations

import json

from .brain import Brain
from .questions import missing, resolve, valid_value

_SCALAR = (str, int, float)


def _prep(sid: str, kind: str, value):
    """Drop non-scalars; size accepts numeric text."""
    if isinstance(value, bool) or not isinstance(value, _SCALAR):
        return None
    if kind == "size" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return value


def _finish(sid: str, value):
    if sid in ("gsm", "size_cm"):
        f = float(value)
        return int(f) if f == int(f) else f
    return value


def read_brief(text: str, brain: Brain, answers: dict | None = None, user: str = "owner") -> dict:
    answers = dict(answers or {})
    sections = resolve(answers)  # every section, hidden ones too: pre-filling is not display
    kinds = {s.id: s.kind for s in sections}
    lines = []
    for s in sections:
        if s.kind == "size":
            lines.append(f"- {s.id}: a number of cm, only if the text states it (largest allowed {s.max:g})"
                         if s.max else f"- {s.id}: a number of cm, only if the text states it")
        else:
            ids = [o.id for o in s.options if not o.disabled]
            if ids:
                extra = " (or a custom colour word in colour_word)" if s.kind == "colour" else ""
                lines.append(f"- {s.id}: one of {json.dumps(ids)}{extra}")
    props = {s.id: {"type": ["string", "null"]} for s in sections if s.kind != "size"}
    props["size_cm"] = {"type": ["string", "number", "null"]}
    props["colour_word"] = {"type": ["string", "null"]}
    schema = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    prompt = ("Read this hoodie image request and fill in only what the text states or clearly implies. "
              "Use null for anything not stated. Never invent a value: for each field give exactly one of "
              "the listed ids, or null. Also give colour_word, the colour word used in the text, or null.\n\n"
              "Fields:\n" + "\n".join(lines) + f"\n\nRequest:\n{text}")
    res = brain.ask_json("read_brief", prompt, schema, user=user)
    data = res.data or {}

    # pass 1: against the existing answers
    cand = {}
    for sid, kind in kinds.items():
        if sid in answers or sid not in data:
            continue
        v = _prep(sid, kind, data[sid])
        if v is not None and valid_value(sid, v, answers):
            cand[sid] = v
    # pass 2: against the merged answers (dependent values judged with the new catalogue/recipe)
    merged = {**answers, **cand}
    accepted = {sid: v for sid, v in cand.items() if valid_value(sid, v, merged)}
    new = {sid: _finish(sid, v) if sid in ("gsm", "size_cm") else v for sid, v in accepted.items()}
    final = {**answers, **new}

    word = data.get("colour_word")
    hint = word.strip() if isinstance(word, str) and word.strip() and "colour" not in final else None
    return {"answers": final, "unknown": missing(final), "colour_hint": hint,
            "provider": res.provider, "fallback": res.fallback}
