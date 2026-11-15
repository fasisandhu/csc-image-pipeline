# CSC Studio: Review Screen Implementation Plan (plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After "Approve & create", each final image is checked automatically and fixed within the limits. A person then sees the image in a Review tab with the check results drawn on it and before/after for fixes. They approve, approve it as a reference packshot, ask for a change, or reject with a note; rejection notes become prompt rules that either user switches on. Approved images land in a Library with their full record and a web export.

**Architecture:**
- **A review chain per final job.** A new `review_steps` table holds automatic follow-up steps (check, finish, repair, regenerate, composite) linked to the final job. The existing single worker runs them in order, through the existing pipeline functions, so every rule still applies:
  - the router and the cap;
  - the repair limits counted from the ledger;
  - logo verify-or-composite;
  - no retry loops.
- **Pipeline code is reused,** refactored only where a CLI body becomes a function: `repair_image`, `record_verdict(by=)`.
- **The visual checklist** becomes a brain task, with Claude on the user's plan and Gemini as the fallback. It returns a verdict per checklist line plus repair codes chosen only from `brand/repairs.json`.
- **The page gets two new tabs (Review, Library)** and a "Pending rules" panel in the same no-build Alpine app.

**Tech Stack:** Python 3.13 (`.venv`), FastAPI 0.163.0, sqlite3, claude-agent-sdk 0.2.182, google-genai 2.34.0, Alpine 3.14.1 (vendored), pytest 8.4.2, httpx 0.28.1.

**Spec:**
- Design: `docs/superpowers/specs/2026-11-15-studio-dashboard-design.md`, build step 5 ("Review screen and library"). The owner moved it ahead of logo upload on 2026-11-15.
- Design sections that apply:
  - "One image, end to end", steps 6–8;
  - calls C1 (anchors), C3 ("Ask for a change"), C4 (rejection notes become rules), C11 (fallback);
  - Y5 (either user approves).
- Mockup screen 2 (review + library): `docs/superpowers/specs/assets/2026-11-15-studio-dashboard-mockup.html`.
- Carry-overs from plan 2's final review are in Task 1.

## Global Constraints

- **Plan 1 and plan 2 constraints still apply:**
  - facts only from `catalogue/` and `brand/`;
  - $9/month Gemini cap; a run over $1.00 needs confirmation from either user;
  - Cloudflare limited to 9,500 neurons a day;
  - no retry loops: two failures on a chain step stop the chain and it becomes "needs a person";
  - keys and credentials never in the browser, DB, logs or API responses;
  - the browser never receives a credential type, an env variable name, or a path outside the served image folders;
  - 127.0.0.1 only; exact pins;
  - no `alert`/`confirm`/`prompt`; `x-text` only, no `x-html`;
  - existing CLIs, the plugin and the tests keep working.
- **Repair and regeneration limits** come from `brand/repairs.json`: `repairs_per_render` 2 and `regenerations_per_shot` 2, counted from `out/log.jsonl` exactly as `qa.next_action` does. A logo gets one repair, then composite. Steps within limits run without a new ask (design C2/C3). The worst case was shown before "Approve & create".
- **Approval belongs to a person.** Either user can approve (CLAUDE.md, Y5). Nothing is approved, exported, made into a reference packshot, or turned into an active prompt rule without a person's click. Every verdict records `by: <user>`.
- **Reference packshots:**
  - Only a plain `packshot-front` final whose job had no sketch layout can become one.
  - It is written as `refs/anchors/<catalogue>_<colour_id>_<gsm>_front.png`.
  - An existing anchor is never overwritten without an explicit "Replace" click.
- **Every displayed AI image shows its caption.** Exports carry caption and alt text (`finish.export`).
- **Served image folders:** `out/drafts`, `out/finals`, `out/reject` (images only) and `out/export` (`.webp`/`.png`/`.jpg` only). Containment rules are unchanged.

## Review Focus

1. **A check step's provider call fails twice, or the cap is reached mid-chain:** the chain stops, the item shows "Needs a person" with a plain reason, nothing is retried, and nothing more is spent. Test: Task 3.
2. **The limits are reached:** after 2 repairs on one render and 2 regenerations of one shot, the chain flags instead of spending again, counting ledger entries made by the CLI too. Test: Task 3.
3. **Approve as reference packshot on a final that used a sketch, is decorated, or isn't packshot-front:** refused with a reason, and nothing is written to `refs/anchors`. An existing anchor needs "Replace". Test: Task 4.
4. **Two people (or two tabs) act on the same item at once,** for example approve and reject, or two "Ask for a change" clicks: exactly one verdict or one repair step is accepted, the other gets 409 with "already decided" or "a fix is already running". Test: Task 4.
5. **An AI visual-review answer names a reason code that doesn't exist, or a checklist line that doesn't exist:** it is dropped, and the item never auto-approves on an invalid or empty answer. An empty or invalid review means "needs a person", not "pass". Test: Task 2.

---

### Task 1: Carry-overs from plan 2

**Files:**
- Modify `tests/test_builder_api.py`, `tests/test_app.py`, `studio/app.py` and `studio/worker.py`.
- Create `studio/notes.py`.
- Test: `tests/test_builder_api.py`, `tests/test_notes.py`.

**Interfaces:**
- `studio.notes.plain_warning(text: str) -> str | None` maps compiler warnings to plain sentences; `None` drops a warning not meant for the page. Rules:

  | Warning | Plain sentence |
  |---|---|
  | `catalogue <id>: unconfirmed <x>` or `catalogue <id>: <x> missing` | "Some catalogue details for this style are still being confirmed with the factory." (once per preview, deduplicated) |
  | `cast <Name>: no approved reference sheet yet…` | "<Name> has no approved reference photo yet, so the face may vary between images." |
  | `paragraph prompt is N words…` | "The prompt is a little long; the result may ignore a detail." |
  | `set style_block differs…` | `None` |
  | anything else | `file_names_only(text)` |

- `studio.app.public_text(text: str) -> str`: `file_names_only`, plus every env-style name (`[A-Z][A-Z0-9_]{2,}`) that starts with `STUDIO_` or contains `KEY`, `TOKEN` or `SECRET` replaced by "a setting". It is used for job `error` and preview warnings.

**Tests:**
- Anchor-gate tests in `tests/test_builder_api.py` and `tests/test_app.py` no longer depend on files in `refs/anchors/`. Monkeypatch `studio.app.find_anchor` (or `pipeline.specs.find_anchor` where it is used) to return `None` or a path explicitly.
- Job error text containing "gemini: GEMINI_API_KEY not set" reaches the browser as "gemini: a setting not set". The DB keeps the original.
- `test_plain_warnings` covers each mapping row.

**Steps:**
- [ ] **Step 1:** Write the failing tests and run them. Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `fix: plan-2 carry-overs — pinned anchor tests, no setting names or jargon on the page`.

### Task 2: Checks engine and the visual review brain task

**Files:**
- Create `studio/review.py`.
- Modify `studio/tasks.py` (add `visual_review`) and `pipeline/finish.py` (`record_verdict(..., by="user")`, keyword argument with a default so the CLI is unchanged).
- Test: `tests/test_review.py`.

**Interfaces:**
- `studio.tasks.visual_review(image: Path, checklist: list[str], brain, user="owner") -> dict` returns `{"lines": [{"text", "ok": bool, "code": str|None, "note": str}], "provider", "fallback"}`:
  - The schema enumerates the allowed codes (keys of `brand/repairs.json` `codes`).
  - Lines whose `text` is not in `checklist`, and codes not in the enum, are dropped.
  - Missing checklist lines come back as `ok: None` ("not checked").
- `studio.review.run_checks(image: Path, shot_id: str, user: str) -> dict` returns `{"colour": {...}, "stray": {...}, "logo": {...}|None, "visual": {...}, "codes": [...], "next": action, "next_code": code|None, "complete": bool}`:
  - `colour` comes from `qa.colour_check(image, hex, on_model)`.
  - `stray` comes from `qa.stray_text_check(image, qa.allowed_texts(spec))`.
  - `logo` comes from `logo.verify(image)`, only when the spec has a `generate_and_verify` decoration.
  - `visual` comes from `visual_review(...)` with `get_brain(user)` (not fast; images use `ClaudeBrain` with the Read gate).
  - `codes` = colour code + stray `unintended_text` + logo codes + visual codes, deduplicated.
  - `next, next_code` = `qa.next_action(ledger.find(image), codes)`.
  - `complete` is False when any visual line is `None`, or the visual review failed.
  - **Rule (Review Focus 5):** `complete` False forces `next = "flag"`, never "approve".
  - It records a ledger `qa` event (`by: "studio"`, codes, next) exactly like the CLI does. It does not move files; the chain step does.

**Tests** (fake brain and fake vision: no paid calls):
- `test_all_pass_means_approve_candidate`
- `test_invalid_codes_and_lines_dropped` (Review Focus 5)
- `test_missing_visual_lines_flag_not_approve` (Review Focus 5)
- `test_logo_checked_only_when_decorated`
- `test_codes_feed_next_action` (`colour_drift` → `finish`; `drawcord_shape` → repair)
- `test_record_verdict_by_user` (default "user" unchanged; `by="owner"` recorded)

**Steps:**
- [ ] **Step 1:** Write the failing tests and run them. Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: checks engine with AI visual review limited to known codes`.

### Task 3: The automatic fix chain

**Files:**
- Modify `studio/db.py` (`review_steps` table; `jobs.review` column), `studio/worker.py` and `pipeline/repair.py` (extract `repair_image(image: Path, code: str) -> tuple[Path, GenerateResult]`; `main()` calls it with identical behaviour and output).
- Test: `tests/test_chain.py`.

**Interfaces:**
- `review_steps` columns:
  - `id`, `job_id` (the final job)
  - `kind`: `check` | `finish` | `repair` | `regenerate` | `composite`
  - `status`: `queued` | `running` | `done` | `failed`
  - `input` (image path), `output` (image path), `code`
  - `result` (JSON), `error`, `cost_usd`, `created`, `updated`
- `jobs.review` (TEXT) takes one of: `checking`, `fixing`, `ready`, `needs_person`, `approved`, `rejected`, `anchor`.
- `db` functions:
  - `add_step(conn, job_id, kind, input, code=None) -> int`;
  - `next_step(conn)` (oldest queued step, after queued jobs);
  - `claim_step`;
  - `set_step`;
  - `steps_for(conn, job_id)`;
  - `set_review(conn, job_id, state, **fields)`.
- **Worker rules:**
  - A done `final` job enqueues a `check` step on its file and sets review `checking`.
  - A done `check` step maps `next` as follows:

    | `next` | What happens | review state |
    |---|---|---|
    | `approve` | nothing more | `ready` |
    | `finish` (`colour_drift`) | enqueue `finish` (`finish.colour_correct`, free) | `fixing` |
    | `repair <code>` | enqueue `repair` | `fixing` |
    | `regenerate` | enqueue `regenerate` (a new final via the job's spec and layout, routed and logged like a final) | `fixing` |
    | `composite` | if an approved anchor source exists for the garment, enqueue `composite` (logo composite from the real artwork onto that source, logged under the shot); otherwise flag | `fixing`, or `needs_person` |
    | `flag` | nothing more | `needs_person` |

  - A done `finish`, `repair`, `regenerate` or `composite` step enqueues a `check` on its output.
  - **A failed step (any exception)** sets review `needs_person` with `public_text(error)`. Nothing is re-queued.
  - **Paid steps (`repair`, `regenerate`)** go through `router.route` first. A route other than "run" sets `needs_person` with the reason (Review Focus 1).
  - **The CLI's "move to out/reject" is kept:** when a check's `next` is `repair`, `regenerate`, `flag` or `composite`, the checked image moves to `out/reject` and is logged "moved", exactly as `qa.main` does, so the limits count the same.

**Tests** (fake runner, fake checks; in-memory ledger):
- `test_pass_goes_ready`
- `test_drift_runs_free_finish_then_rechecks`
- `test_repair_then_pass`
- `test_two_repairs_then_regenerate_then_flag` (Review Focus 2, with ledger entries)
- `test_cap_mid_chain_needs_person_no_spend` (Review Focus 1)
- `test_failed_step_never_retried`
- `test_logo_second_failure_composites_onto_anchor_source`
- `test_repair_image_cli_unchanged` (`repair.main` output for a fake run)

**Steps:**
- [ ] **Step 1:** Write the failing tests and run them. Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: automatic check-and-fix chain within the repair limits`.

### Task 4: Review, rules and library API

**Files:**
- Modify `studio/app.py`.
- Create `studio/library.py`.
- Test: `tests/test_review_api.py`.

**Interfaces:**
- **Reviews:**
  - `GET /api/reviews` lists the user's final jobs with: review state, step list (kind, status, code, output image URL path, cost), latest image, checks summary in plain words, caption, shot words and `limits_left`.
  - `GET /api/reviews/{job_id}` returns one item.
- **Verdicts.** All verdicts are atomic per job: a conditional UPDATE on `jobs.review` from `ready` or `needs_person`. A second verdict gets 409 "already decided" (Review Focus 4).
  - `POST /api/reviews/{job_id}/approve {note}`:
    - calls `finish.record_verdict(latest, "approved", note, by=user)`;
    - sets review `approved`;
    - is allowed from `ready`, or from `needs_person` with a note.
  - `POST /api/reviews/{job_id}/reject {note}` (the note is required):
    - calls `record_verdict(latest, "rejected", note, by=user)`, which appends a pending rule;
    - then the brain drafts the rule: `studio.tasks.draft_rule(note, brain)` returns `{prompt_text, applies_to}` with `applies_to` from `all|packshot|on_model|<catalogue id>`, as one positive sentence;
    - the draft is stored on that pending rule, still pending;
    - sets review `rejected`.
  - `POST /api/reviews/{job_id}/change {code}`: `code` must be a key of `repairs.json` `codes` whose `fix` is `repair`. It enqueues a `repair` step on the latest image if limits allow; otherwise 409 with "the repair limit for this image is reached; ask for a new image instead". One running fix at a time: 409 "a fix is already running".
  - `POST /api/reviews/{job_id}/anchor {replace: bool}` (Review Focus 3):
    - **Allowed only when:** the job's recipe is `packshot-front`, decoration is `none`, `layout` is null, and review is `ready` or `needs_person`. Otherwise 409 with a plain reason.
    - **Writes** the latest image as PNG to `refs/anchors/<catalogue>_<colour_id>_<gsm>_front.png`.
    - **Records** a ledger event `{"event": "anchor_approved", "file": latest, "anchor": path, "by": user}` and also `record_verdict(..., "approved")`.
    - **Sets** review `anchor`.
    - **An existing file** needs `replace: true`, otherwise 409 "a reference packshot already exists for this colour".
- **Rules:**
  - `GET /api/rules` returns `rules` with an index, status, note, `prompt_text`, `applies_to` and `from_shot`.
  - `POST /api/rules/{i}/activate {prompt_text, applies_to}`: the person's edited text, at most 40 words, `applies_to` from the allowed set; sets status `active` and `activated_by`.
  - `POST /api/rules/{i}/dismiss`: status `dismissed`.
- **Library:**
  - `GET /api/library?style=&colour=&technique=&placement=&model=&kind=` returns approved images. An image counts as approved if its latest image has an `approved` ledger event. Each entry has its record: shot words, caption, model, cost so far (sum of the chain's step costs plus the final), who approved and when, prompt (from `out/specs` via `compile_spec`), and image URL paths.
  - `POST /api/library/{job_id}/export {pdf: bool}` calls `finish.export(latest, pdf)` and returns the export file URL paths and `alt` + `caption`.
- **Served folders:** extend the files route's folders to include `out/reject` (images) and `out/export` (`.webp`, `.png`, `.jpg`, and `.pdf` served as a download). Everything else is unchanged.

**Tests:**
- `test_review_list_and_item`
- `test_approve_once_then_409` (Review Focus 4)
- `test_reject_creates_pending_rule_with_draft` (fake brain)
- `test_change_respects_limits_and_single_running`
- `test_anchor_refuses_sketch_decorated_or_other_recipe` (Review Focus 3)
- `test_anchor_writes_png_and_needs_replace`
- `test_rules_activate_validates_text_and_scope`
- `test_library_lists_only_approved_with_record`
- `test_export_requires_approval_and_returns_files`
- `test_reject_folder_and_export_served_but_contained`

**Steps:**
- [ ] **Step 1:** Write the failing tests and run them. Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: review, rules and library API`.

### Task 5: Review screen

**Files:**
- Modify `studio/static/index.html`, `app.js` and `styles.css`.

**Behaviour** (mockup screen 2):
- **The Review tab is live.** It shows a list of items with status pills: "Checking…", "Fixing…", "Ready for your review", "Needs a person", "Approved", "Reference packshot", "Rejected".
- **The detail view has:**
  - **Progress line:** Sketches → Final → Checks → Fixes → Your review → Export.
  - **The latest image, large,** with check boxes drawn on it:
    - the logo box from the logo check;
    - stray-text boxes;
    - a colour badge, for example "Colour difference 2.1 (limit 5)".
  - **A before/after toggle** across the chain's images.
  - **A checks list in plain words:** pass ✓, fixed ↻, "Not checked — please look" for `ok: None`, and the AI's notes.
  - **The caption.**
  - **Buttons:**
    - **Approve.**
    - **Approve as reference packshot:** only shown when allowed; Replace is a two-step in-page confirm.
    - **Ask for a change:** a picker of plain names from `repairs.json` repair codes, e.g. "Fix the drawcords", "Fix the hands", "Fix the logo size", plus "Change my choices", which opens the builder with this job's answers.
    - **Reject with a note:** a required textarea.
  - **Spend so far** for this image.
- **Updates:** poll while any item is checking or fixing; stop when idle.
- **Plain language throughout:** no codes on screen, no paths.

**Steps:**
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Browser walkthrough with stubs for the checking, fixing, ready, needs-a-person and approved states. No paid calls in this task.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: review screen`.

### Task 6: Pending rules and Library screens

**Files:**
- Modify `studio/static/index.html`, `app.js` and `styles.css`.

**Behaviour:**
- **Pending rules:** a panel in the Review tab (badge with count) listing each pending rule: the note, the AI's draft sentence (editable, max 40 words), and the scope picker (All images / Packshots / On-model / one style). "Switch on" or "Dismiss". Active rules are listed read-only with who switched them on.
- **The Library tab is live:**
  - **Grid** of approved images with captions.
  - **Filters:** style, colour, technique, placement, model, packshot/on-model, built from the items present.
  - **Record drawer:** shot words, who approved and when, cost, the full prompt behind a disclosure, the caption.
  - **"Export for the website":** WebP sizes listed with sizes, alt text and caption shown with copy buttons using `navigator.clipboard`, with a select-text fallback. "Approval PDF" is offered for client-mockup items.
  - **Reference packshots** show with a "Reference packshot" badge.

**Steps:**
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Browser walkthrough with stubs.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit: `feat: pending rules and library screens`.

### Task 7: Guide page, tour stop and live demo

**Files:**
- Create `studio/guide/review-and-approve.md`.
- Modify `studio/guide.py` (`PAGES`), `studio/static/*` (tour step 6 "Review tab", shown only when the tab is live) and `tests/test_guide.py`.

**Content:**
- "Review and approve", in plain steps:
  - what the automatic checks and fixes do and their limits (`{{limits.*}}` placeholders);
  - what each button does;
  - reference packshots;
  - rejection notes becoming rules;
  - the Library export.
- Remove the "for now, ask the owner" wording from plan 2 now that the Review tab exists (`app.js` `anchorDone`, guide step 5).

**Live demo** (owner's standing go-ahead; expected ≤ $0.60; run `python -m pipeline.ledger` before and after; stop and report if any single run's estimate exceeds $1):
1. Start the studio. In Review, find the clean olive packshot from plan 2: job 5, `out/finals/20261115T114345_…_final.jpg`. If its checks haven't run, run them (vision ≈ $0.01).
2. "Approve as reference packshot" writes `refs/anchors/pullover-oversized_olive_380_front.png`. Confirm the sketch-built render `…111133…` is refused.
3. In Create image, make an on-model olive 380 oversized shot with Maya (standard, ≈ $0.10). Watch the chain run checks and any fixes. Approve it in Review, then "Export for the website".
4. Reject one draft-quality image with a note, if any, to show a pending rule with a drafted sentence. Do not switch it on.
5. Screenshot every step.

**Steps:**
- [ ] **Step 1:** Write the failing guide tests and run them. Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the full suite. Expect all tests to pass.
- [ ] **Step 4:** Commit.
- [ ] **Step 5:** Run the live demo and write the report.

## Out of scope (later plans)

- Client logo upload.
- Client orders tab.
- Ideas from coverage and "make it in more colours".
- Teammate access.
