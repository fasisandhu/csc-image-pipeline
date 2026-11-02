---
name: finish-and-export
description: Use when Cut Sew Club renders have passed QA and need finishing, a contact sheet for the user's approval, recording the user's approval or rejection, or exporting approved images for the website or a client.
---

# Finish and export

Approval belongs to the user. You prepare the contact sheet and record exactly what they said; export only works on approved files.

Run from the workspace root with the project venv's Python (`PY=.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere).

## Finish (free)

| Need | Command |
|---|---|
| garment colour drift | `$PY -m pipeline.finish colour <image>` |
| packshot background specks, banding | `$PY -m pipeline.finish background <image>` |

Each writes a new file next to the source (`_cc`, `_bg`). QA the new file before showing it.

## Approve

1. `$PY -m pipeline.finish sheet <image> [...]` writes `out/sheets/sheet_<time>.jpg` with shot, model, QA and status.
2. Show the user the sheet path and wait.
3. Record only what the user said, per image:
   - approved: `$PY -m pipeline.finish approve <image> --note "user: <their words>"`
   - rejected: `$PY -m pipeline.finish reject <image> --note "<their reason>"`

A rejection note lands in `brand/prompt_rules.json` as `pending`. Write it as one positive prompt sentence in `prompt_text`, set `applies_to` (`all`, `packshot`, `on_model` or a catalogue id) and `status: active`, then show the user the rule.

## Export (approved files only)

```
$PY -m pipeline.finish export <image>          # site: WebP 1600/800/400 + alt text + caption
$PY -m pipeline.finish export <image> --pdf    # client mockup: adds a one-page approval PDF
```

Output goes to `out/export/<shot_id>/`, with a JSON record holding the alt text and the caption ("AI model, concept render" for on-model shots, the concept-render line otherwise). Every AI image keeps its caption wherever it is used. Drafts are never exported.

Exporting is local. Publishing, posting or sending files anywhere needs the user's explicit go-ahead each time.

## Common mistakes

- Recording an approval from something the user did not say ("looks fine to me" about a different image).
- Exporting the source instead of the approved `_cc` or composite file.
- Dropping the caption when the image is used elsewhere.
