import json
from pathlib import Path

import pytest

from pipeline.compile import CompileError, compile_spec
from pipeline.specs import build_spec, colour_id, find_anchor, load_recipes, shot_id_for

BASE = {"catalogue": "pullover-oversized", "colour": "washed-black", "gsm": 450,
        "recipe": "packshot-front", "decoration": "none"}
DESIGN = {"decoration": "design", "design": "oakmere-athletic", "technique": "3D puff embroidery",
          "placement": "centre chest", "size_cm": 22}


def ref_spec(name):
    return json.loads(Path(f"specs/{name}.json").read_text())["shot"]


def test_packshot_front_matches_existing_anchor_spec():
    spec = build_spec(BASE)
    ref = ref_spec("ovs-wblack-450-anchor-front")
    for k in ("purpose", "camera", "lighting", "background", "must_show"):
        assert spec["shot"][k] == ref[k]
    assert compile_spec(spec).tier == "standard"


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
    ref = ref_spec("ovs-wblack-450-maya-front")
    spec = build_spec(answers)
    assert spec["shot"]["background"] == ref["background"]
    assert spec["shot"]["lighting"] == ref["lighting"]
    assert spec["shot"]["model"] == {"present": True, "cast": "Maya", "pose": ref["model"]["pose"]}
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
