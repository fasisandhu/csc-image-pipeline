# Spike findings: Claude through the Agent SDK (2026-11-15)

Environment: Windows 11, Claude Code 2.1.325, `claude-agent-sdk==0.2.182`, model `claude-sonnet-5-5`. Raw results are in `results.jsonl`. No credential values are in this folder (checked).

| Check | Result | Seconds | Note |
|---|---|---|---|
| S1 logged-in subscription, default setup | pass | 6.3 | reported usage-equivalent $0.148 (not billed on a subscription) |
| S1b slim setup (`tools=[]`, `setting_sources=[]`, short system prompt) | pass | 3.7 | usage-equivalent $0.0034, about 44 times less |
| S2 `csc-studio` plugin loads | pass | 5.4 | all five skills listed |
| S3 Claude reads a local image (`allowed_tools=["Read"]`) | pass | 7.9 | `{"drawcords": 2, "metal_aglets": true}` on the approved anchor |
| S4a invalid `CLAUDE_CODE_OAUTH_TOKEN` via `options.env` | fails as intended | 6.1 | `401 Invalid bearer token`: the per-call token outranks the logged-in session |
| S4b real token via `options.env` | pass | 5.5 | |
| S5 API key | not tested | | no key in `.env` yet |
| S6 warm `ClaudeSDKClient`, 3 brief readings with `output_format` JSON schema | pass | open 1.5; median 1.8 (1.5–2.9) | `structured_output` filled; "teal" gives `colour: null, colour_word: "teal"` |

## Decisions for the build

1. **Per-user credential:** pass `ClaudeAgentOptions(env={"CLAUDE_CODE_OAUTH_TOKEN": token})` for a subscription token, or `env={"ANTHROPIC_API_KEY": key}` for an API key (the second is untested). An empty token means the logged-in session.
2. **`STUDIO_FAST_TASKS` default = `claude`.** A warm session's median of 1.8 s is well under the 5 s limit.
3. **Structured output:** use `output_format={"type": "json_schema", "schema": ...}` and read `ResultMessage.structured_output`. Parse JSON from the text only as a fallback.
4. **Slim setup for structured tasks** (`tools=[]`, `setting_sources=[]`, a task system prompt): faster and about 44 times less usage. Load the `csc-studio` plugin only for agentic tasks that need its skills, which are in later plans.
5. **Cost accounting:** `ResultMessage.total_cost_usd` is reported on every call. Record it as `usage_equiv_usd` for subscription calls (where `cost_usd` is 0) and as `cost_usd` for API-key calls (where the $5 cap applies).
6. **Keep one warm `ClaudeSDKClient` per user** for quick tasks. Opening one takes about 1.5 s.
