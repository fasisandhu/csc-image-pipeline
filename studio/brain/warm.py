"""Warm Claude: one open ClaudeSDKClient per user and task schema, for quick structured tasks.

Opening a Claude session takes seconds; an open one answers a short structured task in one or two
(spikes/claude_sdk/FINDINGS.md, S6). WarmClaude is ClaudeBrain with a session that stays open: the same
per-user env, cap check, cost split, scrubbing and brain records (ClaudeBrain._answer), the same slim options
(ClaudeBrain._options), and the same BrainError on any failure, so FallbackBrain answers instead.

- One asyncio loop thread per process (`_pool`) owns every client. ask_json is synchronous and may be
  called from several threads at once; calls on one client wait their turn (one asyncio.Lock per client).
- A client opens on first use and is reused. After every answer the conversation is cleared (`/clear`), so
  no brief is read after an earlier one and each call's total_cost_usd (a running total for the session)
  is its own; a clear the CLI does not confirm closes the client instead.
- A client closes after `idle_s` without use, on any error or timeout of a call (no retry), and at
  shutdown(), which `python -m studio` calls when the server stops.
- Prompts are sent as client-composed messages: Claude Code delivers them as written, with no `@path`
  file expansion (a brief typing `@../../.env` would otherwise attach that file) and no slash command.
  Only the pool's own `/clear` is sent as a command (so the session leaves ClaudeBrain's verbatim_prompts
  off: that option has no per-message opt-out).
- `read_brief` runs at low effort: a closed-choice mapping whose answer tasks.read_brief re-validates
  against the catalogue, where Claude's adaptive thinking only added time. Other tasks keep the default.
- Text only: a call with images goes to ClaudeBrain, whose one-off query reads them behind the Read gate.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import dataclasses
import hashlib
import json
import threading
from pathlib import Path
from typing import AsyncIterator, Coroutine, Sequence

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ConversationResetMessage, ResultMessage

from . import claude
from .base import BrainError, BrainResult
from .claude import DEFAULT_MODEL, ClaudeBrain

IDLE_S = 600
# Quick tasks (get_brain(fast=True)): the page gives up on "Fill from my words" after 30 s, so a hung Claude must
# fail by 20 s, leaving the Gemini fallback time to answer inside it. Other tasks keep claude.TIMEOUT_S.
FAST_TIMEOUT_S = 20
CLEAR_TIMEOUT_S = 15
WAIT_GRACE_S = 5  # a calling thread waits the call's timeout plus this for the loop to answer
SHUTDOWN_WAIT_S = 10  # per round, for calls and clean-ups to finish once their clients are closed
LOW_EFFORT_TASKS = frozenset({"read_brief"})


async def _as_written(prompt: str) -> AsyncIterator[dict]:
    """`prompt` as one client-composed user message: no @path expansion, no slash-command dispatch."""
    yield {"type": "user", "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None,
           "client_composed": True}


class _Session:
    """One client slot, touched only on the loop thread. Whoever holds `lock` may use or replace `client`."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.client: ClaudeSDKClient | None = None
        self.env: dict | None = None  # what the client was opened with (holds the credential; never shown)
        self.uses = 0  # bumped by every call; an idle timer closes the client only if no call came since
        self.idle_timer: asyncio.TimerHandle | None = None

    def __repr__(self) -> str:
        return f"_Session(open={self.client is not None}, uses={self.uses})"

    async def close(self) -> None:
        client, self.client, self.env = self.client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - a broken client is gone either way
                pass


class _Pool:
    """The process's loop thread and every warm client on it, keyed by (user, task, model, schema hash)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()  # loop start, call submission and shutdown
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._sessions: dict[tuple, _Session] = {}  # loop thread only
        self._tasks: set[asyncio.Task] = set()  # loop thread only: background clean-ups in flight

    def ask(self, key: tuple, prompt: str, options: ClaudeAgentOptions, idle_s: float,
            timeout_s: float) -> tuple[dict | None, str, float]:
        """(structured_output, result text, cost) from the client for `key`. Blocks the calling thread: the loop
        gives up after `timeout_s`, and the thread itself (a stuck loop) after `timeout_s` plus WAIT_GRACE_S."""
        with self._guard:
            if self._closing:
                raise BrainError("claude: the studio is shutting down")
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._loop.run_forever, name="warm-claude", daemon=True)
                self._thread.start()
            call = asyncio.run_coroutine_threadsafe(self._serve(key, prompt, options, idle_s, timeout_s), self._loop)
        try:
            return call.result(timeout=timeout_s + WAIT_GRACE_S)
        except concurrent.futures.CancelledError:
            raise BrainError("claude: the warm session closed before answering") from None
        finally:
            call.cancel()  # no-op once answered; otherwise the loop stops this call and closes its client

    async def _serve(self, key: tuple, prompt: str, options: ClaudeAgentOptions, idle_s: float,
                     timeout_s: float) -> tuple[dict | None, str, float]:
        """On the loop: wait for the client, ask, answer. The lock passes to `_finish`, which releases it."""
        s = self._sessions.get(key)
        if s is None:
            s = self._sessions[key] = _Session()
        held = False
        try:
            async with asyncio.timeout(timeout_s):  # the wait for the client counts too
                await s.lock.acquire()  # a timeout while waiting leaves the client to its current user
                held = True
                s.uses += 1
                if s.idle_timer is not None:
                    s.idle_timer.cancel()
                answer = await self._query(s, prompt, options)
        except BaseException:
            if held:  # error, timeout or cancel while using the client: close it, never retry
                self._spawn(self._finish(s, idle_s, clear=False))
            raise
        self._spawn(self._finish(s, idle_s, clear=True))
        return answer

    async def _query(self, s: _Session, prompt: str, options: ClaudeAgentOptions) -> tuple[dict | None, str, float]:
        """One question on `s`'s client, opened first if needed. Each query starts a fresh conversation (a new
        client or a confirmed /clear), so the result's running total_cost_usd is this call's cost."""
        if self._closing:
            raise BrainError("claude: the studio is shutting down")
        if s.client is not None and s.env != options.env:  # the user's credential changed
            await s.close()
        if s.client is None:
            client = ClaudeSDKClient(options=options)
            await client.connect()  # disconnects itself if it fails
            s.client, s.env = client, options.env
            if self._closing:
                raise BrainError("claude: the studio is shutting down")
        result = None
        try:
            await s.client.query(_as_written(prompt))
            async for m in s.client.receive_response():
                if isinstance(m, ResultMessage):
                    result = m
        except Exception as e:  # noqa: BLE001 - keep what a failed run was billed (ResultError carries it)
            raise claude._failure(e, claude._cost(result)) from None
        return claude._outcome(result)

    async def _finish(self, s: _Session, idle_s: float, clear: bool) -> None:
        """After a call, still holding its lock: clear the conversation (or close the client after a failure,
        or when the CLI does not confirm the clear), start the idle clock, release the lock."""
        try:
            if clear and s.client is not None:
                try:
                    async with asyncio.timeout(CLEAR_TIMEOUT_S):
                        reset = await self._clear(s.client)
                except Exception:  # noqa: BLE001 - any failure: the client goes
                    reset = False
                if not reset:
                    await s.close()
            else:
                await s.close()
        finally:
            s.lock.release()  # first: nothing below may keep the client locked
            if s.client is not None and not self._closing:  # waiters run later: they cancel this timer
                uses = s.uses
                s.idle_timer = asyncio.get_running_loop().call_later(
                    idle_s, lambda: self._spawn(self._reap(s, uses)))

    @staticmethod
    async def _clear(client: ClaudeSDKClient) -> bool:
        """Start a fresh conversation (history and running cost total); True once the CLI confirms it."""
        await client.query("/clear")
        reset = False
        async for m in client.receive_response():
            reset = reset or isinstance(m, ConversationResetMessage)
        return reset

    async def _reap(self, s: _Session, uses: int) -> None:
        """The idle timer: close the client unless a call came since the timer started."""
        async with s.lock:  # waits for a running call, which then restarts the clock
            if s.uses == uses:
                await s.close()

    def _spawn(self, coro: Coroutine) -> None:
        """A background task on the loop; asyncio keeps only weak references, so hold one until it ends."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def shutdown(self, timeout: float = 60) -> None:
        """Close every client and stop the loop thread. Idempotent; a later call starts afresh."""
        with self._guard:
            loop, thread = self._loop, self._thread
            if loop is None or self._closing:
                return
            self._closing = True
        try:
            asyncio.run_coroutine_threadsafe(self._close_all(), loop).result(timeout)
        except Exception:  # noqa: BLE001 - stop the loop whatever happened
            pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout)
            if not thread.is_alive():
                loop.close()
            with self._guard:
                self._loop = self._thread = None
                self._sessions = {}
                self._closing = False

    async def _close_all(self) -> None:
        """On the loop: close every client (calls on them then fail), wait for the calls and clean-ups to end,
        cancel what is left, and close anything opened meanwhile."""
        me = asyncio.current_task()
        for _ in range(3):
            for s in self._sessions.values():
                if s.idle_timer is not None:
                    s.idle_timer.cancel()
                await s.close()
            rest = [t for t in asyncio.all_tasks() if t is not me]
            if not rest:
                return
            _, pending = await asyncio.wait(rest, timeout=SHUTDOWN_WAIT_S)
            for t in pending:
                t.cancel()


_pool = _Pool()


def shutdown() -> None:
    """Close every warm client (and its Claude process) and stop the loop thread."""
    _pool.shutdown()


atexit.register(shutdown)  # a script that never calls shutdown() leaves no Claude process behind


class WarmClaude(ClaudeBrain):
    """ClaudeBrain whose text-only calls go to a warm session (see the module docstring)."""

    def __init__(self, user: str, credential: str, token_env: str | None, model: str = DEFAULT_MODEL,
                 idle_s: float = IDLE_S, timeout_s: float | None = None):
        super().__init__(user, credential, token_env, model, timeout_s)
        self.idle_s = idle_s

    def __repr__(self) -> str:
        return (f"WarmClaude(user={self.user!r}, credential={self.credential!r}, model={self.model!r}, "
                f"idle_s={self.idle_s!r}, timeout_s={self.timeout_s!r})")

    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (),
                 user: str = "owner", *, fallback: bool = False) -> BrainResult:
        """Synchronous and thread-safe; never call from inside a running event loop."""
        if images:  # a warm session has no tools; ClaudeBrain's one-off query reads images behind the Read gate
            return super().ask_json(task, prompt, schema, images, user, fallback=fallback)
        self._check_caller(user)
        return self._answer(task, fallback, lambda env: self._run_warm(task, prompt, env, schema))

    def _options(self, task: str, prompt: str, env: dict, images: Sequence[Path],
                 schema: dict) -> tuple[str, ClaudeAgentOptions]:
        prompt, options = super()._options(task, prompt, env, images, schema)
        if task in LOW_EFFORT_TASKS:
            options = dataclasses.replace(options, effort="low")
        return prompt, options

    def _run_warm(self, task: str, prompt: str, env: dict, schema: dict) -> tuple[dict | None, str, float]:
        prompt, options = self._options(task, prompt, env, (), schema)
        # each prompt is protected per message (_as_written) instead, so the session's own /clear still runs
        options = dataclasses.replace(options, verbatim_prompts=False)
        digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        # the task is in the key as well: the client's system prompt names it
        return _pool.ask((self.user, task, self.model, digest), prompt, options, self.idle_s, self.timeout())
