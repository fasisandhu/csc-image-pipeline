"""Shared adapter interface. Every provider implements generate(prompt, refs, size, aspect)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


class ProviderError(RuntimeError):
    """A provider call failed. Message must never contain keys or account IDs."""


@dataclass
class GenerateResult:
    image: bytes
    mime_type: str
    width: int
    height: int
    provider: str
    model: str
    cost_usd: float
    cost_basis: str  # "usage" (from provider response) or "estimate"
    units: dict = field(default_factory=dict)  # tokens or neurons, provider-specific
    meta: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str

    def estimate(self, refs: Sequence[Path], size: str, aspect: str) -> dict:
        """Pre-call cost estimate: {"cost_usd": float, ...native units}."""
        ...

    def generate(
        self, prompt: str, refs: Sequence[Path] = (), size: str = "1K", aspect: str = "4:5"
    ) -> GenerateResult:
        ...


def parse_aspect(aspect: str) -> tuple[int, int]:
    w, h = aspect.split(":")
    return int(w), int(h)
