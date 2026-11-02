---
name: plan-shots
description: Use when a Cut Sew Club image request arrives (website shots, packshots, on-model shots, decoration close-ups, client mockups) and needs turning into shot specs before anything is generated.
---

# Plan shots

Turn a request into one JSON spec per image, compiled and costed, for the user to review. Nothing is generated here.

Run everything from the workspace root (the repo with `CLAUDE.md`, `brand/`, `catalogue/`, `pipeline/`), with the project venv's Python: `PY=.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere.

## Facts come from files, never from you

| Spec field | Source |
|---|---|
| `garment.catalogue`, `gsm` | `catalogue/<id>.json` (`gsm_options`) |
| `garment.colour` | a `brand/palette.json` id, or `{"name", "hex": "#RRGGBB"}` for a customer colour |
| `decoration[].artwork` | `brand/designs/designs.json` (`file`), or client vector under `clients/` |
| `decoration[].placement`, `size_cm` | `decoration_placements` in the catalogue; size at most `max_cm` |
| `model.cast` | `brand/cast.json` names only |
| `set.reference_images` | approved files in `refs/anchors/` and `refs/cast/`; an anchor only when its catalogue, colour and GSM all match the shot |

If a fact the shot needs is missing or `TODO` (a measurement, a zip finish), ask the user. Do not fill it in.

## Steps

1. **Intake:** list the shots: style, colour, GSM, view, cast, decoration. One spec per image.
2. **Write specs** in `specs/<shot_id>.json`. Copy the closest existing spec as the template:
   - packshot: `specs/ovs-wblack-450-anchor-front.json`
   - on-model: `specs/ovs-wblack-450-maya-front.json`
   - logo or decoration: `specs/ovs-wblack-450-oakmere-puff-front.json`
3. **Choose the tier:** `premium` for heroes, decoration close-ups and anything with text or a logo; `standard` for anchor packshots, product shots and simple on-model shots; `draft` for exploration only.
4. **Check anchor and artwork fit:**
   - No anchor for this catalogue, colour and GSM: leave `reference_images` without one and propose an anchor packshot (standard tier) first; later shots of that garment attach it.
   - A design's `for_garments` tone (dark or light) differs from the garment colour's lightness: stop and ask the user for a matching colourway before planning the shot.
5. **Choose the logo mode:** `generate_and_verify` (Pro renders it; verified later) or `placeholder_then_composite` (plain area; real artwork composited). Client logos with fine text: prefer `placeholder_then_composite`.
6. **Compile and cost:**
   ```
   $PY -m pipeline.compile specs/<shot>.json ...    # writes prompts/<shot>.md
   $PY -m pipeline.router specs/<shot>.json ... --drafts 3
   ```
   Fix every `CompileError`. Read each warning; missing anchors or cast sheets are expected before they are approved.
7. **Hand over for review:** show the user each `prompts/<shot>.md`, the routed models and the total estimate. Wait for their go-ahead before using generate-shots.

## Common mistakes

- Inventing a hex, size or measurement instead of reading it or asking.
- A new on-model spec without the approved anchor in `reference_images`.
- Different cast wording or style block within one set: the compiler copies them verbatim, so do not paraphrase them in `purpose` or `pose`.
- Packing negatives into `purpose`; put them in `avoid` and phrase the shot positively.
