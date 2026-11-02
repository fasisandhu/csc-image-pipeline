---
name: generate-shots
description: Use when reviewed Cut Sew Club shot specs are ready to render, or when drafts, finals, budget status or a regeneration are needed for the image pipeline.
---

# Generate shots

Render reviewed specs: free drafts first, then one paid final per shot. Budget caps are enforced in code; your job is to never route around them.

Run from the workspace root with the project venv's Python (`PY=.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere).

## Before any paid call

```
$PY -m pipeline.ledger                                   # month spend, today's neurons
$PY -m pipeline.router specs/<shot>.json ... --drafts 3  # plan and total estimate
```

| Plan says | Do |
|---|---|
| total at most $1.00, all `run` | proceed |
| total above $1.00 | stop; ask the user; pass `--approved` only after they say yes |
| `ask` (split or $9 cap) | stop; report the reason to the user |
| `queue` (Cloudflare 9,500 neurons) | stop drafts; resume after 00:00 UTC |

## Steps

1. **Drafts** (free, never published):
   `$PY -m pipeline.generate specs/<shot>.json --phase draft --n 3`
2. **Pick the layout:** view the drafts in `out/drafts/`; choose the one that best matches the fit and framing; say why in one line.
3. **Final:**
   `$PY -m pipeline.generate specs/<shot>.json --phase final --layout out/drafts/<picked>.jpg`
   The compiler attaches the anchor, cast sheet and artwork from the spec; `--layout` adds the picked draft.
4. **QA next:** use the qa-and-repair skill on every final.

## Failures

The commands never retry. After two failures in a run, stop and report the error text to the user. A 4xx error is a request or config problem: fix the cause, do not resend unchanged.

## Anchors

An approved packshot becomes the reference for every later shot of that style and colour. When the user approves one, save it as `refs/anchors/<catalogue>_<colour>_<gsm>_<view>.png` and add it to the set's `reference_images`.
