---
name: qa-and-repair
description: Use when a Cut Sew Club render (final, repair or finished variant) needs checking against its garment QA checklist, or when a QA failure needs repairing, regenerating or flagging.
---

# QA and repair

Every render gets a recorded verdict before anyone sees it. You do the visual checklist; the script measures colour, applies the repair limits and moves failures to `out/reject/`.

Run from the workspace root with the project venv's Python (`PY=.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere).

## Loop

1. `$PY -m pipeline.qa <image>` prints the colour result (ΔE2000 vs the spec hex) and the shot's checklist. Nothing is recorded yet.
2. **Look at the image yourself**, at full size plus close crops of drawcords, cuffs, pocket, hands, face and any logo. Tick each checklist line honestly.
3. Record the verdict:
   - all lines pass: `$PY -m pipeline.qa <image> --visual pass`
   - any line fails: `$PY -m pipeline.qa <image> --fail <code> [<code> ...] --note "<what you saw>"`

   Codes are the keys of `codes` in `brand/repairs.json`. The colour code is added automatically.
4. Do what the printed `next:` says:

| next | Do |
|---|---|
| `approve` | add to the contact sheet (finish-and-export) |
| `finish colour_drift` | `$PY -m pipeline.finish colour <image>`, then QA the `_cc` file |
| `repair <code>` | `$PY -m pipeline.repair <image> --reason <code>`, then QA the new file |
| `composite <code>` | logo failed after one repair: use verify-logos to composite |
| `regenerate` | new final with `pipeline.generate` (generate-shots) |
| `flag` | stop; show the user the shot's history; they decide |

The limits (two repairs per render, two regenerations per shot, one repair for logos) are counted from `out/log.jsonl`. Do not reset them by renaming files or editing the log.

## Common mistakes

- Passing a render because the colour passed. The script cannot see drawcords, hands or faces; you can.
- Several fixes in one repair. One code per repair; QA again after each.
- Judging colour from a crop of the lit side. Trust the whole-garment measurement; if it looks wrong, check the mask in `out/cache/masks/`.
- A shot with a logo: also run verify-logos before the verdict.
