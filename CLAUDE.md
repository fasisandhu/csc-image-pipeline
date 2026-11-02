# CLAUDE.md: Cut Sew Club image pipeline

Read this file fully at the start of every session. It holds the decisions made so far; don't re-research or re-decide them unless I ask.

## The business

Cut Sew Club (https://cutsewclub.vercel.app/) is a B2B apparel sourcing studio in Lahore, Pakistan, with production through a partner factory in Sialkot. We make private-label heavyweight hoodies for US customers:

- Styles: pullover (classic and oversized fit) and full-zip.
- Fabric: 320, 380 and 450 GSM cotton-rich fleece.
- Minimum order: 30 pieces.
- Decoration: flat embroidery, 3D puff embroidery, screen print, woven neck labels, hang tags.
- Buyers: small US streetwear brands, merch companies, individuals, and businesses ordering staff or event hoodies.
- Custom-first: the customer chooses colour, design and decoration. There is no fixed colour range or theme. Website images are samples of what's possible (sample colours, sample designs, each technique and placement), not a stock catalogue. Only the garment itself is fixed: fits, construction, GSM, placements and max sizes.
- Positioning: verifiable proof and accountability. The site promises real photos and video of every order.

I'm an AI engineer and an experienced Claude Code user, comfortable with Python and APIs. Skip basics; be concise.

## What we're building

An image generation pipeline that produces website images now (product shots, on-model shots, decoration close-ups) and client logo mockups later. It's the first of several pipelines (ad creatives, social, SEO, leads) that will share one brand kit and eventually become one Claude Code plugin, `csc-studio`.

Design rule: specs and brand rules are permanent; generators are replaceable plug-ins. Never hard-wire a model into anything except its adapter.

Full research (tools, limits, licences, prompts, workflow): https://claude.ai/artifact/6EQ49qQynSpc4YubcQDgu7

## Current phase: Phase 1+2

| Role | Provider and model | Cost (Oct 2026) | Use for |
| --- | --- | --- | --- |
| Drafts | Cloudflare Workers AI, `@cf/black-forest-labs/flux-2-klein-4b` | Free 10,000 neurons/day (about 80–95 1MP images); resets 00:00 UTC | Colourways, placement, layout, pose and background exploration. Sketches only, never published. |
| Standard finals | Gemini API, Nano Banana 2 (`gemini-3.1-flash-image`) | $0.067 per 1K image, $0.101 per 2K image | Anchor packshots, product shots, simple on-model shots |
| Premium finals | Gemini API, Nano Banana Pro (`gemini-3-pro-image`) | $0.134 per 1K or 2K image | Hero images, decoration close-ups, anything with text or a logo |

- Confirm the exact Gemini model IDs in AI Studio before first use. They have changed before.
- Check the Cloudflare model page for FLUX.2 klein's exact input fields before writing the adapter.
- Not yet: Colab (Qwen-Image-Edit masks, FASHN VTON try-on, Z-Image LoRA, IC-Light). Add only when the first real sample arrives or instruction edits keep failing.

## Budget rules

- Gemini spend is funded by the $10/month Google AI Pro credit. Hard cap: $9.00 a month in code, with the budget alert set in Google Cloud.
- Split: about $4 Nano Banana 2, about $6 Nano Banana Pro. Track estimated spend per call in `out/log.jsonl`.
- Stop and ask me before any run estimated above $1, or any run that would cross the cap.
- Cloudflare: stop at 9,500 neurons a day and queue the rest for the next day.
- Never retry a failing call in a loop. Two failures, then stop and report.

## Security

- Keys live only in `.env` (`GEMINI_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`). Load with python-dotenv.
- `.env`, `out/`, and any client artwork are in `.gitignore` from the first commit.
- Never print, log or echo key values.

## Repo structure (create in build step 1)

```
csc-image-pipeline/
├── CLAUDE.md
├── .env.example          # key names only, no values
├── .gitignore
├── requirements.txt      # google-genai, requests, pillow, opencv-python, python-dotenv, resvg-py
├── brand/
│   ├── brand.json        # name, colours (hex + Pantone), fonts, style_block, captions
│   ├── cast.json         # the four AI models (below)
│   ├── palette.json      # showcase colours for samples (examples, not limits)
│   ├── qa_checklist.json # garment QA checklist as data
│   ├── designs/          # sample designs with invented brand names (step 5)
│   └── logo/             # placeholder logo until the real one is final
├── catalogue/
│   ├── pullover-classic.json
│   ├── pullover-oversized.json
│   └── zip.json
├── refs/
│   ├── anchors/          # approved packshots per style and colour
│   └── cast/             # approved cast reference sheets
├── specs/                # one JSON shot spec per image
├── prompts/              # compiled prompts per shot and provider
├── pipeline/
│   ├── providers/        # cloudflare.py, gemini.py, vision.py (one adapter each)
│   ├── compile.py        # spec -> prompts, negatives, QA checklist
│   ├── router.py
│   ├── generate.py
│   ├── ledger.py         # cost log, budget caps, approvals (python -m pipeline.ledger for status)
│   ├── qa.py
│   ├── repair.py
│   ├── logo.py
│   └── finish.py         # colour, background, contact sheet, approve/reject, export
├── plugins/csc-studio/   # Claude Code plugin: skills that drive the pipeline CLI
├── .claude-plugin/marketplace.json  # local marketplace "cut-sew-club" listing csc-studio
└── out/
    ├── drafts/
    ├── finals/
    ├── reject/
    └── log.jsonl
```

## Brand kit and catalogue contents

`brand/brand.json`:
- name, website
- colours: hex and Pantone (TBD; use placeholders until the logo is final)
- `style_block`, reused word for word in every prompt of a set: "clean editorial streetwear catalogue, true-to-life colour, soft natural contrast, no film grain"
- captions: "Concept render. Production photos are shot for every order." and, for on-model shots, "AI model, concept render"

`brand/palette.json`: about 10 popular streetwear hoodie colours with hex codes, used for sample images. Examples only. A spec may pick a palette id or give any custom colour as `{"name", "hex"}`; colour QA checks the render against whatever the spec asked for.

Sample designs (`brand/designs/`): artwork shown on sample hoodies so customers can picture their own. Always invented brand names, never real brands. Start with hand-made SVGs (exact, free, used to test the logo and composite step), then add Nano Banana Pro designs for variety. Text in a generated design goes through the same logo verification.

Each `catalogue/*.json` holds:
- style name, fit description and key measurements
- GSM options
- no colour list (colour is the customer's choice)
- construction: hood (double-layer, centre seam), flat drawcords with metal aglets, kangaroo or split pockets, 2x2 rib cuffs and hem, coverstitched seams, zip type for the zip style
- how weight looks:
  - 320 GSM drapes softly
  - 380 GSM holds gentle structure
  - 450 GSM stands away from the body, with thick rib and a hood that keeps its shape
- decoration placements with max size: left chest 9 cm, centre chest 22 cm, full back 30 cm, sleeve
- neck label 5 × 2 cm woven; hang tag spec

`brand/cast.json` (invented people, reuse descriptions word for word):

| Name | Description | Use |
| --- | --- | --- |
| Amir | South Asian man, mid-20s, short black fade, light stubble, slim athletic build, 180 cm | Hero, streetwear, fit comparison |
| Maya | Black woman, early 20s, natural hair in a low puff, medium build, 168 cm | Streetwear, oversized, back prints |
| Leo | White man, late 20s, curly brown hair, short beard, broad build, 185 cm | Zip hoodie, staff shots |
| Hana | East Asian woman, mid-20s, straight black bob, slim build, 165 cm | Lifestyle, decoration on body |

## Shot spec (one JSON per image)

```json
{
  "set": {"set_id": "", "style_block": "", "reference_images": [], "aspect_ratio": "4:5", "resolution": "2K"},
  "shot": {
    "shot_id": "", "purpose": "", "tier": "draft | standard | premium",
    "garment": {"catalogue": "pullover-oversized", "colour": "washed-black | {\"name\": \"\", \"hex\": \"#RRGGBB\"}", "gsm": 450},
    "decoration": [{"type": "3D puff embroidery", "artwork": "brand/logo/placeholder.svg", "placement": "centre chest", "size_cm": 22, "render_mode": "generate_and_verify | placeholder_then_composite"}],
    "labels": {"neck": "", "hang_tag": ""},
    "camera": {"angle": "", "lens": "", "framing": ""},
    "lighting": "", "background": "",
    "model": {"present": true, "cast": "Amir", "pose": ""},
    "must_show": [], "avoid": []
  }
}
```

Facts come from `catalogue/` and `brand/`, never invented. The spec references catalogue entries; the compiler expands them.

## Prompt rules

- **Nano Banana 2 and Pro:** flowing natural-language paragraph, 120–220 words, not counting the "Image N is…" reference lines. Order:
  1. shot type and purpose
  2. garment: fit, weight described visually, colour name plus hex, construction
  3. decoration with placement and size
  4. model and pose
  5. camera, lens, framing
  6. lighting, then background
  7. style_block
  8. one line per reference image stating its role ("Image 1 is the exact garment; match colour, rib width and hood shape").

  Phrase things positively ("plain, unbranded"), not as lists of negatives.
- **FLUX.2 klein:** structured JSON prompt with keys `subject`, `garment`, `decoration`, `scene`, `camera`, `lighting`, `style`.
- Name construction precisely: coverstitch, dropped vs set-in shoulder, 2x2 rib, metal zip.
- Same cast description and style_block across a set, word for word.
- Ask for natural creases at elbows and pocket on on-model shots.

## Pipeline steps

1. **Intake:** request becomes a job (styles, slots, cast, decoration).
2. **Spec:** one JSON per shot from catalogue and brand. I can review specs before generation.
3. **Compile:** prompt per provider, negative list, QA checklist.
4. **Route:** pick provider by `tier` and remaining budget; fall back or queue when a budget runs out.
5. **Generate:** 2–4 Cloudflare drafts, pick the layout, then the Gemini final at 2K with anchor and cast references attached. Log prompt, model, refs and cost for every call.
6. **QA:** Claude checks each render against the checklist; a script checks garment colour against the spec hex (flag ΔE > 5). Failures get a reason code and go to `out/reject/`.
7. **Repair:** one-change region edit prompt, then QA again. Two failed repairs, then regenerate. Two failed regenerations, then flag for me.
8. **Logo:**
   - generate with the logo attached (Pro);
   - verify: text read by Gemini vision (`gemini-3.8-flash`, not pytesseract; must match exactly), outline match for shape (rigid alignment, IoU threshold in `brand/qa_checklist.json`, calibrated on few samples so far), ink colour difference, and size against the placement;
   - repair once;
   - if it still fails, composite: detect the patch, perspective warp, displacement map from the fabric, then a finish preset (print, flat embroidery, 3D puff).
9. **Finish:**
   - background clean-up for packshots;
   - colour correction;
   - upscale only if the slot needs more than 2K.
10. **Approve:** contact sheet with specs; I approve or reject with a note. Rejection notes become new prompt rules.
11. **Export:**
    - site images: WebP at 3 widths (1600, 800, 400), with alt text and the caption;
    - client mockups: one-page approval PDF.
12. **Library:** approved images plus their full record, ready for other pipelines.

## Garment QA checklist (minimum)

- [ ] Two drawcords, equal length, metal aglets, through eyelets
- [ ] Zip teeth even and continuous, one pull, runs to the neck (zip style)
- [ ] Rib cuffs and hem consistent width, distinct from the body
- [ ] Shoulder seam matches the fit (dropped for oversized)
- [ ] Kangaroo pocket openings symmetrical, attached at the hem
- [ ] Hood double-layer with centre seam
- [ ] Drape matches GSM (450 must not look like a T-shirt)
- [ ] Colour matches hex
- [ ] Hands correct (five fingers, not fused into pockets)
- [ ] Face matches the cast sheet
- [ ] No unintended text or logos; intended logo passes verification

## Non-negotiables

- Client logos are never published unverified. Every logo either passes verification or is composited from the real vector file.
- Every AI image on the site carries the concept-render caption; on-model images say "AI model".
- Nothing is published, posted, sent or spent beyond the caps without my explicit approval.
- Cloudflare drafts never go on the site.

## Build order

1. **Scaffold:** repo structure, `.gitignore`, `.env.example`, `requirements.txt`, and brand and catalogue templates filled with what's known here (placeholders marked TODO). Done when I can open each JSON and see what to fill in.
2. **Provider adapters:** `gemini.py` and `cloudflare.py` with one shared interface (`generate(prompt, refs, size, aspect) -> image + cost`). Done when one test image comes back from each, logged with cost.
3. **Spec-to-prompt compiler and router:** done when one spec produces both prompt formats and routes by tier and budget.
4. **Test run:** one hoodie, one colour. 3 drafts, 1 anchor packshot, 1 on-model final. Report real cost per keeper and QA results.
5. **QA, repair loop and logo step,** then finishing and export.
6. **Package as the first skills** of the `csc-studio` plugin.

Work one step at a time. At the end of each step, show me what was built, how to run it, and what it cost, then wait for my go-ahead.
