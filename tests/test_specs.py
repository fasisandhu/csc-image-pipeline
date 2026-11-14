import re

import pytest

from pipeline.compile import WORD_RANGE, CompileError, compile_spec, load_json
from pipeline.specs import build_spec, colour_id, find_anchor, load_recipes, shot_id_for

BASE = {"catalogue": "pullover-oversized", "colour": "washed-black", "gsm": 450,
        "recipe": "packshot-front", "decoration": "none"}
DESIGN = {"decoration": "design", "design": "oakmere-athletic", "technique": "3D puff embroidery",
          "placement": "centre chest", "size_cm": 22}
CATALOGUES = ("pullover-classic", "pullover-oversized", "zip")
SHOULDER_TERMS = {"dropped": r"\bdropped\b", "set-in": r"\bset-in\b"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def placements(recipe: dict, cat: dict) -> list[tuple[str, float]]:
    """(placement, max_cm) the dashboard offers for this recipe and style: in view, with a numeric max."""
    return [(p, cat["decoration_placements"][p]["max_cm"]) for p in recipe["placements"]
            if _num(cat["decoration_placements"].get(p, {}).get("max_cm"))]


def combos(scene_ids, cast, colour, gsms, design, technique):
    """Answers for every recipe x catalogue x {none, design} (each valid placement) x scene."""
    data = load_recipes()
    for rid, recipe in data["recipes"].items():
        for cat_id in CATALOGUES:
            cat = load_json(f"catalogue/{cat_id}.json")
            for gsm in gsms(cat):
                base = {"catalogue": cat_id, "colour": colour, "gsm": gsm, "recipe": rid}
                decos = [{"decoration": "none"}] if not recipe.get("requires_decoration") else []
                decos += [{"decoration": "design", "design": design, "technique": technique,
                           "placement": p, "size_cm": mx} for p, mx in placements(recipe, cat)]
                for deco in decos:
                    for scene in (scene_ids if recipe["model"] else [None]):
                        extra = {"cast": cast, "scene": scene} if recipe["model"] else {}
                        yield {**base, **deco, **extra}


def test_packshot_front_is_standard():
    assert compile_spec(build_spec(BASE)).tier == "standard"


def test_recipe_text_never_contradicts_catalogue():
    """Recipes are style-neutral: no shoulder or pocket word the catalogue does not say, and a decorated
    shot never asks to be free of all text or logos."""
    scenes = list(load_recipes()["scenes"])
    n = 0
    for a in combos(scenes, "Maya", "washed-black", lambda cat: [450], "oakmere-athletic", "3D puff embroidery"):
        cat = load_json(f"catalogue/{a['catalogue']}.json")
        c = compile_spec(build_spec(a))  # 4. compiles
        n += 1
        text = " ".join([c.prompts["paragraph"], c.prompts["flux_json"], *c.qa, *c.negatives]).lower()
        for term, rx in SHOULDER_TERMS.items():  # 1. shoulder (zip's is not set yet: neither term)
            if term != cat["shoulder"]:
                assert not re.search(rx, text), (a, term)
        if "kangaroo" in cat["construction"]["pockets"]:  # 2. pockets
            assert not re.search(r"\bsplit\b", text), a
        else:
            assert "kangaroo" not in text, a
        if a["decoration"] == "design":  # 3. a decorated shot is not "free of text or logos"
            assert "Free of: text or logos" not in c.qa, a
            assert "text or logos" not in c.negatives, a
    assert n > 50


def test_decorated_shot_avoids_only_other_text():
    spec = build_spec({**BASE, **DESIGN})
    assert "text or logos" not in spec["shot"]["avoid"]
    assert "any other text or logos" in spec["shot"]["avoid"]
    assert "text or logos" in build_spec(BASE)["shot"]["avoid"]  # a plain shot carries no text at all


def test_closeup_requires_a_design():
    recipe = load_recipes()["recipes"]["decoration-closeup"]
    assert recipe["requires_decoration"] is True
    with pytest.raises(CompileError, match="design"):
        build_spec({**BASE, "recipe": "decoration-closeup"})
    compile_spec(build_spec({**BASE, **DESIGN, "recipe": "decoration-closeup"}))


def test_flat_lay_offers_only_front_placements():
    assert "full back" not in load_recipes()["recipes"]["flat-lay"]["placements"]


def test_every_combination_inside_word_range():
    """CLAUDE.md: 120-220 words. Longest cast description, longest palette colour name, longest design
    text and technique; every GSM (its weight look changes the length)."""
    cast = max(load_json("brand/cast.json")["cast"].items(), key=lambda kv: len(kv[1]["description"].split()))[0]
    palette = load_json("brand/palette.json")["colours"]
    colour = max(palette, key=lambda k: len(palette[k]["name"].split()))
    designs = load_json("brand/designs/designs.json")["designs"]
    design = max(designs, key=lambda d: len((d.get("text") or "").split()))["id"]
    technique = max(load_json("catalogue/pullover-classic.json")["decoration_types"], key=lambda t: len(t.split()))
    scenes = list(load_recipes()["scenes"])
    counts, outside = [], []
    for a in combos(scenes, cast, colour, lambda cat: cat["gsm_options"], design, technique):
        c = compile_spec(build_spec(a))
        counts.append(c.word_count)
        if not WORD_RANGE[0] <= c.word_count <= WORD_RANGE[1]:
            outside.append((c.word_count, a))
    assert counts and not outside, outside[:5]


def test_design_raises_tier_and_fills_decoration():
    spec = build_spec({**BASE, **DESIGN})
    assert spec["shot"]["tier"] == "premium"
    assert spec["shot"]["decoration"][0] == {
        "type": "3D puff embroidery", "artwork": "brand/designs/oakmere-athletic.svg",
        "placement": "centre chest", "size_cm": 22, "render_mode": "generate_and_verify"}


def test_anchor_attached_only_when_approved_file_exists():
    assert find_anchor("pullover-oversized", "washed-black", 450) == "refs/anchors/pullover-oversized_washed-black_450_front.png"
    refs = build_spec(BASE)["set"]["reference_images"]
    assert [r["path"] for r in refs] == ["refs/anchors/pullover-oversized_washed-black_450_front.png"]
    assert build_spec({**BASE, "colour": "olive"})["set"]["reference_images"] == []


def test_size_over_placement_max_fails_compile():
    spec = build_spec({**BASE, **DESIGN, "recipe": "packshot-back", "placement": "full back", "size_cm": 34})
    with pytest.raises(CompileError, match="exceeds max 30"):
        compile_spec(spec)


def test_shot_id_stable_and_custom_colour_slugged():
    assert shot_id_for(BASE) == shot_id_for(dict(BASE)) == "pullover-oversized-washed-black-450-packshot-front"
    teal = {**BASE, "colour": {"name": "Teal Blue", "hex": "#1F6F78"}}
    assert shot_id_for(teal) == "pullover-oversized-teal-blue-450-packshot-front"
    assert shot_id_for({**BASE, **DESIGN}).endswith("-packshot-front-oakmere-athletic")


def test_on_model_takes_scene_and_recipe_pose():
    answers = {**BASE, "recipe": "on-model-front", "cast": "Maya", "scene": "warm-studio"}
    data = load_recipes()
    scene, recipe = data["scenes"]["warm-studio"], data["recipes"]["on-model-front"]
    spec = build_spec(answers)
    assert spec["shot"]["background"] == scene["background"]
    assert spec["shot"]["lighting"] == scene["lighting"]
    assert spec["shot"]["model"] == {"present": True, "cast": "Maya", "pose": recipe["pose"]}
    spec = build_spec(answers, creative={"pose": "walking", "background": "x"})
    assert spec["shot"]["model"]["pose"] == "walking" and spec["shot"]["background"] == "x"


def test_creative_cannot_override_packshot_background():
    assert build_spec(BASE, creative={"background": "x"})["shot"]["background"] != "x"


def test_recipes_have_model_flag_and_all_compile():
    data = load_recipes()
    assert all(isinstance(r["model"], bool) for r in data["recipes"].values())
    assert {"studio-grey", "warm-studio", "concrete-daylight", "warm-cafe"} <= set(data["scenes"])
    for name in data["recipes"]:
        a = {**BASE, "recipe": name, "cast": "Amir", "scene": "concrete-daylight"}
        if name == "decoration-closeup":
            a.update(DESIGN)
        compile_spec(build_spec(a))


def test_colour_id():
    assert colour_id("olive") == "olive"


OM_BACK = {**BASE, "recipe": "on-model-back", "cast": "Maya", "scene": "warm-studio"}


@pytest.mark.parametrize("bad", [
    {**BASE, **DESIGN, "design": "no-such-design"},
    {**BASE, "recipe": "nope"},
    {**OM_BACK, "scene": "nope"},
    {**OM_BACK, "cast": "Nobody"},
    {k: v for k, v in OM_BACK.items() if k != "cast"},
    {**BASE, "catalogue": "nope"},
])
def test_bad_answers_raise_compile_error(bad):
    with pytest.raises(CompileError):
        build_spec(bad)


def test_placement_must_suit_recipe_view():
    with pytest.raises(CompileError, match="on-model-back"):
        build_spec({**OM_BACK, **DESIGN, "placement": "centre chest"})
    spec = build_spec({**OM_BACK, **DESIGN, "placement": "full back", "size_cm": 30})
    compile_spec(spec)
