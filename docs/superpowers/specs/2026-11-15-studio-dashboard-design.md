# CSC Studio dashboard: design

Date: 2026-11-15. Status: approved by the owner on 2026-11-15, with the how-to-use guide added at their request.
Mockup: [assets/2026-11-15-studio-dashboard-mockup.html](assets/2026-11-15-studio-dashboard-mockup.html) (screen 1: builder; screen 2: review and library).

## Intent

The image pipeline's real value over calling an image API directly is the guided, fact-checked path from a vague idea to a verified image. A side-by-side test on 2026-11-14 showed it: a direct Gemini call put an invented "AVENLIE CO." label on our hoodie, dropped the drawcords and rendered a 22 cm logo at about 50 cm; the pipeline caught and fixed the same classes of error.

Today that path needs someone to write spec JSON or drive Claude Code skills. The dashboard makes it clickable: a user types a vague idea ("image of hoodie"), answers choices that adapt to earlier answers, sees a live summary, approves a prompt the pipeline wrote, and follows the image through checks to approval.

It is also the shell of a business suite. Images is the first tab; ads, creative, SEO and marketing pipelines come later as further tabs sharing one brand kit, one image library and one budget.

Users: the owner and one teammate, who are equals: either can approve. Internal, non-commercial use. Each has their own Claude plan.

## Goals

1. **Image builder** (screen 1): free-text idea, pre-fill from it, adaptive sections, live summary, prompt written by the pipeline, cost, approve.
2. **Review** (screen 2): progress from sketches to final to checks to approval; the final image large with check results drawn on it; before/after for repairs; approve, ask for a change, reject with a note.
3. **Library**: approved images with their full record, filters, coverage gaps ("missing" tiles), download of web sizes with caption and alt text.
4. **Two users**, each running Claude work on their own credential (Claude subscription or Anthropic API key, chosen per user). One shared library, ledger and budget.
4a. **Client logo upload** (SVG, PDF or PNG) in the builder's design section, verified the same way as sample designs (see "Client logo upload" below).
5. **Suite shell**: tab bar with Images live and Ads, Social, SEO shown as "later"; question sets stored as data so a new pipeline is a new question set and a new prompt writer, not a new app.
6. **How to use**: a built-in guide so every user understands the pipeline. It has a guide page, a first-visit tour, "?" tips next to every section and check result, and "Ask the guide" (see "How to use guide" below).

## Non-goals (this version)

- The ads, social and SEO pipelines themselves.
- Customer-facing use (a simpler customer mockup form is a later, separate project).
- Cloud hosting, multiple organisations, mobile-first layout.
- Editing the brand kit or catalogue from the dashboard (read-only view at most).
- Replacing the Claude Code plugin; it keeps working on the same code and data.

## Anti-goals: failure even if it works

- Any fact typed freely or invented by an AI: style, colour hex, GSM, construction, placement and its max size, cast description, design text. Facts come only from `catalogue/` and `brand/`. AI may write only the creative parts (scene, pose, mood) and may only pick, never invent, factual options. The one fact a user may enter is a custom colour as a name plus a validated `#RRGGBB` hex, which CLAUDE.md allows. AI never invents a hex.
- The dashboard bypassing pipeline rules: $9/month Gemini cap, asking before any run over $1, Cloudflare 9,500 neurons/day, two failures then stop, repair and regeneration limits, logo verify-or-composite, captions on every AI image.
- Anything exported or sent without approval from one of the two users.
- Keys or Claude credentials reaching the browser, git, logs or the ledger.
- Business logic living in the UI. The UI calls the server; the server calls the `pipeline` package.
- One user's requests running on another user's Claude credential.

## Constraints

- The existing `pipeline/` package (compile, router, generate, qa, repair, logo, finish, ledger, providers) and its files (`specs/`, `prompts/`, `out/`, `out/log.jsonl`) stay the source of truth. Extend it; do not fork or rewrite it.
- CLAUDE.md budget, security and non-negotiable rules apply unchanged. `out/log.jsonl` stays the cost ledger that enforces the caps.
- AI providers available now: one Gemini API key (shared, server-side only) and each user's own Claude plan. Anthropic API keys may be added per user later.
- Anthropic's docs say apps should use API key authentication and do not permit routing other people's requests through a Free, Pro, Max or Ultra login. Internal tools are not addressed. This design therefore runs each user's Claude work only on that user's own credential.
- Host: the owner's Windows PC. No open ports. How the teammate reaches it (Tailscale, or Cloudflare Tunnel plus Access with a domain) is decided at the team-access step.

## Decided by the owner

- Layout: one page that grows (sections appear when earlier answers need them), with a live summary panel on the right ("Your image so far" with edit links, prompt behind "Show the full prompt", estimated cost, actions). Friendlier visual style per the mockup: style pictures, named colour swatches, model cards, a size slider that stops at the placement maximum.
- Features in scope:
  1. suite tab bar;
  2. "Fill from my words" pre-fill;
  3. idea cards: coverage gaps, good pairings, "surprise me";
  4. impossible options greyed with the reason;
  5. live cost and month budget bar;
  6. three free Cloudflare sketches before paying;
  7. "make it in more colours".
- Review screen and library as in the mockup's screen 2.
- Claude is called by the dashboard's server through the Claude Agent SDK, which runs Claude Code headless with the `csc-studio` plugin loaded, so existing skills are reused.
- Each user chooses their Claude credential: their own subscription or their own Anthropic API key. The API key path must work without code changes.
- **Spend confirmation:** either user may confirm a run estimated over $1, including the teammate's own runs. The $9 monthly Gemini cap stays a hard stop that no one can confirm past. CLAUDE.md's "stop and ask me" line should be updated to match when this is built.
- **Teammate access is decided later.** Build for the owner on their own PC first, with the user identity coming from one replaceable source (localhost means the owner). Tailscale or Cloudflare with a domain gets chosen at the team-access step.
- **Client logo upload is in this version**, for SVG, PDF and PNG files.
- **Either user approves:** images for export, spend over $1, anchors and pending prompt rules. CLAUDE.md was updated on 2026-11-15 to say "me or my teammate".
- **Client orders are in this version** (see "Client orders" below).
- **Client reference images are used directly:** clients send them with consent, so they go to the image model as reference images with a role (garment, layout or mood). There is no read-only restriction.
- **How to use guide is in this version** (owner's request, 2026-11-15).

## Calls made during design (owner can overturn)
- **Fallback:** if a user's Claude is unavailable (usage limit, no credential, error), text tasks fall back to Gemini Flash. The visual review then waits for a human to tick the checklist.
- **Gemini stays the measurer:** garment masks, logo text reading, colour and the new stray-text scan stay on Gemini vision and scripts, as now. Claude adds judgement on top; it never replaces a measured check.
- **Shot types become data:** a `brand/recipes.json` file of shot types (front and back packshot, on-model hero, back print, decoration close-up, flat lay) holding camera, lens, framing, lighting, pose defaults, must-show items, tier and aspect, plus a short list of scenes. The builder's "Shot" and "Scene" sections come from it.
  - Fit comparison (two garments or models in one image) is out of this version, because the spec, compiler and checks assume one garment and at most one model.
- **Anchors come first:** when no approved anchor exists for the shot's style, colour and GSM, the builder adds an anchor packshot job (standard tier, about $0.10, shown in the estimate) ahead of the requested shot. The requested shot waits until either user approves the anchor. Approving an anchor copies it into `refs/anchors/`; that is part of approval, not brand-kit editing. Cast reference sheets are attached when they exist; until then shots run without them, with the existing warning. "Make it in more colours" therefore includes one anchor per new colour.
- **Estimates show two numbers:** the base cost and the worst case within the repair and regeneration limits (for example $0.13, up to about $1.21). Repairs and regenerations within limits run without a new ask, as in the CLI.
- **"Ask for a change":** the user picks reason codes from `brand/repairs.json`, which runs one repair counted against the limits, or chooses "change choices", which reopens the builder on the same `shot_id` and counts as a regeneration. An optional note is stored with the request but is never sent to the image model as an instruction. A retry after a rejection keeps the same `shot_id`, so limits never reset.
- **Rejection notes become rules with approval:** the brain drafts each rejection note as one positive prompt sentence with its scope. It appears in a "Pending rules" list, and either user can activate it. This is the one place the dashboard writes to `brand/` (`prompt_rules.json`).
- **Colours outside the palette:** if the brief names a colour that is not in the palette ("teal"), "Read the brief" leaves colour open and the colour section opens on the custom picker labelled "teal". The user picks the hex.
- **Coverage comes from a target list:** a targets file lists the website slots the owner wants filled (style, palette colour, technique, placement, shot type). "Missing" means a slot with no approved image. I seed it with a short default list for the owner to edit. Light or dark tone (from palette lightness) is used only for matching designs to garments.
- **Claude usage is logged and capped:**
  - Every brain call is logged with the user, task and credential type, never the credential value.
  - Subscription calls cost nothing per call but are counted.
  - API-key calls get an estimated cost and a per-user monthly cap, set in settings (default $5). This shows in the per-user spend view, separate from the Gemini budget bar.
  - Brain model is configurable: Sonnet 5.5 by default; visual review may be set to a stronger model.
- **Speed for interactive tasks:**
  - Targets: "Fill from my words" in about 5 seconds; idea cards in about 10 seconds, with progress shown.
  - Each user keeps one warm Claude session (a long-lived Agent SDK client) instead of starting Claude Code per call.
  - API-key users may call the Messages API directly for these two tasks.
  - If the spike shows the Claude path is too slow, these two tasks use Gemini Flash, and Claude keeps the creative text and the visual review.
- **Checklist ticks:** either user may tick the visual checklist when the brain's review is unavailable. Approval by either user remains the gate for export.
- **Ask the guide:** a question box on the guide page. Claude answers from the guide text and, when an image or order is open, from its own record (for example "why is this waiting?"). It answers only; it never changes anything.

## How it works

### Components

| Component | Role |
|---|---|
| Web UI | Builder, review, library, settings. Talks only to the app server. |
| App server (Python) | Serves the UI and a JSON API; streams job progress; resolves the current user from one replaceable identity source (localhost means the owner; later Tailscale or Cloudflare Access). |
| Job worker | Runs generation, checks and repairs in the background, one job at a time against the budget. |
| `pipeline` package | Existing code, unchanged in behaviour; gains the additions listed below. |
| Brain adapter (new) | One interface for reasoning tasks, with implementations: Claude via Agent SDK (per-user credential: subscription token or API key) and Gemini Flash (fallback). |
| App database | Users, briefs, choices, jobs, image records, check results, approvals, notes. References files in `out/` and entries in `out/log.jsonl`; does not replace the ledger. |
| Credential store | Per-user Claude credential on the host only, outside git, never logged or sent to the browser. |

### Question sets as data

`studio/questions/images.json` (path is the builder's call) lists the sections, where each section's options come from (catalogue, palette, cast, designs, recipes, scenes), when a section appears (for example model only when shot is on-model, design details only when decoration is chosen) and constraints (size max from the chosen placement; GSM from the chosen style). Future pipelines add their own question file.

### Brain tasks

Each returns structured output. Factual fields are restricted to the enumerated options from the files.

| Task | Input | Output |
|---|---|---|
| Read the brief | free text | pre-filled choices (only valid options), plus what is still unknown |
| Suggest ideas | brief, choices so far, library coverage | 3 idea cards, each a full set of valid choices plus creative scene, pose and mood text |
| Write the creative parts | final choices | scene, pose and mood text within set word limits; the compiler places it |
| Visual review | final image, the shot's checklist | pass or fail per checklist line with a short note; the reviewer confirms by eye |

The next step after checks (approve, finish, repair, composite, regenerate, flag) stays with the existing deterministic `qa.next_action` and its limits. The brain does not override it.

### One image, end to end

1. The user types an idea. "Read the brief" pre-fills choices.
2. The user completes the adaptive sections. The summary and cost update live. Impossible options are greyed with the reason.
3. The server writes the spec from the choices, the recipe and the brain's creative text, then compiles and routes it with the existing code. The prompt is shown read-only.
4. Optional: three free Cloudflare sketches; the user picks a layout.
5. "Approve & create" queues the job. Anything over $1 asks first, as in the CLI.
6. The worker generates the final. The measured checks run: colour, logo verification, stray-text scan. Then the brain's visual review runs, then `qa.next_action`. Repairs and regenerations happen within the limits.
7. The review screen shows the result. Either user approves, asks for a change or rejects with a note; rejection notes become prompt rules as today.
8. Approved images are finished and exported as today, and appear in the library with their caption and record.

"Make it in more colours" turns one approved brief into N specs with one estimate and one approval.

### Client logo upload

- **Where:** the design section's "Upload a logo" tile. Files go under `clients/<client>/`, which is git-ignored, and are never committed, logged in full or sent anywhere except the model calls that need them.
- **Registry:** `clients/designs.json` uses the same fields as `brand/designs/designs.json` (id, client, file, text, colours, suggested size and placement, light or dark tone), plus `source_type` (svg, pdf or png). `designs.design_for` and the logo code look in both registries.
- **Text:** a vision read of the uploaded artwork proposes the text, and the user confirms or corrects it. This confirmed text is what verification checks against. Nothing else about a client logo is typed in.
- **Colours and tone:** ink colours come from the artwork's pixels. Tone (light or dark) follows from them, and the existing tone-mismatch warning applies.
- **By file type:**

| Source | Handling | When verification fails after one repair |
|---|---|---|
| SVG | Used as is, rasterised with resvg as today | Composite from the file, as today |
| PDF | Vector PDF converted to SVG, plus a raster for previews. The user confirms the preview matches the PDF | Composite from the converted vector |
| PNG (or JPG) | Background removed to transparency. Resolution checked against the chosen size; too small is a warning. Marked "raster artwork" | **No composite.** CLAUDE.md allows only a verified render or a composite from the real vector file. The shot is flagged and the dashboard asks for a vector file from the client |

- **Production feasibility** (minimum text height, line width and thread colours per technique) runs only once the factory's limits are recorded in the catalogue. Those numbers are TODO and must come from the Sialkot factory, never be guessed. Until then the dashboard says the check is not set up.

### How to use guide

- **Guide page:** one click from anywhere (a "? Guide" button in the top bar). Its sections:
  1. Start here: what the pipeline does and why, with the direct-API comparison.
  2. Make a website image, in six steps.
  3. Handle a client order.
  4. Read the checks: what colour difference, logo text, shape, ink and size, stray text, and the repair limits mean, using the real thresholds from `brand/qa_checklist.json` and `brand/repairs.json`.
  5. Costs and budget: what is free, what costs money, the caps.
  6. Rules we never break.
  7. When something goes wrong: Claude's limit reached, a shot waiting for its reference packshot, a greyed option, a flagged image.
- **First-visit tour:** about five short pointers on the builder (idea box, sections, summary, sketches, approve). It can be skipped and replayed from the guide.
- **"?" tips:** next to every builder section and every check result, in one or two plain sentences.
- **Kept in step with the code:** the guide is stored as files in the repo (for example `studio/guide/*.md`). Thresholds and limits are read from the same JSON files the pipeline uses, so numbers never go stale. Each build step updates its own guide pages.
- **Separate from the plugin handbook:** the existing "CSC Studio Handbook" artifact stays the guide for Claude Code plugin users.

### Client orders (Clients tab)

One page per client order, in three columns:

1. **What the client sent:** their message plus files, each with a role:
   - artwork (their logo, through the client logo upload above);
   - garment reference (a hoodie they like);
   - layout (their own mockup or sketch);
   - mood (their brand photos).

   Reference, layout and mood images go to the image model as reference images, used as sent with the client's consent.
2. **What we understood:** each requirement (style, colour, GSM, each decoration with placement, size and technique, quantity and size split), showing where it came from.
   - Items are ticked, or marked "ask client" when missing or when we can't make them. Example: "34 cm back print: full back max is 30 cm, offer 30 cm".
   - A button drafts a reply to the client listing the open questions.
   - Requirements map onto catalogue options only. Colour from a photo is sampled and confirmed by the user.
3. **Outputs:**
   - mockups (front, back, decoration close-up; optional on-model);
   - the client approval PDF;
   - a **supplier sheet** for the factory;
   - an artwork pack with the original vector files.

   Nothing is sent automatically; the dashboard prepares the files and a user sends them.

**If the client sends no images,** the order is built from their words with sample mockups, exactly like the image builder.

**The supplier sheet (first version):**
- flat front and back views with numbered placement callouts;
- a table: style and construction from the catalogue, GSM, colour hex (Pantone to match), each decoration's technique, placement and size, trims, labels, and the size split;
- the line "Pictures are AI concept renders for reference; where a picture and the table disagree, the table is correct."

What the factory needs on it is an open question for the owner. See "Open questions".

The existing stray-text scan flags any text in a render that is not in the order's own artwork, for example another brand's logo carried over from a reference photo. A user decides what to do; it is not blocked.

**Later, not in this version:** compare the factory's pre-production sample photo with the approved mockup (colour difference, logo text, position and size) as a "sample check" report.

### Additions to the pipeline package

- `brand/recipes.json` and compiler support for recipe defaults.
- Stray-text scan: a Gemini vision read of the whole image. Any text not in the spec fails with a new reason code (for example `unintended_text`) in `brand/repairs.json`.
- Batch: N specs from one brief with a combined estimate.
- Coverage: which style, colour tone, technique, placement and shot combinations have approved images.

## Build order

Each step also writes its part of the How to use guide; the first-visit tour arrives with the image builder.

1. **Spike (throwaway).**
   - Prove the Agent SDK runs Claude Code headless on the host under two different users' subscription tokens (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`), each request using only its user's token. Also prove the same path with an API key.
   - Confirm the precedence of credentials when the host machine is itself logged in, that the `csc-studio` plugin loads, that Claude can look at a local image, and the typical latency.
   - The docs do not confirm that the SDK honours a per-call OAuth token, so this spike decides whether per-user subscription works on one host.
   - If it fails, stop and bring the owner options:
     - the teammate uses their own API key;
     - a small brain worker on the teammate's own PC, logged in to their own Claude, connects to the host;
     - Gemini Flash for the teammate's brain tasks.
2. **Foundations:**
   - `recipes.json`;
   - the question-set format;
   - the brain adapter with the Gemini fallback;
   - the stray-text scan;
   - the app server, database and worker skeleton.
3. **Builder screen.**
4. **Client logo upload:** SVG, PDF, PNG intake, the clients registry, text confirmation, and the verification and composite rules above.
5. **Review screen and library.**
5a. **Client orders:** the Clients tab, reading the client's message and files, "ask client" items and the reply draft, mockups, the client PDF, the supplier sheet and the artwork pack.
6. **Ideas from coverage** and **make it in more colours.**
7. **Team access:**
   - choose Tailscale or Cloudflare with a domain;
   - users;
   - a settings page for each user's Claude credential (subscription token or API key);
   - per-user spend view.

   The per-user token part of the spike can wait until this step if the teammate's token is not available earlier.

## Done when

- From "image of hoodie" to a reviewed prompt and cost estimate by clicking only, with no facts typed.
- Every option shown exists in the files; impossible ones are greyed with the reason.
- Both users can create. Each user's Claude calls run on their own credential, and the log records which credential type was used, never the value.
- Budget caps and repair limits behave exactly as in the CLI.
- Images reach the review screen with their check results; nothing is exported or sent without approval from one of the two users.
- The Claude Code plugin still works on the same data.

## Left to the builder

- Web framework, frontend approach (a no-build-step frontend is preferred unless there is a reason), database engine (SQLite is enough for two users), progress streaming method, file layout under a new `studio/` folder, and the database schema.
- Exact wording and word limits for brain prompts, within the anti-goals above.
- PDF-to-SVG and background-removal libraries. Check their licences: PyMuPDF is AGPL, which is acceptable for this internal tool but should be noted.

## Open questions (do not block the first build steps)

- **What the factory needs on the supplier sheet:** what is sent today, and what the factory most often gets wrong or asks about. This is needed before build step 5a.
- **How the teammate reaches the dashboard:** Tailscale, or Cloudflare with a domain. This is needed before build step 7.
- **The factory's decoration limits** (minimum text height, line width, thread colours per technique). The feasibility check stays off until these are recorded.
