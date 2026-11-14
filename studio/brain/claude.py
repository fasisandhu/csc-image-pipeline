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

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, query

from pipeline import ledger
from pipeline.ledger import ROOT

from .base import BrainError, BrainResult, env_prefix, log_call, scrub

DEFAULT_MODEL = "claude-sonnet-5-5"
TIMEOUT_S = 60
DEFAULT_CAP_USD = 5.0
CREDENTIALS = ("subscription", "api_key")
# The SDK starts the CLI with {**os.environ, **options.env}: every call sets all three, unused ones to "".
COMPETING_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
ROUTING_OVERRIDES = ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
# Every other secret the server holds (other users' studio tokens, provider keys) is blanked as well.
_SENSITIVE = re.compile(r"KEY|TOKEN|SECRET", re.I)
# Per-user working folder: the CLI keeps its transcripts per cwd, so they stay out of the repo's history.
BRAIN_DIR = ROOT / "out" / "brain"
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


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def read_gate(images: Sequence[Path], cwd: Path):
    """PreToolUse hook for Read: deny every path except the images given to this call (compared resolved).

    allowed_tools=["Read"] approves any Read before a permission callback runs, so this hook is the gate
    that stops a prompt injected in an image from reading .env, client artwork or Claude's credentials.
    A relative path is resolved against `cwd`, the folder the CLI runs in, as the CLI itself would.
    """
    allowed = {_norm(Path(p)) for p in images}

    async def gate(input_data: dict, tool_use_id: str | None, context: dict) -> dict:
        raw = (input_data.get("tool_input") or {}).get("file_path")
        if isinstance(raw, str) and raw:
            path = Path(raw)
            if _norm(path if path.is_absolute() else Path(cwd) / path) in allowed:
                return {}  # no opinion: allowed_tools approves the Read
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Only the image files attached to this task may be read."}}

    return gate


def _cost(result: ResultMessage | None) -> float:
    return float(getattr(result, "total_cost_usd", None) or 0.0)


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
        """Per-call env: every STUDIO_ or secret-named (KEY, TOKEN, SECRET) server variable and all three
        competing credential names set to "", then the user's one credential set.

        A credential in the server's own environment therefore never stands in for the user's, and other
        users' tokens and the provider keys never reach the CLI. Routing overrides (base URL, Bedrock,
        Vertex) would send the call elsewhere, so they stop it. Values are read here and go only to the
        SDK; messages name variables, never values.
        """
        for name in ROUTING_OVERRIDES:
            if os.environ.get(name):
                raise BrainError(f"claude: {name} is set in the server environment; unset it to use Claude")
        value = (os.environ.get(self.token_env) or "").strip() if self.token_env else ""
        env = {k: "" for k in os.environ if k.upper().startswith("STUDIO_") or _SENSITIVE.search(k)}
        env.update(dict.fromkeys(COMPETING_CREDENTIALS, ""))
        if self.credential == "api_key":
            if not value:
                raise BrainError(f"claude: {self.token_env} is not set")
            env["ANTHROPIC_API_KEY"] = value
        elif value:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = value
        elif self.user != "owner":  # the logged-in Claude Code session belongs to whoever runs the server
            raise BrainError(f"claude: {self.token_env} is not set; only the owner may use the logged-in session")
        return env

    def cwd(self) -> Path:
        """out/brain/<user>, created on first use."""
        path = BRAIN_DIR / self.user
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _options(self, task: str, prompt: str, env: dict, images: Sequence[Path],
                 schema: dict) -> tuple[str, ClaudeAgentOptions]:
        cwd = self.cwd()
        if images:
            resolved = [Path(p).resolve() for p in images]  # absolute: the CLI runs in cwd, not the repo
            paths = ", ".join(str(p) for p in resolved)
            prompt = f"{prompt}\n\nRead these image files with the Read tool before answering: {paths}"
            gate = HookMatcher(matcher="Read", hooks=[read_gate(resolved, cwd)], on_failure="block")
            tools = {"tools": ["Read"], "allowed_tools": ["Read"], "max_turns": 4, "hooks": {"PreToolUse": [gate]}}
        else:
            tools = {"tools": [], "max_turns": 2}
        system = f"You are the Cut Sew Club studio brain doing the task '{task}'; answer only with the JSON asked for."
        options = ClaudeAgentOptions(
            model=self.model, cwd=str(cwd), env=env, setting_sources=[], system_prompt=system,
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
            try:
                async with aclosing(query(prompt=prompt, options=options)) as stream:
                    async for m in stream:
                        if isinstance(m, ResultMessage):
                            result = m
            except Exception as e:  # noqa: BLE001 - keep what a failed run was billed (ResultError carries it)
                cost = _cost(result)
                if not cost and isinstance(getattr(e, "data", None), dict):
                    cost = float(e.data.get("total_cost_usd") or 0.0)
                raise BrainError(f"claude: {type(e).__name__}: {e}", cost_usd=cost) from None
            return result

        result = asyncio.run(asyncio.wait_for(ask(), TIMEOUT_S))
        if result is None:
            raise BrainError("claude: no result message")
        if result.is_error:
            raise BrainError(f"claude: {result.subtype}: {(result.result or '')[:200]}", cost_usd=_cost(result))
        return result.structured_output, result.result or "", _cost(result)

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
                total = max(total, e.cost_usd)  # a failed run can still be billed
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
