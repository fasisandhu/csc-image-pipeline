# CSC Studio: Image Builder Implementation Plan (plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build screen 1 of the approved design, the image builder, as a working local web page on top of the plan-1 server. A user types an idea, clicks through adaptive choices, sees a live summary with prompt and cost, makes three free sketches, picks one, and creates the final image. A built-in guide, a first-visit tour and "?" tips explain the pipeline. Brief reading answers fast because each user keeps a warm Claude session.

**Architecture:**
- **Front end:** no build step. `studio/static/` holds the HTML, CSS and JS, plus a vendored, version-pinned Alpine.js and marked.js. FastAPI serves it from the existing `studio/app.py`.
- **Server additions are thin:**
  - file serving, limited to output images;
  - structured budget figures;
  - a job list;
  - layout sketches attached to finals;
  - anchor-first gating;
  - a warm Claude client per user.
- Every rule stays in `pipeline/` and the plan-1 boundary checks. The browser never sees a key or credential.

**Tech Stack:** Python 3.13 (`.venv`), FastAPI 0.163.0 + uvicorn 0.54.0, sqlite3, claude-agent-sdk 0.2.182, Alpine.js 3 (vendored, exact version), marked (vendored, exact version), plain CSS from the approved mockup, pytest 8.4.2 + httpx 0.28.1.

**Spec:** `docs/superpowers/specs/2026-11-15-studio-dashboard-design.md`, build step 3 ("Builder screen"), plus the guide items listed for it. The approved mockup is `docs/superpowers/specs/assets/2026-11-15-studio-dashboard-mockup.html` (screen 1). Plan 1 is `docs/superpowers/plans/2026-11-15-studio-foundations.md` and is merged.

## Global Constraints

- Every plan-1 global constraint still applies:
  - facts only from `catalogue/` and `brand/`;
  - the $9 Gemini cap, and runs over $1.00 confirmed by either user;
  - Cloudflare limited to 9,500 neurons a day;
  - no retry loops;
  - keys and credentials never in the browser, the DB, logs or API responses;
  - 127.0.0.1 only;
  - exact pins;
  - existing CLIs, the plugin and the tests keep passing.
- **The browser:**
  - receives only option ids, labels, reasons, prompts, costs and image URLs;
  - never receives a key, a credential, a credential type, `.env` content or a file path outside `out/drafts/` and `out/finals/`.
- **No runtime CDN.** Every third-party script is vendored under `studio/static/vendor/`, pinned to an exact version at least two weeks old, with the version in the file name and in `studio/static/vendor/VERSIONS.txt`.
- **No `alert()`, `confirm()` or `prompt()`.** Confirmations are built into the page.
- **Copy is plain language for non-technical teammates**, using the mockup's wording where it exists. Errors say what happened and what to do, with no stack traces or raw JSON.
- **Every displayed AI image shows its caption:** "Concept render. Production photos are shot for every order." On-model shots say "AI model, concept render".
- **Desktop-first** (mobile-first is a non-goal of the spec). The page must still not scroll sideways at 1024 px wide.
- **Brief reading:** `STUDIO_FAST_TASKS=claude` uses a warm Claude session per user (owner decision, 2026-11-15). The Gemini fallback is unchanged.

## Review Focus

1. **A brief that names a colour outside the palette ("teal"):** the custom colour picker opens with the name "teal" filled in and the hex left for the person to choose; nothing is auto-filled. Test: Task 6 (API) plus the Task 8 walkthrough.
2. **A shot that needs a reference packshot that doesn't exist yet:** "Approve & create" is refused with a plain message and a one-click "Make the reference packshot first" option. A final is never created without its anchor. Test: Task 2.
3. **A run over $1, or a run that would cross the cap:** an in-page confirm appears for over $1; at the cap, a plain message is shown. No job is queued without confirmation, and there is never a browser dialog. Tests: Task 2 (API) and Task 7 (UI walkthrough).
4. **Claude slow or unavailable during brief reading:** the answer comes from Gemini and the page says so. A spinner shows, the request times out with a message, and the page never hangs. Tests: Task 3 (warm client failure leads to fallback) and Task 6.
5. **A page reload while a job runs:** the job panel restores from the job list and polling resumes. No duplicate job is created. Tests: Task 2 (job list) and the Task 8 walkthrough.

Each line has its test in the owning task.

## File map

| Path | Responsibility |
|---|---|
| `pipeline/qa.py` | `spec_path` also looks in `out/specs/` |
| `studio/worker.py` | writes `out/specs/<shot_id>.json`; passes the chosen layout into finals |
| `studio/db.py` | `layout` column (migration), `list_jobs` |
| `studio/app.py` | budget figures, job list, files route, layout + anchor gating, static mount, `/` |
| `studio/brain/warm.py` | `WarmClaude`: one warm `ClaudeSDKClient` per user and schema |
| `studio/brain/__init__.py` | `get_brain(user, fast=True)` uses `WarmClaude` |
| `studio/questions/images.json` | adds a `tip` per section |
| `studio/guide/make-a-website-image.md`, `studio/guide.py` | new guide page |
| `studio/static/index.html`, `styles.css`, `app.js`, `vendor/*` | the builder page |
| `tests/test_builder_api.py`, `tests/test_warm.py` | tests |

---

### Task 1: Dashboard specs on disk (spec decision M1)

**Files:**
- Modify `pipeline/qa.py` (`spec_path`) and `studio/worker.py`.
- Test: `tests/test_builder_api.py`.

**Interfaces:**
- `qa.spec_path(shot_id) -> Path` returns `specs/<id>.json` if it exists, else `out/specs/<id>.json`. The error for a missing spec names both places.
- `worker` writes `out/specs/<shot_id>.json` (pretty JSON) for each `final` job before generating.

**Decision:** dashboard specs live in the git-ignored `out/specs/`, because `specs/` is tracked and holds hand-written specs. CLI checks (`python -m pipeline.qa`, `logo verify`, `finish`) then work on dashboard finals.

- [ ] **Step 1: Failing tests:**
  - `test_spec_path_falls_back_to_out_specs`: a spec written to `tmp out/specs` resolves. Monkeypatch `ROOT` paths or use a real temp file under `out/specs` and clean it up.
  - `test_worker_writes_spec_for_final`: the fake runner checks that the file exists when it is called.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_builder_api.py -v`. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5:** Commit: `feat: dashboard specs in out/specs so CLI checks work on dashboard finals`.

### Task 2: Server additions for the builder

**Files:**
- Modify `studio/db.py`, `studio/app.py` and `studio/worker.py`.
- Test: `tests/test_builder_api.py`.

**Interfaces:**
- `db`:
  - The `layout TEXT` column is added by `connect()` if missing (`ALTER TABLE`).
  - `create_job(..., layout: str | None = None)`.
  - `list_jobs(conn, user, limit=20) -> list[dict]`, newest first.
- `GET /api/health` → `{"ok", "budget": str, "gemini_usd", "cap_usd", "cf_neurons", "cf_cap"}`, built from `ledger` functions and constants.
- `GET /api/jobs?limit=20` → `{"jobs": [...]}` for the current user. The job dicts are the same as `GET /api/jobs/{id}`.
- `GET /api/files/{path:path}` serves a file only if `(ROOT/path).resolve()` lies under `ROOT/out/drafts` or `ROOT/out/finals`, is a file, and has suffix `.jpg`, `.jpeg`, `.png` or `.webp`. Anything else gets 404 `{"error"}`.
- `JobIn` gains `layout: str | None`:
  - only valid for `kind == "final"`;
  - must be one of the `result.files` of a `done` drafts job by the same user whose `spec.shot.shot_id` equals this job's shot id;
  - otherwise 422 `{"error": "pick a sketch from this shot"}`.
- **Anchor-first (design call C1):** `POST /api/jobs` with `kind == "final"` and `needs_anchor(answers)` returns 409:
  - `{"error": "This colour has no approved reference packshot yet. Make it first, then this shot can use it.", "needs_anchor": true, "anchor_answers": {...}}`;
  - `anchor_answers` is the same `purpose`, `catalogue`, `colour`, `gsm`, plus `recipe: "packshot-front"` and `decoration: "none"`.

  Drafts are allowed without an anchor (they're free and never published). Approving an anchor into `refs/anchors/` arrives with the Review screen; until then the owner uses the CLI. The suggested next plan is the Review screen, ahead of client logo upload, so that approvals have a home. The owner confirms this order.
- **Worker:** final jobs call `compile_spec(spec, [job["layout"]] if job["layout"] else [])`.
- **Static:** mount `studio/static` at `/static`, and `GET /` returns `studio/static/index.html`. Both stay behind the trusted-host and identity rules.

- [ ] **Step 1: Failing tests:**
  - `test_health_has_budget_figures`
  - `test_job_list_newest_first_and_per_user` (Review Focus 5)
  - `test_files_serves_draft_image`
  - `test_files_refuses_traversal_and_other_dirs` (`out/../.env`, `out/log.jsonl`, `specs/x.json` → 404)
  - `test_final_needs_anchor_409_with_anchor_answers` (Review Focus 2)
  - `test_packshot_front_final_not_gated`
  - `test_layout_must_be_a_draft_of_this_shot`
  - `test_final_job_passes_layout_to_compile` (fake runner sees a layout ref)
  - `test_index_served_at_root`
  - `test_over_one_dollar_still_needs_confirm_with_layout` (Review Focus 3)
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite. Expect all tests to pass.
- [ ] **Step 5:** Commit: `feat: builder API — budget figures, job list, image files, layout sketches, anchor-first`.

### Task 3: Warm Claude session per user (owner decision)

**Files:**
- Create `studio/brain/warm.py`.
- Modify `studio/brain/__init__.py`.
- Test: `tests/test_warm.py`.

**Interfaces:**
- `WarmClaude(user, credential, token_env, model=DEFAULT_MODEL, idle_s=600)` has the same `ask_json` signature as `ClaudeBrain`.
- `__repr__` shows no credential.
- It reuses `ClaudeBrain`'s env building, cap check, cost recording, scrubbing and brain records (refactor shared parts into `ClaudeBrain` methods rather than copying them).
- **One background asyncio loop thread per process.** One `ClaudeSDKClient` per `(user, sha256(schema))`:
  - opened lazily with the slim options, `output_format` for that schema, and the user's env;
  - reused for later calls;
  - closed after `idle_s` without use.
- **`ask_json` behaviour:**
  - submits `client.query(prompt)` and reads `receive_response()` up to the `ResultMessage` through `asyncio.run_coroutine_threadsafe`;
  - 60 s timeout;
  - on any error, closes that client and raises `BrainError` with the cost. No retry; the `FallbackBrain` falls back.
- Images are not supported. If images are passed, delegate to `ClaudeBrain`.
- `get_brain(user, fast=True)`, when `STUDIO_FAST_TASKS` is `claude`, returns `FallbackBrain(WarmClaude(...), GeminiBrain())`. Otherwise it behaves as now.
- Clients close at server shutdown (`studio.__main__` calls `warm.shutdown()` after `serve` returns).

- [ ] **Step 1: Failing tests** with a fake client class patched in for `ClaudeSDKClient`:
  - `test_client_reused_for_same_user_and_schema` (1 connect, 3 queries)
  - `test_separate_clients_per_user_and_schema`
  - `test_error_closes_client_and_falls_back` (Review Focus 4: next call opens a new client; no retry inside one call)
  - `test_idle_client_closed`
  - `test_records_like_claude_brain_without_secrets`
  - `test_fast_brain_is_warm_when_claude_mode`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite. Expect all tests to pass.
- [ ] **Step 5: Live check** (throwaway script, not committed): 4 `read_brief` calls through `get_brain("owner", fast=True)`. Report each time; target a median under 3 s for calls 2–4. Never print `.env` values.
- [ ] **Step 6:** Commit: `feat: warm Claude session per user for quick tasks`.

### Task 4: Page shell, styles and vendored libraries

**Files:**
- Create `studio/static/index.html`, `styles.css`, `app.js` and `vendor/` (Alpine.js 3.x, marked) plus `VERSIONS.txt`.
- Test: an extension of `tests/test_builder_api.py` (static assets served; `VERSIONS.txt` names exact versions).

**Content:**
- **Styles:** CSS tokens and components are taken from the approved mockup's screen 1 (`docs/superpowers/specs/assets/2026-11-15-studio-dashboard-mockup.html`, `.ui` styles).
- **Top bar:** "CSC Studio" logo; tabs "Create image" (active), "Clients", "Review", "Library" and "Brand kit", shown as "coming in a later step" when clicked; "Ads", "Social" and "SEO" dimmed; a budget pill ("This month $X of $9", from `/api/health`); a "? Guide" button.
- **Layout:** two columns, builder on the left and a sticky summary on the right.
- **State:** Alpine store `studio` with `answers`, `sections`, `preview`, `job`, `jobs`, `budget`.
- **Persistence:** `localStorage` holds only UI conveniences (tour seen, last answers draft), wrapped in try/catch.

- [ ] **Step 1:** Write the failing test (`GET /static/app.js` returns 200 with a JS content type; `VERSIONS.txt` lines match `name==x.y.z`).
- [ ] **Step 2:** Run it. Expect FAIL.
- [ ] **Step 3:** Implement. Download the pinned vendor files once (cdnjs) into `vendor/` and commit them.
- [ ] **Step 4:** Run the suite. Expect all tests to pass. Then do a visual check in a browser (Playwright MCP if available: open `http://127.0.0.1:8765/`, screenshot) and attach the screenshot path to the report.
- [ ] **Step 5:** Commit: `feat: studio page shell with vendored Alpine and mockup styles`.

### Task 5: Builder sections

**Files:**
- Modify `studio/static/app.js`, `index.html`, `styles.css` and `studio/questions/images.json` (add `tip`).
- Test: a `tests/test_builder_api.py` check that every section has a non-empty `tip`.

**Behaviour:**
- Sections render from `POST /api/questions` in order. Only `visible` sections show.
- **A newly visible section** gets a "NEW" badge plus its reason. The reason comes from the section's `show_if`: "you picked on a model" for cast and scene, "you picked a design" for design, technique, placement and size.
- **Kinds:**

  | Kind | Rendering |
  |---|---|
  | `choice` (catalogue) | tiles with the mockup's hoodie drawings: classic, oversized, zip |
  | `choice` (others) | segmented buttons or cards: cast cards with initials; design tiles with `brand_name` |
  | `colour` | named swatches from the palette options, plus "Custom": a name field (max 40 characters) and a hex field validated `^#[0-9A-Fa-f]{6}$` with a live swatch |
  | `size` | a slider from 1 to `max`, showing "maximum is N cm" |

- **Disabled options** show greyed with their `reason` visible as small text (not hover-only).
- **"?" tip** beside each section title, toggling its `tip` text.
- **Each change:** re-post `/api/questions` (debounced 150 ms) and drop answers the server drops. When `missing` is empty, post `/api/preview` (debounced 300 ms).

- [ ] **Step 1:** Write the failing tip test.
- [ ] **Step 2:** Run it. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the suite. Expect all tests to pass. Then do a browser walkthrough: pick oversized, olive, 380, on-model back, Maya, a scene, design Static Bloom, screen print, full back, 30 cm. Screenshot each new section appearing.
- [ ] **Step 5:** Commit: `feat: adaptive builder sections with reasons and tips`.

### Task 6: Fill from my words and the live summary

**Files:**
- Modify `studio/static/app.js`, `index.html` and `styles.css`.
- Test: `tests/test_builder_api.py`. Brief API behaviour is already covered; add `test_brief_reports_provider_and_fallback`, so the UI can say "answered by Claude" or "answered by Gemini (backup)" and the provider appears in the response without a credential type.

**Behaviour:**
- **"What do you want to make?" box and "✦ Fill from my words"** post `/api/brief` with the current answers.
  - A spinner shows, with the request aborted after 30 s and the message "That took too long; you can still pick below".
  - Picked-up answers show as "Picked up: ✓ olive ✓ design on the back".
  - `colour_hint` opens the Custom colour with the name filled in and the hex empty, with the note "pick the exact shade" (Review Focus 1).
  - A 503 shows "Our AI helpers are busy right now; pick the options below."
- **Summary card ("Your image"):**
  - each answered section with an "edit" link that scrolls to it, and missing sections listed as "still to choose";
  - "▸ Show the full prompt" disclosure;
  - estimated cost `base_usd` with "up to `worst_usd` if every allowed fix runs";
  - the model tier note;
  - compiler `warnings` rendered as plain notes;
  - the budget bar.

- [ ] **Step 1:** Write the failing test.
- [ ] **Step 2:** Run it. Expect FAIL.
- [ ] **Step 3:** Implement. (If `/api/brief` lacks the provider field, add `provider` and `fallback` to its response in `app.py`; `read_brief` already returns them.)
- [ ] **Step 4:** Run the suite. Expect all tests to pass. Browser walkthrough: type "olive hoodie with something on the back", then type "a teal zip hoodie" and check the custom colour opens with "teal".
- [ ] **Step 5:** Commit: `feat: fill from my words and live summary`.

### Task 7: Sketches, approve & create, job panel

**Files:**
- Modify `studio/static/app.js`, `index.html` and `styles.css`.
- No new server endpoints.

**Behaviour:**
- **"See 3 free sketches"** posts a `drafts` job. The job panel polls `GET /api/jobs/{id}` every 2 s until `done` or `failed`. Thumbnails come from `/api/files/...`; clicking one selects it ("✓ chosen layout").
- **"Approve & create"** posts a `final` job with `layout` set to the chosen sketch, if any. The response handling:

  | Response | What the page does |
  |---|---|
  | over $1 | an in-page confirm panel ("This will cost about $X. Create it?" with Create / Cancel), then resends with `confirm: true` |
  | `needs_anchor` | shows the message plus a "Make the reference packshot first" button, which posts a `final` job with `anchor_answers` |
  | other 409 or 422 | shows the plain `error` text |

- **Done final:** a large image with its caption (on-model shots get "AI model, concept render"), plus the note "Checks and approval come in the Review tab (next step)."
- **"Recent images":** 6 entries from `GET /api/jobs` with status pills. On page load, any `queued` or `running` job resumes polling (Review Focus 5).
- **Buttons** disable while a request is in flight, so a double click can't create two jobs.

- [ ] **Step 1:** API behaviours are covered in Task 2. Write a short manual test script in the report (steps and expected results) before implementing.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the suite. Expect all tests to pass. Browser walkthrough with the server running and real Cloudflare drafts (free):
  - make sketches;
  - pick one;
  - press Approve & create on a shot that needs an anchor and see the gate;
  - reload mid-job and see polling resume.

  Do not create a paid final in this task.
- [ ] **Step 4:** Commit: `feat: sketches, approve and create, job panel`.

### Task 8: Guide page, first-visit tour and live demo

**Files:**
- Create `studio/guide/make-a-website-image.md`.
- Modify `studio/guide.py` (`PAGES`), `studio/static/*` and `tests/test_guide.py`.

**Content and behaviour:**
- **Guide view ("? Guide"):** a left list of pages (Start here, Make a website image, Read the checks, Rules) rendered with the vendored marked. Raw HTML in the Markdown is disabled or sanitised.
- **"Make a website image"** has the six steps from the design mockup's guide screen, using `{{placeholders}}` for any numbers.
- **First-visit tour:** 5 pointers (idea box, sections, summary, sketches, approve), each with Next and Skip. It shows once (`localStorage` flag in try/catch) and "Replay the tour" in the guide restarts it. It respects `prefers-reduced-motion`.

- [ ] **Step 1: Failing tests:** `test_make_a_website_image_page_renders` (no `{{` left) and `test_guide_pages_list`.
- [ ] **Step 2:** Run them. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite. Expect all tests to pass.
- [ ] **Step 5:** Commit: `feat: guide view, website-image guide page, first-visit tour`.
- [ ] **Step 6: Live demo** (the controller runs it with the owner's go-ahead; expected cost about $0.10):
  1. Start `python -m studio`.
  2. In the browser: tour, then fill from words.
  3. Choose oversized, olive, 380, product only (packshot-front), plain.
  4. Make sketches, pick one, Approve & create. This is one standard final, about $0.10, and becomes the olive anchor candidate.
  5. See the final with its caption.
  6. Run `python -m pipeline.qa <final>` from the CLI to prove M1.
  7. Screenshots go into the step report.

## Out of scope (later plans)

- Review screen, checks shown on images, approve or reject (which also covers approving an anchor into `refs/anchors/`), and the library. Suggested as plan 3, ahead of client logo upload; the owner confirms.
- Client logo upload: its own plan.
- Ideas from coverage and "make it in more colours": a later plan.
- Clients tab and teammate access.

### Task 7b: Ask only what's missing (owner request, 2026-11-15)

**Files:**
- Modify `studio/static/app.js`, `index.html` and `styles.css`.
- Test: the browser walkthrough (no server change).

**Behaviour:**
- **Answered sections fold away.** Once a visible section has an answer, it collapses to one line, e.g. "Colour: Olive · Change", whether the answer came from the person or from "Fill from my words".
- **Only the gaps stay open.** Unanswered required sections stay open. After a brief, the page scrolls to and focuses the first unanswered section. When nothing is missing, it scrolls to the summary.
- **The section in use stays open.** The section the person is using stays open until they act in another section or press "Done", so it never jumps away mid-choice.
- **Reopening:**
  - "Change" re-opens one section.
  - "Show all options" / "Show only what's left" toggles everything.
  - Folded rows keep their "?" tip available.
- **Unchanged:** not-applicable sections stay hidden as now; "NEW" badges still show on sections that just appeared.
- **Accessibility:** collapse and expand are buttons with `aria-expanded`, and focus moves sensibly.

- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Browser walkthrough:
  - "olive oversized hoodie with something on the back" leaves only the unanswered sections open;
  - Change re-opens a section;
  - Show all works;
  - keyboard only.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: builder asks only what is still missing`.
