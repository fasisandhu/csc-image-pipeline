"""Brain adapter: Claude on each user's own credential, with Gemini as the fallback.

    brain = get_brain("owner", fast=True)
    result = brain.ask_json("read_brief", prompt, schema, user="owner")   # -> BrainResult

Quick tasks (fast=True) go to a warm Claude session per user (warm.WarmClaude) with a 20 s timeout
(warm.FAST_TIMEOUT_S), so the Gemini fallback still answers inside the page's 30 s; other tasks open one
Claude query per call (ClaudeBrain) with claude.TIMEOUT_S.

Env (names in .env.example): STUDIO_<USER>_CLAUDE = subscription | api_key | none (unset = none),
STUDIO_<USER>_CLAUDE_OAUTH_TOKEN (blank = the owner's logged-in Claude Code session),
STUDIO_<USER>_ANTHROPIC_API_KEY, STUDIO_<USER>_CLAUDE_CAP_USD (default 5), STUDIO_FAST_TASKS = claude | gemini.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from .base import Brain, BrainError, BrainResult, env_prefix
from .claude import ClaudeBrain
from . import warm
from .gemini import GeminiBrain
from .warm import WarmClaude

__all__ = ["Brain", "BrainError", "BrainResult", "ClaudeBrain", "FallbackBrain", "GeminiBrain", "WarmClaude",
           "credential_for", "get_brain"]


class FallbackBrain:
    """Ask `primary`; on a BrainError ask `fallback`, whose call is recorded with fallback=True."""

    def __init__(self, primary: Brain, fallback: Brain):
        self.primary, self.fallback = primary, fallback
        self.name = f"{primary.name}+{fallback.name}"
        self.model, self.credential = primary.model, primary.credential

    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (),
                 user: str = "owner", *, fallback: bool = False) -> BrainResult:
        try:
            return self.primary.ask_json(task, prompt, schema, images, user=user, fallback=fallback)
        except BrainError as first:
            try:
                return self.fallback.ask_json(task, prompt, schema, images, user=user, fallback=True)
            except BrainError as second:
                raise BrainError(f"{first} | then {second}") from None


def credential_for(user: str) -> tuple[str, str | None]:
    """("subscription" | "api_key" | "none", name of the env var holding the credential, or None)."""
    prefix = env_prefix(user)
    kind = (os.environ.get(prefix + "CLAUDE") or "none").strip().lower()
    if kind == "subscription":
        return "subscription", prefix + "CLAUDE_OAUTH_TOKEN"
    if kind == "api_key":
        return "api_key", prefix + "ANTHROPIC_API_KEY"
    if kind == "none":
        return "none", None
    raise ValueError(f"{prefix}CLAUDE must be subscription, api_key or none")  # value not echoed: may be a secret


def get_brain(user: str, fast: bool = False) -> Brain:
    """Claude with Gemini fallback, or Gemini alone when the user has no Claude or fast tasks go to Gemini.

    Fast tasks with STUDIO_FAST_TASKS=claude (the default) use the user's warm Claude session.
    """
    kind, token_env = credential_for(user)
    fast_mode = (os.environ.get("STUDIO_FAST_TASKS") or "claude").strip().lower()
    if kind == "none" or (fast and fast_mode == "gemini"):
        return GeminiBrain()
    if fast and fast_mode == "claude":
        return FallbackBrain(WarmClaude(user, kind, token_env, timeout_s=warm.FAST_TIMEOUT_S), GeminiBrain())
    return FallbackBrain(ClaudeBrain(user, kind, token_env), GeminiBrain())
