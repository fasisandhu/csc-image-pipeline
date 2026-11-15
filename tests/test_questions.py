import dataclasses

from pipeline.compile import load_json
from studio.questions import custom_colour_problem, missing, resolve, valid_value


def sec(answers, sid):
    return next(s for s in resolve(answers) if s.id == sid)


def test_model_sections_follow_recipe():
    assert not sec({"recipe": "packshot-front"}, "cast").visible
    assert not sec({"recipe": "packshot-front"}, "scene").visible
    assert sec({"recipe": "on-model-front"}, "cast").visible
    assert sec({"recipe": "on-model-front"}, "scene").visible


def test_gsm_follows_catalogue():
    assert [o.id for o in sec({"catalogue": "zip"}, "gsm").options] == ["320", "380", "450"]


def test_gsm_and_placements_without_catalogue_use_common_options():
    gsm = sec({}, "gsm").options
    assert [o.id for o in gsm] == ["320", "380", "450"] and not any(o.disabled for o in gsm)
    assert valid_value("gsm", 450, {})
    assert valid_value("placement", "full back", {})
    assert valid_value("technique", "screen print", {})
    # zip's centre chest is TODO, so it is not offered in common
    cc = next(o for o in sec({}, "placement").options if o.id == "centre chest")
    assert cc.disabled and cc.reason == "max size not set yet"


def test_sleeve_disabled_with_reason():
    sleeve = next(o for o in sec({"catalogue": "pullover-oversized"}, "placement").options if o.id == "sleeve")
    assert sleeve.disabled and sleeve.reason == "max size not set yet"


def test_placement_not_visible_in_view():
    opts = {o.id: o for o in sec({"catalogue": "pullover-oversized", "recipe": "on-model-back"}, "placement").options}
    assert opts["centre chest"].disabled and opts["centre chest"].reason == "not visible in a back view"
    assert not opts["full back"].disabled
    front = {o.id: o for o in sec({"catalogue": "pullover-oversized", "recipe": "packshot-front"}, "placement").options}
    assert front["full back"].reason == "not visible in a front view"
    assert not {o.id: o for o in sec({"catalogue": "pullover-oversized"}, "placement").options}["full back"].disabled


def test_closeup_disables_plain():
    opts = {o.id: o for o in sec({"recipe": "decoration-closeup"}, "decoration").options}
    assert opts["none"].disabled and opts["none"].reason == "a close-up needs a design"
    assert not opts["design"].disabled
    assert not valid_value("decoration", "none", {"recipe": "decoration-closeup"})
    assert valid_value("decoration", "none", {"recipe": "packshot-front"})
    assert not any(o.disabled for o in sec({"recipe": "packshot-front"}, "decoration").options)


def test_size_max_follows_placement():
    assert sec({"catalogue": "pullover-oversized", "placement": "full back"}, "size_cm").max == 30


def test_size_without_placement_or_catalogue():
    assert sec({}, "size_cm").max == 30
    assert valid_value("size_cm", 22, {}) and not valid_value("size_cm", 31, {})
    assert not valid_value("size_cm", 0, {}) and not valid_value("size_cm", True, {})


def test_design_tone_mismatch_disabled():
    opts = {o.id: o for o in sec({"colour": "cream"}, "design").options}
    assert opts["oakmere-athletic"].disabled and opts["oakmere-athletic"].reason == "made for dark garments"
    assert not opts["oakmere-athletic-light"].disabled
    dark = {o.id: o for o in sec({"colour": {"name": "Ink", "hex": "#101010"}}, "design").options}
    assert dark["oakmere-athletic-light"].disabled


def test_missing_lists_required_unanswered():
    assert missing({"catalogue": "pullover-oversized"})[:5] == ["purpose", "colour", "gsm", "recipe", "decoration"]


def test_valid_value_rejects_unknown():
    assert valid_value("cast", "Zara", {"recipe": "on-model-front"}) is False
    assert valid_value("cast", "Maya", {"recipe": "on-model-front"}) is True
    assert valid_value("colour", "#12345", {}) is False
    assert valid_value("colour", {"name": "Teal", "hex": "#1F6F78"}, {})
    assert not valid_value("colour", {"name": "", "hex": "#1F6F78"}, {})
    # a long name would overflow a Windows path after a paid call, and lengthen every prompt: see the next test
    assert valid_value("colour", {"name": "T" * 24, "hex": "#1F6F78"}, {})
    assert not valid_value("colour", {"name": "T" * 25, "hex": "#1F6F78"}, {})
    assert valid_value("gsm", "450", {"catalogue": "zip"}) and valid_value("gsm", 450, {"catalogue": "zip"})
    assert not valid_value("nope", "x", {})


def test_resolve_is_json_serialisable():
    import json
    json.dumps([dataclasses.asdict(s) for s in resolve({"catalogue": "zip", "colour": "olive"})])


def test_custom_colour_name_is_no_longer_than_a_palette_name():
    """T4: a custom colour name has at most as many words as the longest palette name (counted from
    brand/palette.json) and at most 24 characters, so it never pushes a prompt past 220 words. A longer one is refused
    in plain words, which the page shows as they are."""
    names = [c["name"] for c in load_json("brand/palette.json")["colours"].values()]
    most = max(len(n.split()) for n in names)
    example = next(n for n in names if len(n.split()) == most)
    refused = f"Keep the colour name to {most} words and 24 characters at most, like “{example}”."
    for name in (" ".join(["Teal"] * most), "Teal", "T" * 24, " Deep Teal "):
        assert custom_colour_problem({"name": name, "hex": "#1F6F78"}) is None, name
        assert valid_value("colour", {"name": name, "hex": "#1F6F78"}, {}), name
    for name in (" ".join(["Teal"] * (most + 1)), "T" * 25, "Deep Ocean-Teal Blue Mix"):
        assert custom_colour_problem({"name": name, "hex": "#1F6F78"}) == refused, name
        assert not valid_value("colour", {"name": name, "hex": "#1F6F78"}, {}), name
    assert custom_colour_problem({"name": " ", "hex": "#1F6F78"}) == "Give the colour a name."
    assert "hex" in custom_colour_problem({"name": "Teal", "hex": "teal"})
