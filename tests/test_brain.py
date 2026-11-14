import asyncio
import json
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ClaudeSDKError, ResultError, ResultMessage

from pipeline import ledger
from pipeline.ledger import ROOT
from studio import brain
from studio.brain import BrainError, ClaudeBrain, FallbackBrain, GeminiBrain
from studio.brain import claude as claude_mod
from studio.brain import gemini as gemini_mod

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
SECRET = "sk-test-SECRET"
COMPETING = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
ROUTING = ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
ENV_NAMES = ("STUDIO_OWNER_CLAUDE", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", "STUDIO_OWNER_ANTHROPIC_API_KEY",
             "STUDIO_OWNER_CLAUDE_CAP_USD", "STUDIO_FAST_TASKS", "STUDIO_PARTNER_CLAUDE",
             "STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN") + COMPETING + ROUTING


def cred_env(**set_):
    """The full per-call credential env: every competing name present, blank unless given."""
    return {name: set_.get(name, "") for name in COMPETING}


class FakeModels:
    def __init__(self, text='{"a": 1}', inp=1000, out=200, error=None):
        self.text, self.inp, self.out, self.error = text, inp, out, error
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        usage = SimpleNamespace(prompt_token_count=self.inp, candidates_token_count=self.out, thoughts_token_count=None)
        return SimpleNamespace(text=self.text, usage_metadata=usage)


def fake_client(**kw):
    return SimpleNamespace(models=FakeModels(**kw))


@pytest.fixture
def rows(monkeypatch):
    log: list[dict] = []
    monkeypatch.setattr(ledger, "entries", lambda: log)
    monkeypatch.setattr(ledger, "record", lambda e: log.append({"ts": ledger.now().isoformat(), **e}))
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    return log


def brain_rows(log):
    return [r for r in log if r.get("event") == "brain"]


def patch_run(monkeypatch, ret=None, exc=None):
    calls: list[dict] = []

    def run(self, prompt, env, images, schema):
        calls.append({"prompt": prompt, "env": env, "images": list(images), "schema": schema})
        if exc is not None:
            raise exc(env) if callable(exc) else exc
        return ret

    monkeypatch.setattr(ClaudeBrain, "_run", run)
    return calls


def test_credential_for_reads_env(rows, monkeypatch):
    assert brain.credential_for("owner") == ("none", None)  # unset: no Claude
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    assert brain.credential_for("owner") == ("subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "api_key")
    assert brain.credential_for("owner") == ("api_key", "STUDIO_OWNER_ANTHROPIC_API_KEY")
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "none")
    assert brain.credential_for("owner") == ("none", None)


def test_credential_for_bad_value_does_not_echo_it(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", SECRET)  # a token pasted into the wrong variable
    with pytest.raises(ValueError) as e:
        brain.credential_for("owner")
    assert SECRET not in str(e.value)


def test_get_brain_routes_by_credential_and_fast_tasks(rows, monkeypatch):
    assert isinstance(brain.get_brain("owner"), GeminiBrain)
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    b = brain.get_brain("owner")
    assert isinstance(b, FallbackBrain)
    assert isinstance(b.primary, ClaudeBrain) and isinstance(b.fallback, GeminiBrain)
    assert isinstance(brain.get_brain("owner", fast=True), FallbackBrain)  # default STUDIO_FAST_TASKS=claude
    monkeypatch.setenv("STUDIO_FAST_TASKS", "gemini")
    assert isinstance(brain.get_brain("owner", fast=True), GeminiBrain)
    assert isinstance(brain.get_brain("owner"), FallbackBrain)


def test_falls_back_to_gemini_when_claude_fails(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", SECRET)
    monkeypatch.setattr(gemini_mod.genai, "Client", lambda api_key: fake_client())
    calls = patch_run(monkeypatch, exc=lambda env: ClaudeSDKError(f"CLI failed, options env={env}"))

    res = brain.get_brain("owner").ask_json("read_brief", "olive hoodie", SCHEMA, user="owner")

    assert calls[0]["env"] == cred_env(CLAUDE_CODE_OAUTH_TOKEN=SECRET)
    assert res.provider == "gemini" and res.fallback is True and res.data == {"a": 1}
    recs = brain_rows(rows)
    assert [r["status"] for r in recs] == ["error", "ok"]
    assert [(r["provider"], r["credential"], r["fallback"]) for r in recs] == [
        ("claude", "subscription", False), ("gemini", "gemini", True)]
    assert all(SECRET not in json.dumps(r) for r in rows)
    assert "CLI failed" in recs[0]["error"]


def test_both_brains_failing_raises_brain_error(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    patch_run(monkeypatch, exc=ClaudeSDKError("boom"))
    b = FallbackBrain(ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN"),
                      GeminiBrain(client=fake_client(text="not json")))
    with pytest.raises(BrainError, match="claude.*gemini"):
        b.ask_json("read_brief", "x", SCHEMA)
    assert [r["status"] for r in brain_rows(rows)] == ["error", "error"]


def test_api_key_cap_blocks_before_call(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_ANTHROPIC_API_KEY", SECRET)
    rows.append({"ts": ledger.now().isoformat(), "event": "brain", "provider": "claude", "user": "owner",
                 "cost_usd": 5.0})
    calls = patch_run(monkeypatch, ret=({"a": 1}, "", 0.01))
    with pytest.raises(BrainError, match="cap"):
        ClaudeBrain("owner", "api_key", "STUDIO_OWNER_ANTHROPIC_API_KEY").ask_json("read_brief", "x", SCHEMA)
    assert calls == []
    assert brain_rows(rows)[-1]["status"] == "blocked"


def test_claude_month_spend_counts_only_that_user_this_month(rows):
    month = ledger.now().isoformat()
    rows += [
        {"ts": month, "provider": "claude", "user": "owner", "cost_usd": 1.25},
        {"ts": month, "provider": "claude", "user": "partner", "cost_usd": 2.0},
        {"ts": month, "provider": "gemini", "user": "owner", "cost_usd": 3.0},
        {"ts": "2000-01-01T00:00:00+00:00", "provider": "claude", "user": "owner", "cost_usd": 4.0},
    ]
    assert ledger.claude_month_spend("owner") == 1.25


def test_gemini_brain_records_cost(rows, tmp_path):
    from PIL import Image
    img = tmp_path / "a.png"
    Image.new("RGB", (40, 30), "red").save(img)
    client = fake_client()

    res = GeminiBrain(client=client).ask_json("review", "check it", SCHEMA, images=[img])

    assert res.data == {"a": 1} and res.provider == "gemini" and res.credential == "gemini"
    assert res.cost_usd == round(1000 * 0.75 / 1e6 + 200 * 3.75 / 1e6, 6)
    rec = brain_rows(rows)[0]
    assert rec["tier"] == "brain" and rec["provider"] == "gemini" and rec["cost_usd"] == res.cost_usd
    assert rec["status"] == "ok" and rec["task"] == "review" and rec["user"] == "owner"
    call = client.models.calls[0]
    assert call["config"].response_json_schema == SCHEMA
    assert len(call["contents"]) == 2  # one image part, then the prompt


def test_gemini_brain_checks_budget_first(rows, monkeypatch):
    def over(provider, est):
        assert provider == "gemini" and est == {"cost_usd": 0.002}
        raise ledger.BudgetError("Gemini cap")

    monkeypatch.setattr(ledger, "check", over)
    client = fake_client()
    with pytest.raises(BrainError, match="Gemini cap"):
        GeminiBrain(client=client).ask_json("review", "x", SCHEMA)
    assert client.models.calls == []


def test_claude_prefers_structured_output(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_ANTHROPIC_API_KEY", SECRET)
    patch_run(monkeypatch, ret=({"a": 1}, "ignored", 0.003))

    res = ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN").ask_json("read_brief", "x", SCHEMA)
    assert res.data == {"a": 1} and res.provider == "claude" and res.cost_usd == 0
    sub = brain_rows(rows)[-1]
    assert sub["credential"] == "subscription" and sub["cost_usd"] == 0 and sub["usage_equiv_usd"] == 0.003

    res = ClaudeBrain("owner", "api_key", "STUDIO_OWNER_ANTHROPIC_API_KEY").ask_json("read_brief", "x", SCHEMA)
    api = brain_rows(rows)[-1]
    assert api["credential"] == "api_key" and api["cost_usd"] == 0.003 and res.cost_usd == 0.003
    assert ledger.claude_month_spend("owner") == 0.003
    assert all(SECRET not in json.dumps(r) for r in rows)


def test_claude_json_parsing_fallback(rows, monkeypatch):
    b = ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    patch_run(monkeypatch, ret=(None, '```json\n{"a": 1}\n```', 0.0))
    assert b.ask_json("read_brief", "x", SCHEMA).data == {"a": 1}
    patch_run(monkeypatch, ret=(None, 'Here you go: {"a": 2} hope it helps', 0.0))
    assert b.ask_json("read_brief", "x", SCHEMA).data == {"a": 2}
    patch_run(monkeypatch, ret=(None, "no json here", 0.0))
    with pytest.raises(BrainError):
        b.ask_json("read_brief", "x", SCHEMA)
    assert brain_rows(rows)[-1]["status"] == "error"


def test_claude_timeout_is_a_brain_error(rows, monkeypatch):
    patch_run(monkeypatch, exc=TimeoutError())
    with pytest.raises(BrainError, match="60 s"):
        ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN").ask_json("read_brief", "x", SCHEMA)


def test_claude_env_per_credential(rows, monkeypatch):
    calls = patch_run(monkeypatch, ret=({"a": 1}, "", 0.0))
    sub = ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    sub.ask_json("t", "x", SCHEMA)
    assert calls[-1]["env"] == cred_env()  # blank token: the owner's logged-in session, all three blanked
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", "tok-OWNER")
    sub.ask_json("t", "x", SCHEMA)
    assert calls[-1]["env"] == cred_env(CLAUDE_CODE_OAUTH_TOKEN="tok-OWNER")
    monkeypatch.setenv("STUDIO_OWNER_ANTHROPIC_API_KEY", SECRET)
    ClaudeBrain("owner", "api_key", "STUDIO_OWNER_ANTHROPIC_API_KEY").ask_json("t", "x", SCHEMA)
    assert calls[-1]["env"] == cred_env(ANTHROPIC_API_KEY=SECRET)


@pytest.mark.parametrize("name", COMPETING)
def test_competing_credential_in_process_env_is_blanked(rows, monkeypatch, name):
    monkeypatch.setenv(name, "server-level-CREDENTIAL")
    monkeypatch.setenv("STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN", "tok-PARTNER")
    calls = patch_run(monkeypatch, ret=({"a": 1}, "", 0.0))
    b = ClaudeBrain("partner", "subscription", "STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN")
    b.ask_json("t", "x", SCHEMA, user="partner")
    # the server's own credential never reaches the CLI
    assert calls[-1]["env"] == cred_env(CLAUDE_CODE_OAUTH_TOKEN="tok-PARTNER")


@pytest.mark.parametrize("name", ROUTING)
def test_routing_override_raises(rows, monkeypatch, name):
    monkeypatch.setenv(name, "https://proxy.example/SECRET-ROUTE")
    calls = patch_run(monkeypatch, ret=({"a": 1}, "", 0.0))
    with pytest.raises(BrainError, match=name) as e:
        ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN").ask_json("t", "x", SCHEMA)
    assert calls == []
    assert "SECRET-ROUTE" not in str(e.value) and all("SECRET-ROUTE" not in json.dumps(r) for r in rows)
    assert brain_rows(rows)[-1]["status"] == "error"


def _decide(gate, path) -> dict:
    return asyncio.run(gate({"hook_event_name": "PreToolUse", "tool_name": "Read",
                             "tool_input": {"file_path": str(path)}, "tool_use_id": "t1"}, "t1", {"signal": None}))


def test_read_gate_allows_only_the_call_images(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    gate = claude_mod.read_gate([img])
    assert _decide(gate, img) == {}
    assert _decide(gate, tmp_path / "sub" / ".." / "a.jpg") == {}  # resolves to the image
    for bad in (ROOT / ".env", tmp_path / "sub" / ".." / ".." / "a.jpg", "a.jpg"):
        out = _decide(gate, bad)["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
        assert out["permissionDecisionReason"]
    no_path = asyncio.run(gate({"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}, None, {}))
    assert no_path["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("mode", ["result", "result_then_raise", "raise_only"])
def test_error_result_cost_is_recorded(rows, monkeypatch, mode):
    monkeypatch.setenv("STUDIO_OWNER_ANTHROPIC_API_KEY", SECRET)
    msg = ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=2,
                        session_id="s", total_cost_usd=0.01, result="")

    async def fake_query(prompt, options):
        if mode != "raise_only":
            yield msg
        if mode != "result":
            raise ResultError("Claude run failed", data={"is_error": True, "total_cost_usd": 0.01}, exit_code=1)

    monkeypatch.setattr(claude_mod, "query", fake_query)
    with pytest.raises(BrainError):
        ClaudeBrain("owner", "api_key", "STUDIO_OWNER_ANTHROPIC_API_KEY").ask_json("t", "x", SCHEMA)
    rec = brain_rows(rows)[-1]
    assert rec["status"] == "error" and rec["cost_usd"] == 0.01
    assert ledger.claude_month_spend("owner") == 0.01


def test_only_owner_may_use_logged_in_session(rows, monkeypatch):
    calls = patch_run(monkeypatch, ret=({"a": 1}, "", 0.0))
    b = ClaudeBrain("partner", "subscription", "STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN")
    with pytest.raises(BrainError, match="STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN"):
        b.ask_json("t", "x", SCHEMA, user="partner")
    assert calls == []
    with pytest.raises(ValueError):  # a brain only answers for the user whose credential it holds
        b.ask_json("t", "x", SCHEMA, user="owner")


def test_claude_options_slim_setup(tmp_path):
    b = ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    prompt, o = b._options("read_brief", "olive hoodie", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}, [], SCHEMA)
    assert prompt == "olive hoodie"
    assert o.model == "claude-sonnet-5-5" and o.tools == [] and o.max_turns == 2 and o.setting_sources == []
    assert o.output_format == {"type": "json_schema", "schema": SCHEMA} and o.env == {"CLAUDE_CODE_OAUTH_TOKEN": "t"}
    assert "read_brief" in o.system_prompt and "\n" not in o.system_prompt and not o.plugins and not o.hooks
    img = tmp_path / "a.jpg"
    prompt, o = b._options("review", "check", {}, [img], SCHEMA)
    assert o.tools == ["Read"] and o.allowed_tools == ["Read"] and o.max_turns == 4
    assert str(img.resolve()) in prompt
    (matcher,) = o.hooks["PreToolUse"]  # every Read goes through the image gate
    assert matcher.matcher == "Read" and len(matcher.hooks) == 1 and matcher.on_failure == "block"
    assert _decide(matcher.hooks[0], ROOT / ".env")["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_brain_repr_has_no_credential_value(monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE_OAUTH_TOKEN", SECRET)
    b = ClaudeBrain("owner", "subscription", "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN")
    assert SECRET not in repr(b) and SECRET not in repr(vars(b))
