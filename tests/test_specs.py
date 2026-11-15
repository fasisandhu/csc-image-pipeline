import json
import re
import uuid

import pytest

from pipeline.compile import WORD_RANGE, CompileError, compile_spec, load_json
from pipeline import specs as specs_mod
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


RULE_FREE_MAX = 190  # without prompt rules: leaves 30 of CLAUDE.md's 220 words for the rules a person switches on


def test_every_combination_inside_word_range():
    """CLAUDE.md: 120-220 words, and at most RULE_FREE_MAX before any prompt rule is added. Longest cast
    description, longest palette colour name, longest design text and technique; every GSM (its weight look changes
    the length)."""
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
        if not WORD_RANGE[0] <= c.word_count <= RULE_FREE_MAX:
            outside.append((c.word_count, a))
    assert counts and not outside, outside[:5]
    # the scope check run when a rule is switched on finds the same longest prompt
    assert specs_mod.longest_prompt_words("all") == max(counts)


def test_longest_prompt_words_per_scope_and_with_rules(isolated_prompt_rules):
    on_model, packshot = specs_mod.longest_prompt_words("on_model"), specs_mod.longest_prompt_words("packshot")
    assert specs_mod.longest_prompt_words("all") == max(on_model, packshot)
    styles = [specs_mod.longest_prompt_words(c) for c in CATALOGUES]
    assert max(styles) == specs_mod.longest_prompt_words("all")
    rule = "The hood stands up in a full, rounded shape."  # 9 words, on packshots only
    isolated_prompt_rules.write_text(json.dumps({"rules": [
        {"status": "active", "applies_to": "packshot", "prompt_text": rule}]}), encoding="utf-8")
    assert specs_mod.longest_prompt_words("packshot") == packshot + 9
    assert specs_mod.longest_prompt_words("on_model") == on_model


def test_tests_never_read_the_live_prompt_rules():
    from pipeline import compile as compiler
    assert compiler.RULES_PATH != compiler.ROOT / "brand" / "prompt_rules.json"
    assert compiler.load_rules() == {"rules": []}


def test_design_raises_tier_and_fills_decoration():
    spec = build_spec({**BASE, **DESIGN})
    assert spec["shot"]["tier"] == "premium"
    assert spec["shot"]["decoration"][0] == {
        "type": "3D puff embroidery", "artwork": "brand/designs/oakmere-athletic.svg",
        "placement": "centre chest", "size_cm": 22, "render_mode": "generate_and_verify"}


def test_anchor_attached_only_when_approved_file_exists(monkeypatch):
    assert find_anchor("pullover-oversized", "washed-black", 450) == "refs/anchors/pullover-oversized_washed-black_450_front.png"
    refs = build_spec(BASE)["set"]["reference_images"]
    assert [r["path"] for r in refs] == ["refs/anchors/pullover-oversized_washed-black_450_front.png"]
    real = specs_mod.find_anchor
    monkeypatch.setattr(specs_mod, "find_anchor", lambda cat, colour, gsm: None if colour == "olive" else real(cat, colour, gsm))
    assert build_spec({**BASE, "colour": "olive"})["set"]["reference_images"] == []


def test_size_over_placement_max_fails_compile():
    spec = build_spec({**BASE, **DESIGN, "recipe": "packshot-back", "placement": "full back", "size_cm": 34})
    with pytest.raises(CompileError, match="exceeds max 30"):
        compile_spec(spec)


HASH = r"-[0-9a-f]{8}$"


def test_shot_id_stable_and_custom_colour_slugged():
    sid = shot_id_for(BASE)
    assert sid == shot_id_for(dict(BASE)) == shot_id_for(dict(reversed(list(BASE.items()))))
    assert re.fullmatch(r"pullover-oversized-washed-black-450-packshot-front" + HASH, sid)
    teal = {**BASE, "colour": {"name": "Teal Blue", "hex": "#1F6F78"}}
    assert re.fullmatch(r"pullover-oversized-teal-blue-1f6f78-450-packshot-front" + HASH, shot_id_for(teal))
    assert re.fullmatch(r".*-packshot-front-oakmere-athletic" + HASH, shot_id_for({**BASE, **DESIGN}))
    # the same shot from equivalent answers: gsm as text, size as 22.0, hex in lower case
    assert shot_id_for({**BASE, **DESIGN, "gsm": "450", "size_cm": 22.0}) == shot_id_for({**BASE, **DESIGN})
    assert shot_id_for({**teal, "colour": {"name": "Teal Blue", "hex": "#1f6f78"}}) == shot_id_for(teal)


def test_shot_id_ignores_answers_the_shot_does_not_use():
    # a hidden cast or scene on a packshot, or a design left over after choosing plain, is not the shot
    assert shot_id_for({**BASE, "cast": "Maya", "scene": "warm-cafe"}) == shot_id_for(BASE)
    assert shot_id_for({**BASE, **DESIGN, "decoration": "none"}) == shot_id_for(BASE)


def test_shot_id_collisions():
    om = {**BASE, "recipe": "on-model-front"}
    assert (shot_id_for({**om, "cast": "Maya", "scene": "warm-cafe"})
            != shot_id_for({**om, "cast": "Amir", "scene": "concrete-daylight"}))
    close = {**BASE, "recipe": "decoration-closeup", "decoration": "design", "design": "kilnworks-kw"}
    assert (shot_id_for({**close, "technique": "screen print", "placement": "left chest", "size_cm": 9})
            != shot_id_for({**close, "technique": "3D puff embroidery", "placement": "full back", "size_cm": 30}))
    t1, t2 = {"name": "Teal", "hex": "#1F6F78"}, {"name": "Teal", "hex": "#00CED1"}
    assert shot_id_for({**BASE, "colour": t1}) != shot_id_for({**BASE, "colour": t2})
    assert colour_id(t1) != colour_id(t2)
    assert find_anchor("pullover-oversized", t1, 450) is None
    assert build_spec({**BASE, "colour": t1})["shot"]["shot_id"] != build_spec({**BASE, "colour": t2})["shot"]["shot_id"]


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
    assert colour_id({"name": "Teal Blue", "hex": "#1F6F78"}) == "teal-blue-1f6f78"
    assert colour_id({"name": "!!!", "hex": "#1F6F78"}) == "1f6f78"  # nothing left to slug: the hex alone


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


# --- final review I1: a sketch never carries its garment into a final ----------------------------
LAYOUT_ROLE = ("a rough layout sketch; use only its framing and pose, and take every seam, the shoulder "
               "construction, drawcords and all other garment details from the text, not from this sketch")


def test_layout_role_takes_only_framing_and_pose():
    from pipeline.compile import REF_ROLES, ROOT
    assert REF_ROLES["layout"] == LAYOUT_ROLE
    sketch = ROOT / "out" / "drafts" / f"zz-test-{uuid.uuid4().hex[:8]}.png"  # git-ignored, removed below
    sketch.parent.mkdir(parents=True, exist_ok=True)
    sketch.write_bytes(b"\x89PNG\r\n\x1a\n-test")
    try:
        spec = build_spec({**BASE, **DESIGN, "recipe": "on-model-front", "cast": "Maya", "scene": "warm-studio"})
        with_sketch = compile_spec(spec, [sketch.relative_to(ROOT).as_posix()])
        without = compile_spec(spec)
    finally:
        sketch.unlink(missing_ok=True)
    n = next(i + 1 for i, r in enumerate(with_sketch.refs) if r["path"] == sketch.relative_to(ROOT).as_posix())
    assert with_sketch.refs[n - 1]["role"] == LAYOUT_ROLE
    assert f"Image {n} is {LAYOUT_ROLE}." in with_sketch.prompts["paragraph"]
    assert with_sketch.word_count == without.word_count  # reference-role lines do not count toward the limit


@pytest.mark.parametrize("catalogue, want", [
    ("pullover-oversized", "dropped shoulder seams"),
    ("pullover-classic", "set-in shoulder seams"),
    ("zip", None),  # its shoulder is still TODO in the catalogue: nothing is asked for
])
def test_shoulder_seams_are_a_must_show(catalogue, want):
    for recipe in ("packshot-front", "packshot-back", "flat-lay", "on-model-front", "on-model-back"):
        a = {**BASE, "catalogue": catalogue, "recipe": recipe, "cast": "Maya", "scene": "warm-studio"}
        must = build_spec(a)["shot"]["must_show"]
        shoulder = [m for m in must if "shoulder" in m]
        assert shoulder == ([want] if want else (["the shoulder seams"] if recipe == "on-model-front" else [])), \
            (recipe, must)
        if want:
            assert f"Shows: {want}" in compile_spec(build_spec(a)).qa
    # the close-up frames the decoration, not the shoulders
    closeup = build_spec({**BASE, **DESIGN, "catalogue": catalogue, "recipe": "decoration-closeup"})
    assert not any("shoulder" in m for m in closeup["shot"]["must_show"])
