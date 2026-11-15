"""WarmClaude: one warm ClaudeSDKClient per user and task schema. A fake client stands in: no Claude process."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ClaudeSDKError, ConversationResetMessage, ResultError, ResultMessage

from pipeline import ledger
from studio import brain
from studio.brain import BrainError, ClaudeBrain, FallbackBrain, GeminiBrain, warm
from studio.brain import claude as claude_mod
from studio.brain.warm import WarmClaude
from test_brain import (ROUTING, SCHEMA, SECRET, brain_dir, brain_rows, cred_env, fake_client,  # noqa: F401  (fixtures)
                        rows)

OWNER_TOKEN = "STUDIO_OWNER_CLAUDE_OAUTH_TOKEN"
PARTNER_TOKEN = "STUDIO_PARTNER_CLAUDE_OAUTH_TOKEN"
OWNER_KEY = "STUDIO_OWNER_ANTHROPIC_API_KEY"
OTHER_SCHEMA = {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]}


def result(total: float, structured=None, is_error=False, subtype="success", turns=1) -> ResultMessage:
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error, num_turns=turns,
                         session_id="s", total_cost_usd=total, result="", structured_output=structured)


class Fake:
    """Stands in for ClaudeSDKClient. Its total_cost_usd is a running total for the session, zeroed by /clear,
    as the CLI reports it.

    `steps` scripts the answers to real (non-/clear) queries in order: "ok", "error_result", "hang" (until the
    client is closed), an exception, or a callable(client) returning one. When empty: "ok".
    """

    def __init__(self):
        self.made: list = []
        self.steps: list = []
        self.delay = 0.0
        self.cost = 0.001
        self.clear_resets = True
        self.closed_while_busy = 0
        fake = self

        class Client:
            def __init__(self, options=None):
                self.options, self.prompts, self.composed = options, [], []
                self.connects = self.disconnects = self.active = self.overlaps = 0
                self.connected, self.total = False, 0.0
                self.closed = asyncio.Event()
                fake.made.append(self)

            async def connect(self, prompt=None):
                self.connects += 1
                self.connected = True

            async def query(self, prompt, session_id="default"):
                assert self.connected, "query on a closed client"
                composed = not isinstance(prompt, str)
                if composed:  # a stream of message dicts, as ClaudeSDKClient.query accepts
                    (msg,) = [m async for m in prompt]
                    assert msg["type"] == "user" and msg["message"]["role"] == "user"
                    composed, prompt = msg.get("client_composed") is True, msg["message"]["content"]
                self.composed.append(composed)
                self.active += 1
                self.overlaps += self.active > 1
                self.prompts.append(prompt)

            async def receive_response(self):
                try:
                    if self.prompts[-1] == "/clear":
                        if fake.clear_resets:
                            self.total = 0.0
                            yield ConversationResetMessage(new_conversation_id="c", uuid="u", session_id="s")
                        yield result(self.total, turns=0)
                        return
                    step = fake.steps.pop(0) if fake.steps else "ok"
                    await asyncio.sleep(fake.delay)
                    if step == "hang":
                        await self.closed.wait()
                        raise ClaudeSDKError("stream closed")
                    if callable(step) and not isinstance(step, type):
                        step = step(self)
                    if isinstance(step, BaseException):
                        raise step
                    self.total += fake.cost
                    if step == "error_result":
                        yield result(self.total, is_error=True, subtype="error_max_turns")
                        return
                    n = sum(p != "/clear" for p in self.prompts)
                    yield result(self.total, structured={"a": n})
                finally:
                    self.active -= 1

            async def disconnect(self):
                fake.closed_while_busy += self.active > 0
                self.connected = False
                self.disconnects += 1
                self.closed.set()

        self.Client = Client


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(warm, "ClaudeSDKClient", f.Client)
    pool = warm._Pool()
    monkeypatch.setattr(warm, "_pool", pool)
    yield f
    pool.shutdown()


def until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out waiting"
        time.sleep(0.01)


def real(prompts):
    return [p for p in prompts if p != "/clear"]


def test_client_reused_for_same_user_and_schema(rows, fake, tmp_path):
    answers = [WarmClaude("owner", "subscription", OWNER_TOKEN).ask_json("read_brief", f"brief {i}", SCHEMA)
               for i in range(3)]  # a new WarmClaude per call, as get_brain makes one per request

    assert [a.data for a in answers] == [{"a": 1}, {"a": 2}, {"a": 3}]
    assert all(a.provider == "claude" and a.credential == "subscription" for a in answers)
    (c,) = fake.made
    until(lambda: len(c.prompts) == 6)
    assert c.connects == 1 and c.disconnects == 0
    # each answer is followed by /clear: every brief is read on its own, not after the earlier ones
    assert c.prompts == ["brief 0", "/clear", "brief 1", "/clear", "brief 2", "/clear"]
    # briefs go as written (no @path file expansion, no slash command); only /clear runs as a command
    assert c.composed == [True, False] * 3
    o = c.options  # ClaudeBrain's slim setup, with this schema
    assert o.tools == [] and o.setting_sources == [] and o.max_turns == 2 and not o.plugins and not o.hooks
    assert o.output_format == {"type": "json_schema", "schema": SCHEMA} and "read_brief" in o.system_prompt
    assert o.env == cred_env() and Path(o.cwd) == tmp_path / "brain" / "owner"


def test_read_brief_runs_at_low_effort_other_tasks_at_default(rows, fake):
    b = WarmClaude("owner", "subscription", OWNER_TOKEN)
    b.ask_json("read_brief", "olive hoodie", SCHEMA)
    b.ask_json("check_layout", "x", SCHEMA)
    brief, other = fake.made
    assert brief.options.effort == "low" and other.options.effort is None
    assert "read_brief" in brief.options.system_prompt and "check_layout" in other.options.system_prompt


def test_prompts_sent_as_written(rows, fake):
    """Typed text never triggers @path expansion or a slash command: every prompt is a client-composed message.
    The session itself does not set verbatim_prompts, which has no per-message opt-out and would stop /clear."""
    b = WarmClaude("owner", "subscription", OWNER_TOKEN)
    b.ask_json("read_brief", "@../../.env olive", SCHEMA)
    b.ask_json("read_brief", "/model claude-haiku", SCHEMA)
    (c,) = fake.made
    until(lambda: len(c.prompts) == 4)
    assert c.prompts == ["@../../.env olive", "/clear", "/model claude-haiku", "/clear"]
    assert c.composed == [True, False, True, False] and c.options.verbatim_prompts is False


def test_separate_clients_per_user_and_schema(rows, fake, monkeypatch, tmp_path):
    monkeypatch.setenv(PARTNER_TOKEN, "tok-PARTNER")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-level-CREDENTIAL")  # never reaches either client
    owner = WarmClaude("owner", "subscription", OWNER_TOKEN)
    partner = WarmClaude("partner", "subscription", PARTNER_TOKEN)

    owner.ask_json("read_brief", "a", SCHEMA)
    owner.ask_json("read_brief", "b", OTHER_SCHEMA)
    partner.ask_json("read_brief", "c", SCHEMA, user="partner")
    owner.ask_json("read_brief", "d", SCHEMA)
    partner.ask_json("read_brief", "e", SCHEMA, user="partner")

    assert len(fake.made) == 3 and all(c.connects == 1 for c in fake.made)
    own_a, own_b, part = fake.made
    assert real(own_a.prompts) == ["a", "d"] and real(own_b.prompts) == ["b"] and real(part.prompts) == ["c", "e"]
    assert own_b.options.output_format["schema"] == OTHER_SCHEMA
    assert own_a.options.env == own_b.options.env == cred_env()
    assert part.options.env == cred_env(CLAUDE_CODE_OAUTH_TOKEN="tok-PARTNER")
    assert Path(part.options.cwd) == tmp_path / "brain" / "partner"


@pytest.mark.parametrize("step", ["raise", "error_result", "hang"])
def test_error_closes_client_and_falls_back(rows, fake, monkeypatch, step):
    monkeypatch.setattr(claude_mod, "TIMEOUT_S", 0.3)
    monkeypatch.setenv(OWNER_KEY, SECRET)
    fake.cost = 0.01
    fake.steps = [ResultError("Claude run failed", data={"total_cost_usd": 0.01}, exit_code=1)
                  if step == "raise" else step]
    b = FallbackBrain(WarmClaude("owner", "api_key", OWNER_KEY), GeminiBrain(client=fake_client()))

    t0 = time.monotonic()
    res = b.ask_json("read_brief", "olive hoodie", SCHEMA)

    assert res.provider == "gemini" and res.fallback is True
    assert time.monotonic() - t0 < 2  # the call's own timeout, not the calling thread's backstop
    (c,) = fake.made
    until(lambda: c.disconnects == 1)
    assert c.prompts == ["olive hoodie"]  # one query: no retry, no /clear on a failed client
    recs = brain_rows(rows)
    assert [(r["provider"], r["status"]) for r in recs] == [("claude", "error"), ("gemini", "ok")]
    assert recs[0]["cost_usd"] == (0.0 if step == "hang" else 0.01)  # a failed run is still billed
    if step == "hang":
        assert "no answer within 0.3 s" in recs[0]["error"]
    assert all(SECRET not in json.dumps(r) for r in rows)

    res = b.ask_json("read_brief", "navy hoodie", SCHEMA)  # the next call opens a new client
    assert res.provider == "claude" and res.fallback is False
    assert len(fake.made) == 2 and fake.made[1].connects == 1 and real(fake.made[1].prompts) == ["navy hoodie"]


def test_idle_client_closed(rows, fake):
    b = WarmClaude("owner", "subscription", OWNER_TOKEN, idle_s=0.1)
    b.ask_json("read_brief", "x", SCHEMA)
    (c,) = fake.made
    until(lambda: c.disconnects == 1)
    assert c.prompts == ["x", "/clear"]

    b.ask_json("read_brief", "y", SCHEMA)
    assert len(fake.made) == 2 and real(fake.made[1].prompts) == ["y"]


def test_idle_close_never_races_a_running_query(rows, fake):
    """The idle timer of an earlier call fires while a later call is using the client: the close waits for that
    call, which restarted the clock, so the client stays open."""
    b = WarmClaude("owner", "subscription", OWNER_TOKEN)
    b.ask_json("read_brief", "a", SCHEMA)
    pool = warm._pool
    ((_, s),) = pool._sessions.items()
    stale = s.uses  # what the idle timer started after call "a" holds
    (c,) = fake.made
    fake.delay = 0.3
    with ThreadPoolExecutor(1) as ex:
        call = ex.submit(b.ask_json, "read_brief", "b", SCHEMA)
        until(lambda: c.prompts[-1:] == ["b"] and c.active)
        reap = asyncio.run_coroutine_threadsafe(pool._reap(s, stale), pool._loop)  # call "a"'s timer fires now
        assert call.result(timeout=5).provider == "claude"
    reap.result(timeout=5)
    assert fake.closed_while_busy == 0 and c.disconnects == 0
    asyncio.run_coroutine_threadsafe(pool._reap(s, s.uses), pool._loop).result(timeout=5)  # truly idle: closes
    assert c.disconnects == 1 and fake.closed_while_busy == 0


def test_short_idle_time_under_load(rows, fake):
    fake.delay = 0.03
    b = WarmClaude("owner", "subscription", OWNER_TOKEN, idle_s=0.02)  # shorter than one query
    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(lambda i: b.ask_json("read_brief", f"brief {i}", SCHEMA), range(12)))
    assert all(r.provider == "claude" for r in results)
    assert fake.closed_while_busy == 0 and [r["status"] for r in brain_rows(rows)] == ["ok"] * 12


def test_concurrent_calls_on_one_client_do_not_interleave(rows, fake):
    fake.delay = 0.05
    b = WarmClaude("owner", "subscription", OWNER_TOKEN)
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda i: b.ask_json("read_brief", f"brief {i}", SCHEMA), range(4)))

    (c,) = fake.made
    until(lambda: len(c.prompts) == 8)
    assert c.connects == 1 and c.overlaps == 0
    assert sorted(real(c.prompts)) == [f"brief {i}" for i in range(4)] and c.prompts[1::2] == ["/clear"] * 4
    assert sorted(r.data["a"] for r in results) == [1, 2, 3, 4]


def test_records_like_claude_brain_without_secrets(rows, fake, monkeypatch):
    monkeypatch.setenv(OWNER_TOKEN, SECRET)
    monkeypatch.setenv(OWNER_KEY, SECRET + "-api")
    monkeypatch.setattr(ClaudeBrain, "_run", lambda self, prompt, env, images, schema: ({"a": 1}, "", 0.001))
    for cred, env_name in (("subscription", OWNER_TOKEN), ("api_key", OWNER_KEY)):
        ClaudeBrain("owner", cred, env_name).ask_json("read_brief", "x", SCHEMA)
        WarmClaude("owner", cred, env_name).ask_json("read_brief", "x", SCHEMA)
        WarmClaude("owner", cred, env_name).ask_json("read_brief", "y", SCHEMA)  # its own cost, not the running total

    recs = brain_rows(rows)
    same = [{k: v for k, v in r.items() if k not in ("ts", "seconds")} for r in recs]
    assert same[0] == same[1] == same[2] and same[3] == same[4] == same[5]
    assert same[0]["credential"] == "subscription" and same[0]["cost_usd"] == 0 and same[0]["usage_equiv_usd"] == 0.001
    assert same[3]["credential"] == "api_key" and same[3]["cost_usd"] == 0.001 and "usage_equiv_usd" not in same[3]
    assert ledger.claude_month_spend("owner") == 0.003
    # the credential changed for the same user and schema: the old client closed, a new one opened
    assert len(fake.made) == 2 and fake.made[0].disconnects == 1
    assert fake.made[1].options.env == cred_env(ANTHROPIC_API_KEY=SECRET + "-api")

    fake.steps = [lambda client: ClaudeSDKError(f"CLI failed, options env={client.options.env}")]
    b = WarmClaude("owner", "subscription", OWNER_TOKEN)
    with pytest.raises(BrainError) as e:
        b.ask_json("read_brief", "z", SCHEMA)
    assert "CLI failed" in str(e.value) and SECRET not in str(e.value)
    assert brain_rows(rows)[-1]["status"] == "error" and "CLI failed" in brain_rows(rows)[-1]["error"]
    assert all(SECRET not in json.dumps(r) for r in rows)
    assert SECRET not in repr(b) and SECRET not in repr(vars(b)) and "WarmClaude(" in repr(b)
    assert SECRET not in repr(vars(warm._pool))


def test_api_key_cap_blocks_before_opening(rows, fake, monkeypatch):
    monkeypatch.setenv(OWNER_KEY, SECRET)
    rows.append({"ts": ledger.now().isoformat(), "event": "brain", "provider": "claude", "user": "owner",
                 "cost_usd": 5.0})
    with pytest.raises(BrainError, match="cap"):
        WarmClaude("owner", "api_key", OWNER_KEY).ask_json("read_brief", "x", SCHEMA)
    assert fake.made == [] and brain_rows(rows)[-1]["status"] == "blocked"


@pytest.mark.parametrize("name", ROUTING)
def test_routing_override_refused(rows, fake, monkeypatch, name):
    monkeypatch.setenv(name, "https://proxy.example/SECRET-ROUTE")
    with pytest.raises(BrainError, match=name) as e:
        WarmClaude("owner", "subscription", OWNER_TOKEN).ask_json("read_brief", "x", SCHEMA)
    assert fake.made == [] and "SECRET-ROUTE" not in str(e.value)
    assert brain_rows(rows)[-1]["status"] == "error"


def test_only_owner_may_use_logged_in_session(rows, fake):
    b = WarmClaude("partner", "subscription", PARTNER_TOKEN)
    with pytest.raises(BrainError, match=PARTNER_TOKEN):
        b.ask_json("read_brief", "x", SCHEMA, user="partner")
    with pytest.raises(ValueError):  # a brain only answers for the user whose credential it holds
        b.ask_json("read_brief", "x", SCHEMA, user="owner")
    assert fake.made == []


def test_unconfirmed_clear_closes_the_client(rows, fake, monkeypatch):
    """A /clear the CLI does not confirm closes the client: no answer ever sees an earlier brief, and each
    call's total_cost_usd is its own."""
    monkeypatch.setenv(OWNER_KEY, SECRET)
    fake.clear_resets = False
    b = WarmClaude("owner", "api_key", OWNER_KEY)
    b.ask_json("read_brief", "x", SCHEMA)
    until(lambda: fake.made[0].disconnects == 1)
    b.ask_json("read_brief", "y", SCHEMA)
    assert len(fake.made) == 2 and [r["cost_usd"] for r in brain_rows(rows)] == [0.001, 0.001]


def test_images_go_to_a_one_off_query(rows, fake, monkeypatch, tmp_path):
    calls = []

    def run(self, prompt, env, images, schema):
        calls.append(list(images))
        return {"a": 1}, "", 0.0
    monkeypatch.setattr(ClaudeBrain, "_run", run)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    res = WarmClaude("owner", "subscription", OWNER_TOKEN).ask_json("review", "check", SCHEMA, images=[img])
    assert res.provider == "claude" and calls == [[img]] and fake.made == []


def test_shutdown_closes_clients_and_stops_the_loop(rows, fake, monkeypatch):
    monkeypatch.setenv(PARTNER_TOKEN, "tok-PARTNER")
    WarmClaude("owner", "subscription", OWNER_TOKEN).ask_json("read_brief", "x", SCHEMA)
    WarmClaude("partner", "subscription", PARTNER_TOKEN).ask_json("read_brief", "x", SCHEMA, user="partner")
    thread = warm._pool._thread
    assert thread.is_alive()

    warm.shutdown()
    assert [c.disconnects for c in fake.made] == [1, 1] and not any(c.connected for c in fake.made)
    assert not thread.is_alive()
    warm.shutdown()  # idempotent

    WarmClaude("owner", "subscription", OWNER_TOKEN).ask_json("read_brief", "y", SCHEMA)  # a later call starts afresh
    assert len(fake.made) == 3


def test_shutdown_ends_a_call_in_flight(rows, fake):
    fake.steps = ["hang"]
    b = FallbackBrain(WarmClaude("owner", "subscription", OWNER_TOKEN), GeminiBrain(client=fake_client()))
    with ThreadPoolExecutor(1) as ex:
        call = ex.submit(b.ask_json, "read_brief", "x", SCHEMA)
        until(lambda: fake.made and fake.made[0].active)
        t0 = time.monotonic()
        warm.shutdown()
        res = call.result(timeout=5)
    assert time.monotonic() - t0 < 5 and res.provider == "gemini" and res.fallback is True
    assert fake.made[0].disconnects == 1 and not warm._pool._thread


def test_fast_brain_is_warm_when_claude_mode(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    fast = brain.get_brain("owner", fast=True)  # STUDIO_FAST_TASKS unset: claude
    assert isinstance(fast, FallbackBrain) and isinstance(fast.fallback, GeminiBrain)
    assert type(fast.primary) is WarmClaude and fast.primary.idle_s == 600
    assert (fast.primary.user, fast.primary.credential, fast.primary.token_env) == ("owner", "subscription", OWNER_TOKEN)
    monkeypatch.setenv("STUDIO_FAST_TASKS", "claude")
    assert type(brain.get_brain("owner", fast=True).primary) is WarmClaude
    assert type(brain.get_brain("owner").primary) is ClaudeBrain  # slow tasks: one query per call, as before
    monkeypatch.setenv("STUDIO_FAST_TASKS", "gemini")
    assert isinstance(brain.get_brain("owner", fast=True), GeminiBrain)
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "none")
    assert isinstance(brain.get_brain("owner", fast=True), GeminiBrain)


# --- final review I3: a slow Claude reaches the Gemini fallback inside the page's 30 s ----------------
def test_fast_tasks_use_the_short_timeout(rows, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    assert warm.FAST_TIMEOUT_S == 20 and claude_mod.TIMEOUT_S == 60
    fast, slow = brain.get_brain("owner", fast=True), brain.get_brain("owner")
    assert fast.primary.timeout() == 20  # the page gives up at 30 s: Claude's 20 s leaves room for Gemini
    assert slow.primary.timeout() == 60  # other tasks keep the long timeout
    assert WarmClaude("owner", "subscription", OWNER_TOKEN).timeout() == 60  # the class default is unchanged


def test_hung_fast_claude_falls_back_to_gemini_within_the_short_timeout(rows, fake, monkeypatch):
    monkeypatch.setenv("STUDIO_OWNER_CLAUDE", "subscription")
    monkeypatch.setattr(warm, "FAST_TIMEOUT_S", 0.3)  # stands in for 20 s; claude.TIMEOUT_S stays 60
    monkeypatch.setattr(brain, "GeminiBrain", lambda: GeminiBrain(client=fake_client()))
    fake.steps = ["hang"]
    b = brain.get_brain("owner", fast=True)

    t0 = time.monotonic()
    res = b.ask_json("read_brief", "olive hoodie", SCHEMA)

    assert res.provider == "gemini" and res.fallback is True
    assert time.monotonic() - t0 < 2
    recs = brain_rows(rows)
    assert [(r["provider"], r["status"]) for r in recs] == [("claude", "error"), ("gemini", "ok")]
    assert "no answer within 0.3 s" in recs[0]["error"]
    until(lambda: fake.made[0].disconnects == 1)  # the hung client is closed, never retried


def test_thread_backstop_follows_the_short_timeout(rows, fake, monkeypatch):
    """The calling thread's own wait (for a loop that never answers) is the call's timeout plus a small grace,
    not claude.TIMEOUT_S plus it."""
    monkeypatch.setattr(warm, "WAIT_GRACE_S", 0.1)
    b = WarmClaude("owner", "subscription", OWNER_TOKEN, timeout_s=0.2)
    b.ask_json("read_brief", "warm up", SCHEMA)  # starts the loop thread
    warm._pool._loop.call_soon_threadsafe(time.sleep, 1.0)  # the loop itself is stuck for 1 s

    t0 = time.monotonic()
    with pytest.raises(BrainError, match="no answer within 0.2 s"):
        b.ask_json("read_brief", "x", SCHEMA)
    assert time.monotonic() - t0 < 0.8


def test_main_shuts_warm_sessions_down_after_serve(monkeypatch):
    from studio import __main__ as main_mod
    from studio import db
    calls: list[str] = []
    monkeypatch.setattr(main_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(db, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "mark_interrupted", lambda conn: 0)
    monkeypatch.setattr(main_mod, "Worker", lambda connect: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(warm, "shutdown", lambda: calls.append("shutdown"))
    monkeypatch.setattr(main_mod, "serve", lambda sock: calls.append("serve"))
    assert main_mod.main(0) == 0 and calls == ["serve", "shutdown"]

    def crash(sock):
        calls.append("serve")
        raise RuntimeError("server crashed")
    monkeypatch.setattr(main_mod, "serve", crash)
    with pytest.raises(RuntimeError):
        main_mod.main(0)
    assert calls == ["serve", "shutdown", "serve", "shutdown"]
