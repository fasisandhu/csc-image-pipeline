"""Claude brain through claude_agent_sdk, on the user's own credential (see spikes/claude_sdk/FINDINGS.md).

Slim setup for structured tasks: no project settings, no plugin, no tools unless images must be read.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import time
from contextlib import aclosing
from pathlib import Path
from typing import Sequence

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from pipeline import ledger
from pipeline.ledger import ROOT

from .base import BrainError, BrainResult, env_prefix, log_call, scrub

DEFAULT_MODEL = "claude-sonnet-5-5"
TIMEOUT_S = 60
DEFAULT_CAP_USD = 5.0
CREDENTIALS = ("subscription", "api_key")
_TASK: contextvars.ContextVar[str] = contextvars.ContextVar("brain_task", default="brain")
_FENCE = re.compile(r"```[A-Za-z]*")


def first_json_object(text: str) -> dict | None:
    """The first JSON object in `text`, with code fences stripped."""
    text = _FENCE.sub("", text or "")
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text, i)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


class ClaudeBrain:
    name = "claude"

    def __init__(self, user: str, credential: str, token_env: str | None, model: str = DEFAULT_MODEL):
        if credential not in CREDENTIALS:
            raise ValueError(f"ClaudeBrain credential must be one of {CREDENTIALS}")
        self.prefix = env_prefix(user)
        self.user, self.credential, self.token_env, self.model = user, credential, token_env, model

    def __repr__(self) -> str:
        return f"ClaudeBrain(user={self.user!r}, credential={self.credential!r}, model={self.model!r})"

    def cap_usd(self) -> float:
        return float(os.environ.get(self.prefix + "CLAUDE_CAP_USD") or DEFAULT_CAP_USD)

    def _env(self) -> dict:
        """Per-call credential env. Values are read here and go only to the SDK."""
        value = (os.environ.get(self.token_env) or "").strip() if self.token_env else ""
        if self.credential == "api_key":
            if not value:
                raise BrainError(f"claude: {self.token_env} is not set")
            return {"ANTHROPIC_API_KEY": value}
        if value:
            return {"CLAUDE_CODE_OAUTH_TOKEN": value}
        if self.user != "owner":  # the logged-in Claude Code session belongs to whoever runs the server
            raise BrainError(f"claude: {self.token_env} is not set; only the owner may use the logged-in session")
        return {}

    def _options(self, task: str, prompt: str, env: dict, images: Sequence[Path],
                 schema: dict) -> tuple[str, ClaudeAgentOptions]:
        if images:
            paths = ", ".join(str(Path(p).resolve()) for p in images)
            prompt = f"{prompt}\n\nRead these image files with the Read tool before answering: {paths}"
            tools = {"tools": ["Read"], "allowed_tools": ["Read"], "max_turns": 4}
        else:
            tools = {"tools": [], "max_turns": 2}
        system = f"You are the Cut Sew Club studio brain doing the task '{task}'; answer only with the JSON asked for."
        options = ClaudeAgentOptions(
            model=self.model, cwd=str(ROOT), env=env, setting_sources=[], system_prompt=system,
            output_format={"type": "json_schema", "schema": schema}, **tools,
        )
        return prompt, options

    def _run(self, prompt: str, env: dict, images: Sequence[Path], schema: dict) -> tuple[dict | None, str, float]:
        """The only SDK call: (structured_output, result text, total_cost_usd).

        Synchronous: it starts its own event loop with asyncio.run, so it must not be called from inside a
        running event loop (call ask_json from plain `def` code). Raises TimeoutError after TIMEOUT_S.
        """
        prompt, options = self._options(_TASK.get(), prompt, env, images, schema)

        async def ask() -> ResultMessage | None:
            result = None
            async with aclosing(query(prompt=prompt, options=options)) as stream:
                async for m in stream:
                    if isinstance(m, ResultMessage):
                        result = m
            return result

        result = asyncio.run(asyncio.wait_for(ask(), TIMEOUT_S))
        if result is None:
            raise BrainError("claude: no result message")
        if result.is_error:
            raise BrainError(f"claude: {result.subtype}: {(result.result or '')[:200]}")
        return result.structured_output, result.result or "", float(result.total_cost_usd or 0.0)

    def _costs(self, total: float) -> tuple[float, dict]:
        """(billed cost_usd, extra record fields). Subscription use is not billed; keep its usage equivalent."""
        if self.credential == "subscription":
            return 0.0, {"usage_equiv_usd": round(total, 6)}
        return total, {}

    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (),
                 user: str = "owner", *, fallback: bool = False) -> BrainResult:
        """Synchronous; never call from inside a running event loop (see _run)."""
        if user != self.user:
            raise ValueError(f"this ClaudeBrain holds {self.user!r}'s credential, not {user!r}'s")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("ClaudeBrain.ask_json is synchronous; call it from plain def code, not an event loop")

        base = {"event": "brain", "user": self.user, "task": task, "provider": "claude", "model": self.model,
                "credential": self.credential, "fallback": fallback}
        if self.credential == "api_key":
            spent, cap = ledger.claude_month_spend(self.user), self.cap_usd()
            if spent >= cap:
                msg = f"claude: {self.user}'s API-key spend ${spent:.2f} this month reached the ${cap:.2f} cap"
                log_call(base, "blocked", 0.0, 0.0, error=msg)
                raise BrainError(msg)

        t0 = time.perf_counter()
        secrets: list[str] = []
        total = 0.0
        try:
            env = self._env()
            secrets = list(env.values())
            token = _TASK.set(task)
            try:
                structured, text, total = self._run(prompt, env, images, schema)
            finally:
                _TASK.reset(token)
            data = structured if isinstance(structured, dict) else first_json_object(text)
            if data is None:
                raise BrainError("claude: answer had no JSON object")
        except Exception as e:  # noqa: BLE001 - any failure is logged and becomes a BrainError
            if isinstance(e, TimeoutError):
                msg = f"claude: no answer within {TIMEOUT_S} s"
            elif isinstance(e, BrainError):
                msg = str(e)
            else:
                msg = f"claude: {type(e).__name__}: {e}"
            msg = scrub(msg, secrets)
            cost, extra = self._costs(total)
            log_call(base, "error", time.perf_counter() - t0, cost, error=msg, **extra)
            raise BrainError(msg) from None

        seconds = time.perf_counter() - t0
        cost, extra = self._costs(total)
        log_call(base, "ok", seconds, cost, **extra)
        return BrainResult(data, "claude", self.model, self.credential, round(seconds, 2), round(cost, 6), fallback)
