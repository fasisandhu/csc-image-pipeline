"""Question set for the dashboard image builder.

studio/questions/<name>.json defines the sections; options, disabled states and limits
are computed here from catalogue/ and brand/ so facts live in one place.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.colour import hex_to_rgb, srgb_to_lab
from pipeline.compile import ROOT, load_json
from pipeline.specs import load_recipes

QDIR = Path(__file__).parent / "questions"
HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")
NO_MAX = "max size not set yet"
NEEDS_DESIGN = "a close-up needs a design"
# A custom colour name becomes part of file names (short paths after a paid call) and of every prompt of the shot
# (no prompt past 220 words): at most this many characters, and no more words than the longest palette name.
MAX_COLOUR_NAME = 24
NO_NAME = "Give the colour a name."
BAD_HEX = "The hex code is # and six characters from 0-9 and A-F, such as #1F6F78."


@dataclass
class Option:
    id: str
    label: str
    sub: str = ""
    disabled: bool = False
    reason: str = ""


@dataclass
class Section:
    id: str
    title: str
    hint: str
    kind: str  # "choice" | "colour" | "size"
    options: list[Option] = field(default_factory=list)
    value: object = None
    visible: bool = True
    required: bool = True
    max: float | None = None
    tip: str = ""  # plain-language help behind the section's "?"


def _catalogues() -> dict[str, dict]:
    out = {}
    for p in sorted((ROOT / "catalogue").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["id"]] = d
    return out


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _placements(cats: dict, chosen: str | None) -> dict[str, float | None]:
    """placement -> numeric max_cm (None if not set). No style chosen: what every style offers."""
    sets = ([cats[chosen]] if chosen in cats else list(cats.values()))
    sets = [{k: _num(v.get("max_cm")) for k, v in c["decoration_placements"].items()} for c in sets]
    if not sets:
        return {}
    return {k: (None if any(s[k] is None for s in sets) else min(s[k] for s in sets))
            for k in sets[0] if all(k in s for s in sets)}


def _common(cats: dict, chosen: str | None, key: str) -> list:
    lists = [cats[chosen][key]] if chosen in cats else [c[key] for c in cats.values()]
    if not lists:
        return []
    return [x for x in lists[0] if all(x in l for l in lists)]


def _tone(colour) -> str | None:
    try:
        hx = colour["hex"] if isinstance(colour, dict) else load_json("brand/palette.json")["colours"][colour]["hex"]
        if not HEX_RE.fullmatch(hx):
            return None
        return "dark" if float(srgb_to_lab(hex_to_rgb(hx))[0]) < 50 else "light"
    except Exception:
        return None


def _palette_words() -> tuple[int, str]:
    """The most words in a palette colour's name (brand/palette.json), and the first name that long."""
    names = [c["name"] for c in load_json("brand/palette.json")["colours"].values()]
    most = max(len(n.split()) for n in names)
    return most, next(n for n in names if len(n.split()) == most)


def custom_colour_problem(v) -> str | None:
    """Why a custom colour {"name", "hex"} cannot be used, in plain words for the page; None when it can."""
    if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v["name"].strip():
        return NO_NAME
    most, example = _palette_words()
    if len(v["name"].split()) > most or len(v["name"].strip()) > MAX_COLOUR_NAME:
        return (f"Keep the colour name to {most} words and {MAX_COLOUR_NAME} characters at most, "
                f"like “{example}”.")
    if not isinstance(v.get("hex"), str) or not HEX_RE.fullmatch(v["hex"]):
        return BAD_HEX
    return None


def _valid_custom_colour(v) -> bool:
    return custom_colour_problem(v) is None


def _visible(sec: dict, answers: dict, recipes: dict) -> bool:
    for key, want in (sec.get("show_if") or {}).items():
        if key == "recipe.model":
            r = recipes["recipes"].get(answers.get("recipe"))
            actual = bool(r["model"]) if r else None
        else:
            actual = answers.get(key)
        if actual != want:
            return False
    return True


def _options(sec: dict, answers: dict, cats: dict, recipes: dict) -> list[Option]:
    src, cat = sec["source"], answers.get("catalogue")
    if src == "static":
        opts = [Option(**o) for o in sec["options"]]
        recipe = recipes["recipes"].get(answers.get("recipe")) or {}
        if sec["id"] == "decoration" and recipe.get("requires_decoration"):
            for o in opts:
                if o.id == "none":
                    o.disabled, o.reason = True, NEEDS_DESIGN
        return opts
    if src == "catalogue":
        return [Option(k, c["style_name"]) for k, c in cats.items()]
    if src == "palette":
        return [Option(k, v["name"], v["hex"]) for k, v in load_json("brand/palette.json")["colours"].items()]
    if src == "gsm":
        common = _common(cats, cat, "gsm_options")
        if not common:
            every = sorted({g for c in cats.values() for g in c["gsm_options"]})
            return [Option(str(g), f"{g} GSM", disabled=True, reason="choose a style first") for g in every]
        looks = cats[cat]["weight_look"] if cat in cats else {}
        return [Option(str(g), f"{g} GSM", looks.get(str(g), "")) for g in common]
    if src == "recipes":
        return [Option(k, r["label"], "on-model" if r["model"] else "") for k, r in recipes["recipes"].items()]
    if src == "cast":
        return [Option(k, k, v.get("description", "")) for k, v in load_json("brand/cast.json")["cast"].items()]
    if src == "scenes":
        return [Option(k, s["label"]) for k, s in recipes["scenes"].items()]
    if src == "designs":
        tone = _tone(answers["colour"]) if answers.get("colour") else None
        out = []
        for d in load_json("brand/designs/designs.json")["designs"]:
            bad = tone is not None and d["for_garments"] != tone
            out.append(Option(d["id"], d["brand_name"], f"for {d['for_garments']} garments", bad,
                              f"made for {d['for_garments']} garments" if bad else ""))
        return out
    if src == "technique":
        return [Option(t, t) for t in _common(cats, cat, "decoration_types")]
    if src == "placement":
        recipe = recipes["recipes"].get(answers.get("recipe"))
        out = []
        for name, mx in _placements(cats, cat).items():
            reason = ""
            if mx is None:
                reason = NO_MAX
            elif recipe and name not in recipe["placements"]:
                reason = f"not visible in a {recipe['view']} view"
            out.append(Option(name, name, f"up to {mx:g} cm" if mx is not None else "", bool(reason), reason))
        return out
    return []


def _size_max(answers: dict, cats: dict) -> float | None:
    pl = _placements(cats, answers.get("catalogue"))
    if answers.get("placement") is not None:
        return pl.get(answers["placement"])
    # no placement yet: the largest numeric max among the placements offered
    # (with no style, any style's placements, so a brief can pre-fill the size)
    pool = pl if answers.get("catalogue") in cats else {
        k: _num(v.get("max_cm")) for c in cats.values() for k, v in c["decoration_placements"].items()}
    nums = [m for m in pool.values() if m is not None]
    return max(nums) if nums else None


def resolve(answers: dict, name: str = "images") -> list[Section]:
    spec = json.loads((QDIR / f"{name}.json").read_text(encoding="utf-8"))
    cats, recipes = _catalogues(), load_recipes()
    out = []
    for sec in spec["sections"]:
        s = Section(sec["id"], sec["title"], sec["hint"], sec["kind"], _options(sec, answers, cats, recipes),
                    answers.get(sec["id"]), _visible(sec, answers, recipes), sec.get("required", True),
                    tip=sec.get("tip", ""))
        if sec["kind"] == "size":
            s.max = _size_max(answers, cats)
        out.append(s)
    return out


def missing(answers: dict, name: str = "images") -> list[str]:
    return [s.id for s in resolve(answers, name)
            if s.visible and s.required and (s.value is None or s.value == "")]


def valid_value(section_id: str, value, answers: dict, name: str = "images") -> bool:
    sec = next((s for s in resolve(answers, name) if s.id == section_id), None)
    if sec is None:
        return False
    if sec.kind == "colour":
        if isinstance(value, dict):
            return _valid_custom_colour(value)
        return isinstance(value, str) and any(o.id == value for o in sec.options)
    if sec.kind == "size":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return sec.max is not None and 0 < value <= sec.max
    if value is None or isinstance(value, (dict, list)):
        return False
    return any(o.id == str(value) and not o.disabled for o in sec.options)
