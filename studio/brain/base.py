"""Brain interface: one JSON-answering call for reasoning tasks, logged to out/log.jsonl.

Credential values never leave the adapter: records, errors and reprs carry only the credential
type ("subscription", "api_key" or "gemini").
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from pipeline import ledger

USER_RE = re.compile(r"[a-z0-9_]+")
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.I)


@dataclass
class BrainResult:
    data: dict
    provider: str
    model: str
    credential: str
    seconds: float
    cost_usd: float
    fallback: bool = False


class BrainError(RuntimeError):
    """A brain call failed or was blocked. The message is safe to log: it holds no credential value.

    `cost_usd` is what the failed call still cost (e.g. an error result after billed turns).
    """

    def __init__(self, message: str = "", cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


class Brain(Protocol):
    name: str
    model: str
    credential: str

    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (),
                 user: str = "owner", *, fallback: bool = False) -> BrainResult:
        """Answer `prompt` as a JSON object matching `schema`. `fallback=True` marks a stand-in call."""
        ...


def env_prefix(user: str) -> str:
    """'owner' -> 'STUDIO_OWNER_'."""
    if not USER_RE.fullmatch(user):
        raise ValueError(f"user id must be lower-case letters, digits or _: {user!r}")
    return f"STUDIO_{user.upper()}_"


def scrub(text: str, secrets: Sequence[str] = (), limit: int = 300) -> str:
    """Drop credential values from a message: the given ones, plus any secret-named env value."""
    values = {s for s in secrets if s}
    values |= {v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= 8}
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, "***")
    return text[:limit]


def log_call(base: dict, status: str, seconds: float, cost_usd: float, **extra) -> None:
    ledger.record({**base, "tier": "brain", "seconds": round(seconds, 2), "cost_usd": round(cost_usd, 6),
                   "status": status, **extra})
