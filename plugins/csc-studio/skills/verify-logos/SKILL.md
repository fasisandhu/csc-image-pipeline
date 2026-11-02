---
name: verify-logos
description: Use when a Cut Sew Club render contains a logo, wordmark, printed or embroidered design, or when a client or sample design must be placed on a garment image.
---

# Verify logos

A logo is never shown unless it passed verification or was composited from the real file. Non-negotiable: no exception for "close enough" or "only a mockup".

Run from the workspace root with the project venv's Python (`PY=.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere).

## Artwork

- Sample designs: `brand/designs/*.svg`, listed in `designs.json` with their exact `text`. Invented brand names only.
- Client artwork: vector files under `clients/` (git-ignored). Add an entry with its exact text before verifying.
- After adding or editing an SVG: `$PY -m pipeline.logo rasterize`, then view the PNGs in `brand/designs/png/` for clipped text or wrong fonts.

## Verify a generated logo

```
$PY -m pipeline.logo verify <image>
```

Checks: text read by vision must match exactly; outline IoU at least `min_iou`; ink colour ΔE at most `max_ink_de`; size within range of the spec's `size_cm` (all in `brand/qa_checklist.json`). Failed checks come back as `logo_text`, `logo_shape`, `logo_colour`, `logo_size`: pass them to `pipeline.qa --fail`.

## Repair once, then composite

1. First failure: `$PY -m pipeline.repair <image> --reason <logo_code>` (the artwork is attached automatically), then verify again.
2. Second failure: composite the real artwork onto an approved plain render of the same garment, logged under the logo shot:
   ```
   $PY -m pipeline.logo composite <plain image> --design <id> --as-shot <logo shot_id>
   ```
   `--placement`, `--size-cm` and `--preset` (`print`, `flat_embroidery`, `3d_puff`) default to the design's suggestion.
3. View the composite: drawcords and hands should sit in front of the artwork, and the size should look right against the torso. A warning about assumed chest width means the scale is a guess; tell the user.

## Common mistakes

- Accepting a logo whose text reads correctly at thumbnail size without running verify.
- Repairing a logo twice. The rule is one repair, then composite.
- Compositing onto a render that already has a logo on it.
