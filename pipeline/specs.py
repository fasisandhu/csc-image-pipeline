"""Dashboard answers -> shot spec (CLAUDE.md shape). Facts come from brand/ and catalogue/.

Recipes (brand/recipes.json) hold shot types and scenes; the compiler expands the spec.
"""
from __future__ import annotations

import re

from .compile import REF_ROLES, ROOT, load_json, resolve_colour
from .designs import design_for


def load_recipes() -> dict:
    data = load_json("brand/recipes.json")
    return {"recipes": data["recipes"], "scenes": data["scenes"]}


def colour_id(colour: str | dict) -> str:
    """A palette id as is, or the slug of a custom colour name."""
    if isinstance(colour, str):
        return colour
    return re.sub(r"[^a-z0-9]+", "-", colour["name"].lower()).strip("-")


def find_anchor(catalogue: str, colour: str | dict, gsm: int) -> str | None:
    rel = f"refs/anchors/{catalogue}_{colour_id(colour)}_{gsm}_front.png"
    return rel if (ROOT / rel).exists() else None


def _decorated(answers: dict) -> bool:
    return answers.get("decoration") == "design"


def shot_id_for(answers: dict) -> str:
    sid = f"{answers['catalogue']}-{colour_id(answers['colour'])}-{answers['gsm']}-{answers['recipe']}"
    return f"{sid}-{answers['design']}" if _decorated(answers) else sid


def build_spec(answers: dict, creative: dict | None = None) -> dict:
    """creative may override pose, and background on on-model shots only."""
    creative = creative or {}
    recipes = load_recipes()
    recipe = recipes["recipes"][answers["recipe"]]
    brand = load_json("brand/brand.json")
    resolve_colour(answers["colour"])  # fail early on an unknown colour

    background, lighting = recipe["background"], recipe["lighting"]
    model = {"present": False, "cast": "", "pose": ""}
    if recipe["model"]:
        scene = recipes["scenes"][answers["scene"]]
        background = creative.get("background") or scene["background"]
        lighting = scene["lighting"]
        model = {"present": True, "cast": answers["cast"], "pose": creative.get("pose") or recipe["pose"]}

    decoration = []
    if _decorated(answers):
        design = design_for(answers["design"])
        if design is None:
            raise ValueError(f"unknown design {answers['design']!r}")
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
            "must_show": list(recipe["must_show"]),
            "avoid": list(recipe["avoid"]),
        },
    }
