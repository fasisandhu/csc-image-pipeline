"""Shot spec -> provider prompts, negative list and QA checklist.

Facts come only from catalogue/ and brand/. Values that are missing or start with "TODO"
are left out of the prompt and reported as warnings, never invented.

    python -m pipeline.compile specs/<shot>.json [...]   # writes prompts/<shot_id>.json and .md
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .designs import design_for, ref_png

ROOT = Path(__file__).resolve().parent.parent
WORD_RANGE = (120, 220)

REF_ROLES = {
    "anchor": "the exact garment; match colour, rib width and hood shape",
    "cast": "the exact person; match face, hair, skin tone and build",
    "artwork": "the logo artwork; reproduce its shapes, colours and any text exactly",
    # a sketch's garment is a fast draft's guess: only its framing and pose may be copied (final review I1a)
    "layout": ("a rough layout sketch; use only its framing and pose, and take every seam, the shoulder "
               "construction, drawcords and all other garment details from the text, not from this sketch"),
}


class CompileError(ValueError):
    """The spec contradicts the catalogue or brand kit."""


@dataclass
class Compiled:
    shot_id: str
    tier: str
    size: str
    aspect: str
    refs: list[dict]
    prompts: dict  # keyed by adapter prompt_format: "paragraph", "flux_json"
    negatives: list[str]
    qa: list[str]
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0


def is_missing(value) -> bool:
    return value is None or value == "" or (isinstance(value, str) and value.strip().upper().startswith("TODO"))


def load_json(rel: str | Path) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return text[:1].upper() + text[1:] + "." if text else ""


def _join(items: list[str], sep: str = ", ") -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else sep.join(items[:-1]) + sep.rstrip() + " and " + items[-1]


def _a(phrase: str) -> str:
    """Indefinite article for a phrase: "an oversized", "an 85mm", "a 50mm"."""
    w = phrase.strip().lower()
    return "an" if w[:1] in "aeiou" or w.startswith(("8", "11", "18")) else "a"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def resolve_colour(colour: str | dict) -> dict:
    """Colour is free choice: a brand/palette.json id, or a custom {"name", "hex"}."""
    if isinstance(colour, str):
        palette = load_json("brand/palette.json")["colours"]
        if colour not in palette:
            raise CompileError(f"colour {colour!r} not in brand/palette.json; use a palette id or {{\"name\", \"hex\"}}")
        return palette[colour]
    if not colour.get("name") or not re.fullmatch(r"#[0-9A-Fa-f]{6}", colour.get("hex") or ""):
        raise CompileError(f"custom colour needs a name and a #RRGGBB hex, got {colour!r}")
    return colour


def _ref_role(path: str) -> str:
    if path.startswith("refs/anchors/"):
        return REF_ROLES["anchor"]
    if path.startswith("refs/cast/"):
        return REF_ROLES["cast"]
    return REF_ROLES["layout"]


def compile_spec(spec: dict, layout_refs: list[str] = ()) -> Compiled:
    """layout_refs: picked drafts attached to a final as layout sketches."""
    brand = load_json("brand/brand.json")
    cast_kit = load_json("brand/cast.json")["cast"]
    checklist = load_json("brand/qa_checklist.json")
    s, shot = spec["set"], spec["shot"]
    warn: list[str] = []

    # --- garment, from catalogue ---------------------------------------------------------
    g = shot["garment"]
    cat_path = Path("catalogue") / f"{g['catalogue']}.json"
    if not (ROOT / cat_path).exists():
        raise CompileError(f"unknown catalogue entry {g['catalogue']!r}")
    cat = load_json(cat_path)
    colour = resolve_colour(g["colour"])
    if g["gsm"] not in cat["gsm_options"]:
        raise CompileError(f"{g['gsm']} GSM not offered for {cat['id']}")
    weight_look = cat["weight_look"][str(g["gsm"])]
    fabric = "cotton-rich fleece" if is_missing(cat.get("fabric")) else cat["fabric"]
    is_zip = bool(cat["construction"].get("zip"))

    for key in ("fit", "shoulder"):
        if is_missing(cat.get(key)):
            warn.append(f"catalogue {cat['id']}: {key} missing")
    for note in cat.get("_confirm", []):
        warn.append(f"catalogue {cat['id']}: unconfirmed {note}")
    hex_ = colour["hex"]
    colour_text = f"{colour['name']} ({hex_})"

    con = cat["construction"]
    construction = [con[k] for k in ("hood", "drawcords", "pockets", "cuffs_hem", "seams") if not is_missing(con.get(k))]
    if is_zip:
        z = con["zip"]
        construction.insert(0, z["type"] + ("" if is_missing(z.get("finish")) else f", {z['finish']} finish"))
    fit = "" if is_missing(cat.get("fit")) else _lower_first(cat["fit"].rstrip("."))
    shoulder = "" if is_missing(cat.get("shoulder")) else cat["shoulder"]
    if shoulder and "shoulder" not in fit.lower():
        fit = f"{fit}, {shoulder} shoulder" if fit else f"{shoulder} shoulder"

    garment_text = " ".join(filter(None, [
        _sentence(f"The garment is {_a(cat['style_name'])} {cat['style_name'].lower()} in {g['gsm']} GSM {fabric}" + (f": {fit}" if fit else "")),
        _sentence(f"The {g['gsm']} GSM fabric {weight_look}"),
        _sentence(f"The colour is {colour_text}"),
        _sentence(f"Construction: {_join(construction, '; ')}"),
    ]))

    # --- references (order = image numbering) --------------------------------------------
    refs: list[dict] = []

    def add_ref(path: str, role: str) -> None:
        if not (ROOT / path).exists():
            warn.append(f"reference {path} not found (not yet approved?), skipped")
            return
        if all(r["path"] != path for r in refs):
            refs.append({"path": path, "role": role})

    for r in s.get("reference_images", []):
        path, role = (r["path"], r.get("role")) if isinstance(r, dict) else (r, None)
        add_ref(path, role or _ref_role(path))
    for path in layout_refs:
        add_ref(Path(path).as_posix(), REF_ROLES["layout"])

    # --- decoration and labels -----------------------------------------------------------
    deco_text, has_logo = [], False
    for d in shot.get("decoration", []):
        place = cat["decoration_placements"].get(d["placement"])
        if place is None:
            raise CompileError(f"placement {d['placement']!r} not offered for {cat['id']}")
        if d["type"] not in cat["decoration_types"]:
            raise CompileError(f"decoration type {d['type']!r} not offered")
        if is_missing(place["max_cm"]):
            warn.append(f"{d['placement']}: max size not set in catalogue, size unchecked")
        elif d["size_cm"] > place["max_cm"]:
            raise CompileError(f"{d['placement']} {d['size_cm']} cm exceeds max {place['max_cm']} cm")
        if d.get("render_mode") == "generate_and_verify":
            has_logo = True
            ref = ref_png(d["artwork"])
            add_ref(ref, REF_ROLES["artwork"])
            n = next((i + 1 for i, r in enumerate(refs) if r["path"] == ref), None)
            source = f"the logo shown in Image {n}" if n else "the logo artwork"
            text = (design_for(d["artwork"]) or {}).get("text")
            deco_text.append(_sentence(f"At the {d['placement']}, about {d['size_cm']} cm wide, is {d['type']} of {source}, reproduced exactly"
                                       + (f", reading \"{text}\" spelled exactly" if text else "")))
        else:
            deco_text.append(_sentence(
                f"The {d['placement']} is a clean, flat, plain area about {d['size_cm']} cm wide, ready for {d['type']} added later"
            ))
    if not shot.get("decoration"):
        deco_text.append("The garment is plain and unbranded.")
    neck = shot.get("labels", {}).get("neck")
    if neck:
        w, h = cat["labels"]["neck"]["size_cm"]
        deco_text.append(_sentence(f"Inside the neck sits a {w} by {h} cm woven label {neck}"))

    # --- model ---------------------------------------------------------------------------
    m = shot.get("model", {})
    model_text, cast_desc = "", ""
    if m.get("present"):
        member = cast_kit.get(m["cast"])
        if member is None:
            raise CompileError(f"unknown cast member {m['cast']!r}")
        cast_desc = member["description"]
        if not is_missing(member.get("reference_sheet")):
            add_ref(member["reference_sheet"], REF_ROLES["cast"])
        else:
            warn.append(f"cast {m['cast']}: no approved reference sheet yet, face consistency relies on the description")
        model_text = " ".join([
            _sentence(f"Worn by a model ({cast_desc}), {m['pose']}"),
            "The fleece shows natural creases at the elbows and around the pocket.",
        ])

    # --- camera, light, style ------------------------------------------------------------
    cam = shot["camera"]
    camera_text = _sentence(f"The camera is {cam['angle']}, with {_a(cam['lens'])} {cam['lens']} lens, {cam['framing']}")
    rules_path = ROOT / "brand" / "prompt_rules.json"
    scopes = {"all", cat["id"], "on_model" if m.get("present") else "packshot"}
    rules = json.loads(rules_path.read_text(encoding="utf-8"))["rules"] if rules_path.exists() else []
    rule_text = " ".join(_sentence(r["prompt_text"]) for r in rules
                         if r.get("status") == "active" and r.get("applies_to") in scopes and r.get("prompt_text"))
    style_block = s.get("style_block") or brand["style_block"]
    if style_block != brand["style_block"]:
        warn.append("set style_block differs from brand/brand.json")
    ref_text = " ".join(f"Image {i + 1} is {r['role']}." for i, r in enumerate(refs))

    paragraph = " ".join(filter(None, [
        _sentence(shot["purpose"]),
        garment_text,
        " ".join(deco_text),
        model_text,
        _sentence(f"Clearly visible: {_join(shot['must_show'])}") if shot.get("must_show") else "",
        camera_text,
        _sentence(shot["lighting"]),
        _sentence(shot["background"]),
        rule_text,
        _sentence(f"Style: {style_block}"),
        ref_text,
    ]))
    words = len(paragraph.split()) - len(ref_text.split())  # reference-role lines do not count toward the limit
    if not WORD_RANGE[0] <= words <= WORD_RANGE[1]:
        warn.append(f"paragraph prompt is {words} words, outside {WORD_RANGE[0]}-{WORD_RANGE[1]}")

    flux = {
        "subject": shot["purpose"] + (f"; worn by {cast_desc}, {m['pose']}" if cast_desc else ""),
        "garment": f"{cat['style_name'].lower()}, {g['gsm']} GSM {fabric}, {fit}; fabric {weight_look}; "
                   f"colour {colour_text}; {'; '.join(construction)}",
        "decoration": " ".join(deco_text),
        "scene": shot["background"],
        "camera": f"{cam['angle']}, {cam['lens']}, {cam['framing']}",
        "lighting": shot["lighting"],
        "style": style_block,
    }

    # --- QA checklist --------------------------------------------------------------------
    active = {"always", "zip" if is_zip else "pullover"}
    if m.get("present"):
        active.add("model")
    if shot.get("decoration"):
        active.add("decoration")
    if has_logo:
        active.add("logo")
    fill = {"shoulder": shoulder or "unknown", "gsm": g["gsm"], "weight_look": weight_look,
            "colour_name": colour["name"], "colour_hex": hex_, "cast": m.get("cast", "")}
    qa = [i["text"].format(**fill) for i in checklist["items"] if i["when"] in active]
    qa += [f"Shows: {x}" for x in shot.get("must_show", [])]
    qa += [f"Free of: {x}" for x in shot.get("avoid", [])]

    return Compiled(
        shot_id=shot["shot_id"],
        tier=shot["tier"],
        size=s.get("resolution", "2K"),
        aspect=s.get("aspect_ratio", "4:5"),
        refs=refs,
        prompts={"paragraph": paragraph, "flux_json": json.dumps(flux, ensure_ascii=False)},
        negatives=list(shot.get("avoid", [])) + checklist["negatives"],
        qa=qa,
        warnings=warn,
        word_count=words,
    )


def compile_file(path: str | Path, layout_refs: list[str] = ()) -> Compiled:
    return compile_spec(json.loads(Path(path).read_text(encoding="utf-8")), layout_refs)


def write(c: Compiled) -> Path:
    out = ROOT / "prompts"
    out.mkdir(exist_ok=True)
    (out / f"{c.shot_id}.json").write_text(json.dumps(asdict(c), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    flux = json.dumps(json.loads(c.prompts["flux_json"]), indent=2, ensure_ascii=False)
    md = [
        f"# {c.shot_id}", "", f"Tier **{c.tier}**, {c.size}, {c.aspect}", "",
        f"## Nano Banana prompt ({c.word_count} words)", "", c.prompts["paragraph"], "",
        "## FLUX.2 klein prompt", "", "```json", flux, "```", "",
        "## References", "", *([f"{i + 1}. `{r['path']}`: {r['role']}" for i, r in enumerate(c.refs)] or ["(none)"]), "",
        "## QA checklist", "", *[f"- [ ] {q}" for q in c.qa], "",
        "## Negatives", "", *[f"- {n}" for n in c.negatives], "",
        "## Warnings", "", *([f"- {w}" for w in c.warnings] or ["(none)"]), "",
    ]
    md_path = out / f"{c.shot_id}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    return md_path


if __name__ == "__main__":
    for p in sys.argv[1:]:
        c = compile_file(p)
        print(f"{c.shot_id}: {c.word_count} words, {len(c.refs)} refs, {len(c.warnings)} warnings -> {write(c).relative_to(ROOT)}")
