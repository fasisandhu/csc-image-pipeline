# CSC Studio: Spike and Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that per-user Claude works through the Agent SDK, then build the dashboard's server-side foundations, testable without a UI:
- recipes and a spec builder;
- question sets;
- a stray-text scan;
- a brain adapter with Gemini fallback;
- brief reading;
- an app server with a job queue;
- a guide skeleton.

**Architecture:**
- A new `studio/` package (FastAPI server, SQLite job store, one background worker, brain adapter, question sets, guide) sits on top of the existing `pipeline/` package, which keeps enforcing every rule.
- `pipeline/` gains two pieces:
  - `pipeline/specs.py`: answers to a spec dict, using `brand/recipes.json`;
  - a stray-text scan in `pipeline/qa.py`.
- No UI in this plan: the step-2 demo uses FastAPI's built-in `/docs` page.

**Tech Stack:** Python 3.13 (`.venv`), FastAPI + uvicorn, sqlite3 (stdlib), claude-agent-sdk, google-genai 2.34.0, pytest 8.4.2, httpx (for FastAPI's TestClient).

**Spec:** `docs/superpowers/specs/2026-11-15-studio-dashboard-design.md`. This plan covers build steps 1 (quick test) and 2 (foundations). Steps 3 to 8 get their own plans after the step-2 demo, because the spike's results shape them.

## Global Constraints

- **Facts only from `catalogue/` and `brand/`.** AI returns option ids only and never a value. The one fact a person may type is a custom colour `{"name", "hex": "#RRGGBB"}`.
- **Budget:**
  - Gemini hard cap `ledger.GEMINI_MONTHLY_CAP_USD` = 9.00.
  - Runs estimated above `ledger.RUN_APPROVAL_USD` = 1.00 need explicit confirmation from either user.
  - Cloudflare stops at `ledger.CF_DAILY_NEURON_CAP` = 9500.
- **No retry loops.** The worker never re-runs a failed or interrupted job by itself.
- **Limits** come from `brand/repairs.json` (`repairs_per_render` 2, `regenerations_per_shot` 2).
- **Secrets:**
  - Keys and Claude credentials live only in `.env`.
  - Never print, log, store in SQLite, return from the API, or write them to `out/log.jsonl`.
  - Log the credential *type* only: `"subscription"`, `"api_key"` or `"gemini"`.
- **Server binding:** 127.0.0.1 only. A localhost request is user `"owner"`.
- **Models:**
  - Claude brain default `claude-sonnet-5-5`.
  - Gemini brain model `pipeline.providers.vision.MODEL` (`gemini-3.8-flash`), priced at that module's `USD_PER_M_INPUT` and `USD_PER_M_OUTPUT`.
- **Claude API-key spend:** per-user monthly cap from `STUDIO_<USER>_CLAUDE_CAP_USD`, default 5.00.
- **New dependencies** are pinned to the exact version installed: runtime in `requirements.txt`, tests in `requirements-dev.txt`.
- **Nothing existing breaks:** the existing CLIs, the csc-studio plugin and the 20 existing tests keep working. Run `.venv/Scripts/python -m pytest -q` at the end of every task.

## Review Focus

1. **A brief names a colour outside the palette ("teal"):** colour stays unanswered and `colour_hint` is `"teal"`. No hex is invented. Test in Task 5.
2. **A placement with a TODO max (sleeve), or a size above the max:** the option is disabled with a reason, and the compiled spec raises `CompileError`. Tests in Tasks 1 and 2.
3. **A job estimated over $1 without confirmation, or one the router answers `ask`/`queue`:** HTTP 409 with the reason, and no job row is created. Test in Task 6.
4. **Claude is unavailable** (auth error, usage limit, timeout, API-key cap): the answer comes from Gemini with `fallback: true`, and the credential value appears in no record. Test in Task 4.
5. **The server restarts while a job is `running`:** the job becomes `interrupted` and is never re-run automatically. Test in Task 6.

## File map

| Path | Responsibility |
|---|---|
| `spikes/claude_sdk/spike.py`, `FINDINGS.md` | Task 0, throwaway |
| `brand/recipes.json` | shot types and scenes (data) |
| `pipeline/specs.py` | answers → spec dict, anchor lookup, shot id |
| `pipeline/providers/vision.py` | + `read_all_text` |
| `pipeline/qa.py` | + `allowed_texts`, `stray_text_check`, CLI line |
| `studio/questions/images.json`, `studio/questions.py` | question set and resolver |
| `studio/brain/base.py`, `gemini.py`, `claude.py`, `__init__.py` | brain adapter |
| `studio/tasks.py` | `read_brief` |
| `studio/db.py`, `studio/worker.py`, `studio/identity.py`, `studio/app.py`, `studio/__main__.py` | server, store, worker |
| `studio/guide/*.md`, `studio/guide.py` | guide skeleton |
| `tests/test_specs.py`, `test_questions.py`, `test_stray_text.py`, `test_brain.py`, `test_tasks.py`, `test_app.py`, `test_guide.py` | tests |

**Shared `answers` dict** (Tasks 1, 2, 5, 6). Every key is optional until submitted:

```python
{"purpose": "website" | "client",
 "catalogue": "pullover-oversized", "colour": "olive" | {"name": "Teal Blue", "hex": "#1F6F78"}, "gsm": 380,
 "recipe": "on-model-back", "cast": "Maya", "scene": "concrete-daylight",
 "decoration": "none" | "design", "design": "static-bloom", "technique": "screen print",
 "placement": "full back", "size_cm": 30}
```

---

### Task 0: Spike, Claude through the Agent SDK (throwaway, no TDD)

**Files:** Create `spikes/claude_sdk/spike.py` and `spikes/claude_sdk/FINDINGS.md`.

**Produces:**
- the pinned `claude-agent-sdk` version;
- the confirmed way to pass a per-call credential (expected: `ClaudeAgentOptions(env={...})`);
- warm and cold latency;
- the `STUDIO_FAST_TASKS` default.

- [ ] **Step 1:** Install with `.venv/Scripts/python -m pip install claude-agent-sdk`. Record the version from `pip show claude-agent-sdk`.
- [ ] **Step 2: S1, subscription with no keys in env.** `query("Reply with the word ok", ClaudeAgentOptions(model="claude-sonnet-5-5"))`. Expect `ok`. Record cold seconds.
- [ ] **Step 3: S2, plugin.** Add `plugins=[{"type": "local", "path": "plugins/csc-studio"}]` and ask "List the csc-studio skills you can use". Expect all five: plan-shots, generate-shots, qa-and-repair, verify-logos, finish-and-export.
- [ ] **Step 4: S3, image.** Use `allowed_tools=["Read"]` with `cwd` set to the repo root. Ask it to read `out/finals/20261101T181322_ovs-wblack-450-anchor-front_repair1_cc.jpg` and answer only JSON `{"drawcords": int, "metal_aglets": bool}`. Expect `2, true`.
- [ ] **Step 5: S4, per-call credential.**
  - The owner runs `! claude setup-token` and adds the result to `.env` as `STUDIO_OWNER_CLAUDE_OAUTH_TOKEN`. Never echo it.
  - Run once with `env={"CLAUDE_CODE_OAUTH_TOKEN": "invalid"}`. Expect an auth error, which proves the per-call token outranks the logged-in session.
  - Run again with the real token. Expect success.
- [ ] **Step 6: S5, API key.** If `STUDIO_OWNER_ANTHROPIC_API_KEY` is set, run with `env={"ANTHROPIC_API_KEY": ...}` and expect success. Otherwise write "not tested".
- [ ] **Step 7: S6, warm session.**
  - Open one `ClaudeSDKClient`.
  - Send 3 brief-reading prompts, each answering JSON `{"colour": "<palette id or null>"}`.
  - Record the seconds for each and the median.
- [ ] **Step 8:** Write `FINDINGS.md` with a table of S1 to S6 (result, seconds) and the SDK version. Apply these decisions:
  - **S4 failed:** stop, and bring the owner the options from design call C15. Task 4's `ClaudeBrain` then supports only `STUDIO_<USER>_CLAUDE=subscription` with no token (the logged-in session) until the owner decides.
  - **S6 median above 5 s:** set `STUDIO_FAST_TASKS=gemini` as the default; otherwise `claude`.
- [ ] **Step 9:** Commit the spike folder only (no secrets), then **stop and report to the owner** (as an Artifact page) before Task 1.

```bash
git add spikes/claude_sdk && git commit -m "spike: Claude Agent SDK per-user credentials, plugin, image, latency"
```

### Task 1: Recipes and spec builder

**Files:**
- Create `brand/recipes.json` and `pipeline/specs.py`.
- Test: `tests/test_specs.py`.

**Interfaces (produces):**
- `load_recipes() -> dict`, returning `{"recipes": {...}, "scenes": {...}}`.
- `colour_id(colour: str | dict) -> str`: a palette id, or the slug of a custom name (`"Teal Blue"` → `"teal-blue"`).
- `find_anchor(catalogue: str, colour: str | dict, gsm: int) -> str | None`: returns `f"refs/anchors/{catalogue}_{colour_id}_{gsm}_front.png"` if it exists.
- `shot_id_for(answers: dict) -> str`: `f"{catalogue}-{colour_id}-{gsm}-{recipe}"`, plus `f"-{design}"` when decorated.
- `build_spec(answers: dict, creative: dict | None = None) -> dict`: a spec in CLAUDE.md's shape. `creative` may override `pose`, and `background` on on-model shots only.

**`recipes.json`:**
- Shape per recipe: `label`, `model` (bool), `view` ("front" or "back"), `tier`, `aspect` "4:5", `resolution` "2K", `purpose`, `camera` {angle, lens, framing}, `lighting`, `background`, `pose` (on-model only), `must_show`, `avoid`.
- Shape per scene: `label`, `background`, `lighting`.
- `packshot-front` copies its text verbatim from `specs/ovs-wblack-450-anchor-front.json`. `on-model-front` copies its text verbatim from `specs/ovs-wblack-450-maya-front.json`.
- Also author `packshot-back`, `on-model-back`, `decoration-closeup` and `flat-lay` in the same shape.
- Scenes:
  - `studio-grey`: the anchor spec's background and lighting;
  - `warm-studio`: the maya spec's background and lighting;
  - `concrete-daylight`;
  - `warm-cafe`.
- On-model recipes take their background and lighting from the chosen scene. Packshots ignore the scene.

**Rules:**
- **Tier:** the recipe's tier, raised to `"premium"` when `decoration == "design"`.
- **Decoration entry:** `{"type": technique, "artwork": design["file"], "placement", "size_cm", "render_mode": "generate_and_verify"}`.
- **`set.reference_images`:** the anchor, if found, with role `compile.REF_ROLES["anchor"]`.
- **`set.style_block`:** `brand.json` `style_block`.

- [ ] **Step 1: Write failing tests:**

```python
def test_packshot_front_matches_existing_anchor_spec():
    spec = build_spec({"catalogue": "pullover-oversized", "colour": "washed-black", "gsm": 450,
                       "recipe": "packshot-front", "decoration": "none"})
    ref = json.loads(Path("specs/ovs-wblack-450-anchor-front.json").read_text())["shot"]
    for k in ("purpose", "camera", "lighting", "background", "must_show"):
        assert spec["shot"][k] == ref[k]
    assert compile_spec(spec).tier == "standard"

def test_design_raises_tier_and_fills_decoration():
    spec = build_spec({..., "recipe": "packshot-front", "decoration": "design", "design": "oakmere-athletic",
                       "technique": "3D puff embroidery", "placement": "centre chest", "size_cm": 22})
    assert spec["shot"]["tier"] == "premium"
    assert spec["shot"]["decoration"][0] == {"type": "3D puff embroidery", "artwork": "brand/designs/oakmere-athletic.svg",
        "placement": "centre chest", "size_cm": 22, "render_mode": "generate_and_verify"}

def test_anchor_attached_only_when_approved_file_exists():
    # washed-black 450 oversized -> refs/anchors/pullover-oversized_washed-black_450_front.png ; olive -> []

def test_size_over_placement_max_fails_compile():   # Review Focus 2
    with pytest.raises(CompileError, match="exceeds max 30"):
        compile_spec(build_spec({... "placement": "full back", "size_cm": 34}))

def test_shot_id_stable_and_custom_colour_slugged():
    # same answers -> same id "pullover-oversized-washed-black-450-packshot-front"
    # {"name": "Teal Blue", "hex": "#1F6F78"} -> "pullover-oversized-teal-blue-450-packshot-front"

def test_on_model_takes_scene_and_recipe_pose():
    # recipe on-model-front, cast Maya, scene warm-studio -> background == maya spec background,
    # model == {"present": True, "cast": "Maya", "pose": <maya spec pose>}
```

- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_specs.py -v`. Expect FAIL (`ModuleNotFoundError: pipeline.specs`).
- [ ] **Step 3:** Write `brand/recipes.json` and implement the five functions in `pipeline/specs.py`, reusing `compile.load_json` and `compile.resolve_colour`.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Commit:** `git add brand/recipes.json pipeline/specs.py tests/test_specs.py && git commit -m "feat: shot recipes and answers-to-spec builder"`

### Task 2: Question set and resolver

**Files:**
- Create `studio/__init__.py` (empty), `studio/questions/images.json` and `studio/questions.py`.
- Test: `tests/test_questions.py`.

**Interfaces:**
- Consumes `pipeline.specs.load_recipes`, `pipeline.colour.srgb_to_lab` and `hex_to_rgb`, and `pipeline.compile.resolve_colour`.
- Produces:
  - `@dataclass Option(id: str, label: str, sub: str = "", disabled: bool = False, reason: str = "")`.
  - `@dataclass Section(id: str, title: str, hint: str, kind: str, options: list[Option], value: object, visible: bool, required: bool, max: float | None = None)`, where `kind` is one of `"choice" | "colour" | "size"`.
  - `resolve(answers: dict, name: str = "images") -> list[Section]`.
  - `missing(answers: dict, name: str = "images") -> list[str]`.
  - `valid_value(section_id: str, value, answers: dict, name: str = "images") -> bool`.

**`images.json`:**
- An ordered list of sections, each `{id, title, hint, kind, source, required, show_if?}`.
- Sections in order: `purpose` (static: website, client), `catalogue`, `colour`, `gsm`, `recipe`, `cast`, `scene`, `decoration` (static: none, design), `design`, `technique`, `placement`, `size_cm`.
- `show_if`:
  - `cast` and `scene`: `{"recipe.model": true}`;
  - `design`, `technique`, `placement` and `size_cm`: `{"decoration": "design"}`.

**Rules:**
- `gsm` options come from the chosen catalogue's `gsm_options`. With no catalogue chosen, every option is disabled with reason `"choose a style first"`.
- `placement` options come from the catalogue's `decoration_placements`. A `max_cm` that is missing or TODO is disabled with reason `"max size not set yet"`.
- `size_cm.max` is the chosen placement's `max_cm`.
- `technique` options are the catalogue's `decoration_types`.
- Design tone check:
  - garment tone is `"dark"` if the colour's L* < 50, else `"light"`;
  - a design whose `for_garments` differs is disabled with reason `f"made for {for_garments} garments"`.
- `colour` lists palette ids; a custom `{"name", "hex"}` is valid when the hex matches `^#[0-9A-Fa-f]{6}$` and the name is non-empty.
- Option ids are strings. `valid_value` compares `str(value)` with the enabled option ids, so `450` and `"450"` both pass. `size_cm` is valid when it is a number in the range `0 < size <= max`.

- [ ] **Step 1: Write failing tests:**
  - `test_model_sections_follow_recipe`: `cast` and `scene` are hidden for `packshot-front` and visible for `on-model-front`.
  - `test_gsm_follows_catalogue`: for `zip`, the gsm option ids equal `["320", "380", "450"]` (ids are strings).
  - `test_sleeve_disabled_with_reason` (Review Focus 2): `sleeve` has `disabled` True and reason `"max size not set yet"`.
  - `test_size_max_follows_placement`: with placement `"full back"`, `size_cm.max == 30`.
  - `test_design_tone_mismatch_disabled`: with colour `"cream"`, `oakmere-athletic` is disabled with `"made for dark garments"`, and `oakmere-athletic-light` is enabled.
  - `test_missing_lists_required_unanswered`: `missing({"catalogue": "pullover-oversized"})` starts with `["purpose", "colour", "gsm", "recipe", "decoration"]`.
  - `test_valid_value_rejects_unknown`: `valid_value("cast", "Zara", {...}) is False`; `valid_value("colour", "#12345", {})` is False.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_questions.py -v`. Expect FAIL (no module).
- [ ] **Step 3:** Implement `studio/questions.py` and write `images.json`. Titles and hints follow the design mockup wording ("Fits we make", "Samples only; customers can choose any colour", "Changes how the hoodie hangs in the picture").
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Commit:** `git add studio tests/test_questions.py && git commit -m "feat: image question set with file-sourced options and limits"`

### Task 3: Stray-text scan

**Files:**
- Modify `pipeline/providers/vision.py` (add a method) and `pipeline/qa.py` (add functions and a CLI line).
- Test: `tests/test_stray_text.py`.

**Interfaces:**
- `GeminiVision.read_all_text(image: Path) -> VisionResult`, where `data` is `list[{"text": str, "box": [x0, y0, x1, y1]}]` in pixels, reusing `_ask` and `_to_px`.
- `qa.allowed_texts(spec: dict) -> list[str]`: the `text` of each decoration's design (`designs.design_for`), plus `labels.neck` if set.
- `qa.stray_text_check(image: str | Path, allowed: list[str]) -> dict`, returning `{"result": "pass"|"fail", "code": None|"unintended_text", "found": [str], "stray": [str]}`.
  - The vision call goes through `ledger.logged_call` with `{"event": "vision", "purpose": "stray_text", ...}`.
  - A found text is allowed when `logo.normalise_text(t)` is empty, or is a substring of any normalised allowed text.

- [ ] **Step 1: Write failing tests** (monkeypatch `GeminiVision.__init__` and `read_all_text`, and set `ledger.entries` and `ledger.record` to in-memory lists):
  - `test_design_text_parts_allowed`: found `["OAKMERE", "ATHLETIC"]`, allowed `["OAKMERE ATHLETIC"]`. Expect `result == "pass"`.
  - `test_invented_label_fails`: found `["AVENLIE CO."]`, allowed `[]`. Expect `code == "unintended_text"` and `stray == ["AVENLIE CO."]`.
  - `test_blank_reads_ignored`: found `["", "  ", "--"]`. Expect pass.
  - `test_allowed_texts_from_spec`: the Oakmere spec gives `["OAKMERE ATHLETIC"]`; the anchor spec gives `[]`.
  - `test_scan_is_logged`: one record with `purpose == "stray_text"`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_stray_text.py -v`. Expect FAIL (`AttributeError`).
- [ ] **Step 3:** Implement. The `read_all_text` prompt asks for every piece of visible text anywhere in the image (garment, labels, background), with `box_2d` and the exact characters, returning `[]` if there is none. In `qa.main`, after the colour line, print `stray text: pass|FAIL <stray list>`. It is informational, like the colour line, and records nothing automatically.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Commit:** `git add pipeline/providers/vision.py pipeline/qa.py tests/test_stray_text.py && git commit -m "feat: stray-text scan flags text not in the shot's artwork"`

### Task 4: Brain adapter

**Files:**
- Create `studio/brain/base.py`, `gemini.py`, `claude.py` and `__init__.py`.
- Modify `pipeline/ledger.py` (add `claude_month_spend`), `.env.example` (names only) and `requirements.txt` (add `claude-agent-sdk==0.2.182`).
- Read `spikes/claude_sdk/FINDINGS.md` first: it is the confirmed SDK usage.
- Test: `tests/test_brain.py`.

**Interfaces:**
- Consumes the Task 0 findings: the credential env mechanism and `STUDIO_FAST_TASKS`.
- Produces:

```python
@dataclass
class BrainResult:
    data: dict; provider: str; model: str; credential: str; seconds: float; cost_usd: float; fallback: bool = False

class BrainError(RuntimeError): ...

class Brain(Protocol):
    name: str; model: str; credential: str
    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (), user: str = "owner") -> BrainResult: ...

class GeminiBrain: ...                      # credential "gemini"
class ClaudeBrain:
    def __init__(self, user: str, credential: str, token_env: str | None, model: str = "claude-sonnet-5-5"): ...
    def _run(self, prompt: str, env: dict, images: Sequence[Path], schema: dict) -> tuple[dict | None, str, float]: ...
        # the only SDK call; tests patch it. Returns (structured_output, result text, total_cost_usd)
class FallbackBrain:
    def __init__(self, primary: Brain, fallback: Brain): ...
def credential_for(user: str) -> tuple[str, str | None]   # ("subscription"|"api_key"|"none", env var name or None)
def get_brain(user: str, fast: bool = False) -> Brain     # fast=True honours STUDIO_FAST_TASKS
```

- `ledger.claude_month_spend(user: str) -> float`.

**Env names** (`.env.example`, values blank):
- `STUDIO_OWNER_CLAUDE` = `subscription | api_key | none`;
- `STUDIO_OWNER_CLAUDE_OAUTH_TOKEN` (blank means the logged-in Claude Code session);
- `STUDIO_OWNER_ANTHROPIC_API_KEY`;
- `STUDIO_OWNER_CLAUDE_CAP_USD` (default 5);
- `STUDIO_FAST_TASKS` = `claude | gemini`.

**Rules:**
- Every `ask_json` records `{"event": "brain", "user", "task", "provider", "model", "credential", "tier": "brain", "seconds", "cost_usd", "status", "fallback"}`.
- Gemini records use `provider: "gemini"`, so they count toward the $9 cap. Call `ledger.check("gemini", {"cost_usd": 0.002})` first.
- `ClaudeBrain` (confirmed by the spike):
  - `env` is `{"CLAUDE_CODE_OAUTH_TOKEN": token}` for a subscription token, `{"ANTHROPIC_API_KEY": key}` for an API key, or `{}` for the logged-in session (subscription with a blank token).
  - `_run` uses `claude_agent_sdk.query` with `ClaudeAgentOptions(model, cwd=ROOT, env=env, setting_sources=[], system_prompt=<one line naming the task>, output_format={"type": "json_schema", "schema": schema})`. It also sets:
    - text-only calls: `tools=[]` and `max_turns=2`;
    - calls with images: `tools=["Read"]`, `allowed_tools=["Read"]`, and `max_turns=4`, with the image paths named in the prompt.
  - No plugin is loaded for these structured tasks.
  - Data is `structured_output` when present; otherwise the first JSON object parsed from the result text, with code fences stripped.
  - Cost recording:
    - subscription calls record `cost_usd: 0` and `usage_equiv_usd: total_cost_usd`;
    - API-key calls record `cost_usd: total_cost_usd`, which counts toward `claude_month_spend`.
  - Raises `BrainError` on:
    - SDK errors;
    - a 60 s timeout;
    - no usable JSON;
    - for API-key users, `claude_month_spend(user) >= cap`.
  - One `query()` per call. A warm client is not needed, because the slim setup answered in 3.7 s cold.
- `get_brain` returns `FallbackBrain(ClaudeBrain, GeminiBrain)` unless the user's credential is `none`, in which case it returns `GeminiBrain`.

- [ ] **Step 1: Write failing tests:**
  - `test_credential_for_reads_env`: monkeypatch the env and check each of the three types.
  - `test_falls_back_to_gemini_when_claude_fails` (Review Focus 4):
    - `ClaudeBrain._run` raises an SDK error and the token env is `"sk-test-SECRET"`;
    - the result has `provider == "gemini"` and `fallback is True`;
    - two brain records exist (error, ok);
    - `"sk-test-SECRET"` appears in no record's JSON.
  - `test_api_key_cap_blocks_before_call`: with a recorded Claude spend of 5.0 for the owner, `ClaudeBrain` raises `BrainError` and `_run` is never called.
  - `test_gemini_brain_records_cost`: with a fake genai client whose `usage_metadata` is 1000 input / 200 output tokens, `cost_usd == round(1000*0.75/1e6 + 200*3.75/1e6, 6)`, and the record has `tier == "brain"`.
  - `test_claude_prefers_structured_output`: `_run` returns `({"a": 1}, "ignored", 0.003)`. Expect `data == {"a": 1}`; a subscription record has `cost_usd == 0` and `usage_equiv_usd == 0.003`; an API-key record has `cost_usd == 0.003`.
  - `test_claude_json_parsing_fallback`: `_run` returns `(None, '```json\n{"a": 1}\n```', 0.0)`. Expect `{"a": 1}`. With `(None, "no json here", 0.0)`, expect `BrainError`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_brain.py -v`. Expect FAIL (no module).
- [ ] **Step 3:** Implement as specified above, following `spikes/claude_sdk/FINDINGS.md`.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Commit:** `git add studio/brain pipeline/ledger.py .env.example requirements.txt tests/test_brain.py && git commit -m "feat: brain adapter, Claude per-user credential with Gemini fallback"`

### Task 5: Reading a brief

**Files:**
- Create `studio/tasks.py`.
- Test: `tests/test_tasks.py`.

**Interfaces:**
- Consumes `questions.resolve`, `questions.valid_value` and `Brain.ask_json`.
- Produces `read_brief(text: str, brain: Brain, answers: dict | None = None, user: str = "owner") -> dict`, returning `{"answers": dict, "unknown": list[str], "colour_hint": str | None, "provider": str, "fallback": bool}`.

**Rules:**
- The prompt lists each visible section's enabled option ids from `resolve(answers)` and asks for JSON with only what the text states or clearly implies, plus `colour_word` (the colour word used, or null).
- The schema is an object with one string-or-null property per section id, plus `colour_word`.
- Keep a returned value only if `valid_value` accepts it **and** that section is not already answered: existing answers win.
- If `colour` is invalid or null and `colour_word` is set, report it as `colour_hint`.
- `unknown` = `questions.missing(merged answers)`.

- [ ] **Step 1: Write failing tests** with a fake brain returning fixed data:
  - `test_valid_answers_kept`: `{"colour": "olive", "decoration": "design", "placement": "full back"}` comes back in `answers`.
  - `test_invented_values_dropped`: `{"colour": "#123456", "gsm": "500", "cast": "Zara"}` gives `answers == {}`.
  - `test_unknown_colour_becomes_hint` (Review Focus 1): `{"colour": None, "colour_word": "teal"}` gives no `"colour"` key, `colour_hint == "teal"`, and `"colour"` in `unknown`.
  - `test_existing_answers_win`: `answers={"gsm": 450}` with the brain returning `"gsm": "380"` keeps `450`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_tasks.py -v`. Expect FAIL.
- [ ] **Step 3:** Implement `read_brief`. Convert `gsm` and `size_cm` option ids back to `int`.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Commit:** `git add studio/tasks.py tests/test_tasks.py && git commit -m "feat: read a free-text brief into valid choices only"`

### Task 6: Job store, worker and app server

**Files:**
- Create `studio/db.py`, `studio/worker.py`, `studio/identity.py`, `studio/app.py` and `studio/__main__.py`.
- Modify `requirements.txt` (fastapi, uvicorn), `requirements-dev.txt` (httpx) and `CLAUDE.md` (repo structure: `studio/`, `docs/`; requirements line).
- Test: `tests/test_app.py`.

**Interfaces:**
- **db:**
  - `connect(path: Path | None = None) -> sqlite3.Connection` (default `ROOT/"out"/"studio.db"`; the `jobs` table is created if missing);
  - `create_job(conn, user, kind, answers, spec, estimate, confirmed_by=None) -> int`;
  - `get_job(conn, job_id) -> dict | None`;
  - `next_queued(conn) -> dict | None`;
  - `set_status(conn, job_id, status, **fields)`;
  - `mark_interrupted(conn) -> int`.
- **`jobs` columns:** `id` (INTEGER PRIMARY KEY), `user`, `kind` (`final` | `drafts`), `status` (`queued`, `running`, `done`, `failed`, `interrupted`), `answers`, `spec`, `estimate`, `result` (JSON text), `error`, `confirmed_by`, `created`, `updated`.
- **estimate:** `studio.app.estimate(spec: dict) -> dict`, returning `{"base_usd", "worst_usd", "action", "reason", "tier", "model"}` from `router.route(compile_spec(spec))`.
  - `worst_usd = round(base * (1 + R) + base * P * (1 + R), 2)`, with `R` = `regenerations_per_shot` and `P` = `repairs_per_render`;
  - the Oakmere premium shot gives `base 0.134`, `worst 1.21`.
- **worker:** `Worker(connect_fn, runner=generate.run_route)` with `run_once() -> bool` and `start() -> threading.Thread`.
  - A job runs `compile_spec`, then:
    - kind `final`: one `router.route(c)` and one `runner` call, storing `{"files": [file], "cost_usd"}`;
    - kind `drafts`: three `router.route(c, "draft", pending)` and three `runner` calls, storing `{"files": [...], "cost_usd": 0}`.
  - Then it sets `done`.
  - Any exception sets `failed` with `error=str(e)`. It never re-queues.
- **identity:** `current_user(request) -> str` returns `"owner"` for client host in `{"127.0.0.1", "::1", "testclient"}`, otherwise raises `HTTPException(403)`.
- **app** (FastAPI):

| Endpoint | Request | Response |
|---|---|---|
| `GET /api/health` | | `{"ok": true, "budget": ledger.status()}` |
| `POST /api/questions` | `{"answers"}` | `{"sections": [...], "missing": [...]}` |
| `POST /api/brief` | `{"text", "answers"}` | `read_brief(..., get_brain(user, fast=True), user=user)` |
| `POST /api/preview` | `{"answers"}` | `{"shot_id", "prompt", "warnings", "base_usd", "worst_usd", "action", "reason", "needs_anchor"}`; `CompileError` → 422 `{"error"}` |
| `POST /api/jobs` | `{"answers", "kind": "final"\|"drafts", "confirm": false}` | 201 `{"id"}`; 409 `{"error"}` when the route action is not `run` (router reason), or when `base_usd > ledger.RUN_APPROVAL_USD` and not `confirm`; `confirmed_by = user` when confirmed |
| `GET /api/jobs/{id}` | | the job dict (404 if missing) |

- `needs_anchor` is True when `find_anchor` returns None and the recipe is not `packshot-front`. It is only reported; it gets enforced in plan 2.
- **`__main__`:** `python -m studio` runs `load_dotenv(ROOT/".env")`, then `mark_interrupted`, starts the worker, and runs `uvicorn.run(app, host="127.0.0.1", port=8765)`.

- [ ] **Step 1:** Install `fastapi`, `uvicorn` and `httpx`. Pin the installed versions into `requirements.txt` and `requirements-dev.txt`.
- [ ] **Step 2: Write failing tests** (TestClient; `ledger.entries` returns an in-memory list; `generate.run_route` monkeypatched to a fake that writes nothing and returns `(Path("out/finals/fake.jpg"), SimpleNamespace(cost_usd=0.134))`; `db.connect` uses `tmp_path`):
  - `test_questions_endpoint`: 200, and `"missing"` contains `"purpose"`.
  - `test_preview_two_costs`: Oakmere answers give `base_usd == 0.134`, `worst_usd == 1.21` and `action == "run"`.
  - `test_preview_oversize_is_422` (Review Focus 2): `size_cm` 34 on full back.
  - `test_job_over_one_dollar_needs_confirm` (Review Focus 3): with `estimate` patched to `base_usd 1.5`: 409 without `confirm`; with `confirm` 201 and `confirmed_by == "owner"`.
  - `test_job_refused_when_route_asks` (Review Focus 3): Gemini spend of 8.95 this month gives 409 with the router's reason, and `jobs` has 0 rows.
  - `test_worker_runs_once_and_never_requeues`:
    - a done `final` job has `result.files == ["out/finals/fake.jpg"]`; a done `drafts` job has 3 files;
    - with the fake raising, the job is `failed` and a second `run_once()` returns False.
  - `test_restart_marks_running_interrupted` (Review Focus 5): a row set to `running` becomes `interrupted` after `mark_interrupted`, and `next_queued` returns None.
  - `test_remote_host_forbidden`: `current_user` with host `"10.0.0.5"` raises 403.
  - `test_no_secrets_in_job`: with env `STUDIO_OWNER_CLAUDE_OAUTH_TOKEN=tok-SECRET`, `GET /api/jobs/1` does not contain `tok-SECRET`.
- [ ] **Step 3:** Run `.venv/Scripts/python -m pytest tests/test_app.py -v`. Expect FAIL.
- [ ] **Step 4:** Implement the five modules. Update `CLAUDE.md`'s repo structure (`studio/`, `docs/superpowers/`) and requirements line.
- [ ] **Step 5:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 6: Commit:** `git add studio requirements*.txt CLAUDE.md tests/test_app.py && git commit -m "feat: studio server with question, brief, preview and job endpoints"`

### Task 7: Guide skeleton and step-2 demo

**Files:**
- Create `studio/guide.py`, `studio/guide/start-here.md`, `studio/guide/read-the-checks.md` and `studio/guide/rules.md`.
- Modify `studio/app.py` (add `GET /api/guide/{page}`).
- Test: `tests/test_guide.py`.

**Interfaces:** `render(page: str) -> str`. It replaces `{{key}}` placeholders and raises `KeyError` on an unknown one. The keys:

| Key | Source |
|---|---|
| `colour.tolerance` | 5 |
| `colour.drift_max` | 15 |
| `logo.size_low_pct` | 70 |
| `logo.size_high_pct` | 125 |
| `limits.repairs_per_render` | 2 |
| `limits.regenerations_per_shot` | 2 |
| `budget.cap_usd` | 9.00 |
| `budget.ask_over_usd` | 1.00 |

All are read from `brand/qa_checklist.json`, `brand/repairs.json` and the `ledger` constants.

**Content:**
- **read-the-checks** uses the wording from the design page's guide mockup ("Difference under {{colour.tolerance}} matches; …").
- **start-here** is about the comparison and what the pipeline adds.
- **rules** covers the rules that never change.

- [ ] **Step 1: Write failing tests:**
  - `test_checks_page_uses_live_thresholds`: the rendered page contains `"under 5"`, `"15"`, `"70%"`, `"125%"` and `"2 repairs"`.
  - `test_unknown_placeholder_raises`.
  - `test_guide_endpoint`: `GET /api/guide/read-the-checks` returns 200 with Markdown text.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_guide.py -v`. Expect FAIL.
- [ ] **Step 3:** Implement `render`, the three pages and the route.
- [ ] **Step 4:** Run `.venv/Scripts/python -m pytest -q`. Expect all tests to pass.
- [ ] **Step 5: Live demo** (real calls; expected cost under $0.01 in Gemini, $0 on the subscription and $0 on Cloudflare):
  - `python -m studio`, then open `http://127.0.0.1:8765/docs`.
  - `POST /api/brief` with `"olive hoodie with something on the back"`. Expect `colour: olive` and `decoration: design`.
  - `POST /api/preview` with full answers. Expect a prompt plus the two costs.
  - `POST /api/jobs` with `kind: "drafts"`. Expect 3 free drafts in `out/drafts/`.
  - Run `python -m pipeline.ledger` and record the spend.
- [ ] **Step 6: Commit:** `git add studio tests/test_guide.py && git commit -m "feat: guide skeleton with live thresholds"`. Then report to the owner as an Artifact page (what was built, how to run it, cost) and wait for go-ahead before plan 2 (the image builder screen).
