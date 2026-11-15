"""Dashboard answers -> shot spec (CLAUDE.md shape). Facts come from brand/ and catalogue/.

Recipes (brand/recipes.json) hold shot types and scenes; the compiler expands the spec.
"""
from __future__ import annotations

import hashlib
import json
import re

from .compile import REF_ROLES, ROOT, CompileError, is_missing, load_json, resolve_colour
from .designs import design_for

NO_TEXT, NO_OTHER_TEXT = "text or logos", "any other text or logos"
ANY_SHOULDER = "the shoulder seams"  # a recipe's style-neutral item, made exact by the catalogue's shoulder
SHOT_KEYS = ("catalogue", "colour", "gsm", "recipe", "decoration")
MODEL_KEYS = ("cast", "scene")
DESIGN_KEYS = ("design", "technique", "placement", "size_cm")


def load_recipes() -> dict:
    data = load_json("brand/recipes.json")
    return {"recipes": data["recipes"], "scenes": data["scenes"]}


def colour_id(colour: str | dict) -> str:
    """A palette id as is; a custom colour as "<slug of its name>-<hex, lower case, no #>" (just the hex
    when the name leaves no slug), so two custom colours with one name never share an id or an anchor."""
    if isinstance(colour, str):
        return colour
    slug = re.sub(r"[^a-z0-9]+", "-", colour["name"].lower()).strip("-")
    hex6 = colour["hex"].lstrip("#").lower()
    return f"{slug}-{hex6}" if slug else hex6


def find_anchor(catalogue: str, colour: str | dict, gsm: int) -> str | None:
    rel = f"refs/anchors/{catalogue}_{colour_id(colour)}_{gsm}_front.png"
    return rel if (ROOT / rel).exists() else None


def _decorated(answers: dict) -> bool:
    return answers.get("decoration") == "design"


def _avoid(items: list[str], decorated: bool) -> list[str]:
    """A decorated shot carries its design: it avoids only other text or logos."""
    return [NO_OTHER_TEXT if decorated and x == NO_TEXT else x for x in items]


def _must_show(recipe: dict, cat: dict) -> list[str]:
    """The recipe's must-show items plus the catalogue's shoulder seams ("dropped shoulder seams"), in place of a
    generic "the shoulder seams" item, so a final draws the style's own shoulder (final review I1b). Nothing is
    added while the shoulder is unset (TODO), or on a recipe whose framing leaves the shoulders out."""
    must = list(recipe["must_show"])
    shoulder = cat.get("shoulder")
    if is_missing(shoulder) or not recipe.get("shows_shoulders", True):
        return must
    seams = f"{shoulder} shoulder seams"
    return [seams if m == ANY_SHOULDER else m for m in must] if ANY_SHOULDER in must else must + [seams]


def _number(v):
    """450 for "450", 450.0 or 450; other values unchanged."""
    if isinstance(v, str) and re.fullmatch(r"\d+(\.\d+)?", v.strip()):
        v = float(v)
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _facts(answers: dict) -> dict:
    """The fact-bearing answers this shot uses, one spelling each: cast and scene only with a model,
    design fields only when decorated, so a hidden or left-over answer never changes the id."""
    recipe = load_recipes()["recipes"].get(answers.get("recipe")) or {}
    keys = list(SHOT_KEYS)
    if recipe.get("model", True):
        keys += MODEL_KEYS
    if _decorated(answers):
        keys += DESIGN_KEYS
    out = {k: answers[k] for k in keys if k in answers}
    for k in ("gsm", "size_cm"):
        if k in out:
            out[k] = _number(out[k])
    if isinstance(out.get("colour"), dict):
        out["colour"] = {"name": out["colour"].get("name"), "hex": str(out["colour"].get("hex", "")).lower()}
    return out


def shot_id_for(answers: dict) -> str:
    """Readable prefix, the design when decorated, then 8 hex of a sha256 over the fact-bearing answers.
    Different shots never share an id (qa.next_action counts finals per shot_id); the same answers
    always give the same one."""
    sid = f"{answers['catalogue']}-{colour_id(answers['colour'])}-{_number(answers['gsm'])}-{answers['recipe']}"
    if _decorated(answers):
        sid += f"-{answers['design']}"
    canon = json.dumps(_facts(answers), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{sid}-{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:8]}"


def build_spec(answers: dict, creative: dict | None = None) -> dict:
    """creative may override pose, and background on on-model shots only."""
    creative = creative or {}
    recipes = load_recipes()
    if answers.get("recipe") not in recipes["recipes"]:
        raise CompileError(f"unknown recipe {answers.get('recipe')!r}")
    recipe = recipes["recipes"][answers["recipe"]]
    if not (ROOT / "catalogue" / f"{answers.get('catalogue')}.json").exists():
        raise CompileError(f"unknown catalogue entry {answers.get('catalogue')!r}")
    cat = load_json(f"catalogue/{answers['catalogue']}.json")
    brand = load_json("brand/brand.json")
    resolve_colour(answers["colour"])  # fail early on an unknown colour

    background, lighting = recipe["background"], recipe["lighting"]
    model = {"present": False, "cast": "", "pose": ""}
    if recipe["model"]:
        if answers.get("scene") not in recipes["scenes"]:
            raise CompileError(f"unknown scene {answers.get('scene')!r}")
        if answers.get("cast") not in load_json("brand/cast.json")["cast"]:
            raise CompileError(f"unknown or missing cast {answers.get('cast')!r}")
        scene = recipes["scenes"][answers["scene"]]
        background = creative.get("background") or scene["background"]
        lighting = scene["lighting"]
        model = {"present": True, "cast": answers["cast"], "pose": creative.get("pose") or recipe["pose"]}

    decoration = []
    if recipe.get("requires_decoration") and not _decorated(answers):
        raise CompileError(f"recipe {answers['recipe']} needs a design: a close-up has nothing to show without one")
    if _decorated(answers):
        design = design_for(answers.get("design") or "")
        if design is None:
            raise CompileError(f"unknown design {answers.get('design')!r}")
        if answers.get("placement") not in recipe["placements"]:
            raise CompileError(f"placement {answers.get('placement')!r} not available on recipe {answers['recipe']}")
        decoration.append({"type": answers["technique"], "artwork": design["file"],
                           "placement": answers["placement"], "size_cm": answers["size_cm"],
                           "render_mode": "generate_and_verify"})

    anchor = find_anchor(answers["catalogue"], answers["colour"], answers["gsm"])
    refs = [{"path": anchor, "role": REF_ROLES["anchor"]}] if anchor else []
    sid = shot_id_for(answers)
    return {
        "set": {"set_id": sid, "style_block": brand["style_block"], "reference_images": refs,
                "aspect_ratio": recipe["aspect"], "resolution": recipe["resolution"]},
        "shot": {
            "shot_id": sid,
            "purpose": recipe["purpose"],
            "tier": "premium" if _decorated(answers) else recipe["tier"],
            "garment": {"catalogue": answers["catalogue"], "colour": answers["colour"], "gsm": answers["gsm"]},
            "decoration": decoration,
            "labels": {"neck": "", "hang_tag": ""},
            "camera": dict(recipe["camera"]),
            "lighting": lighting,
            "background": background,
            "model": model,
            "must_show": _must_show(recipe, cat),
            "avoid": _avoid(recipe["avoid"], _decorated(answers)),
        },
    }
