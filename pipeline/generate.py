"""Guarded, logged provider calls. Spec compilation and routing arrive in build step 3."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from . import ledger
from .providers import GenerateResult, ProviderError, get_provider

EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def run(
    tier: str,
    prompt: str,
    refs: Sequence[Path] = (),
    size: str = "1K",
    aspect: str = "4:5",
    tag: str = "image",
    out_dir: str | None = None,
) -> tuple[Path, GenerateResult]:
    """Check budget, call the provider once (no retries), save the image, log the call."""
    provider = get_provider(tier)
    refs = [Path(r) for r in refs]
    estimate = provider.estimate(refs, size, aspect)
    ledger.check(provider.name, estimate)

    base = {
        "tag": tag, "tier": tier, "provider": provider.name, "model": provider.model,
        "size": size, "aspect": aspect, "refs": [r.as_posix() for r in refs],
        "prompt": prompt, "estimate": estimate,
    }
    try:
        result = provider.generate(prompt, refs, size, aspect)
    except ProviderError as e:
        ledger.record({**base, "status": "error", "error": str(e), "cost_usd": 0.0})
        raise

    folder = ledger.ROOT / "out" / (out_dir or ("drafts" if tier == "draft" else "finals"))
    folder.mkdir(parents=True, exist_ok=True)
    stamp = ledger.now().strftime("%Y%m%dT%H%M%S")
    path = folder / f"{stamp}_{tag}{EXT.get(result.mime_type, '.png')}"
    path.write_bytes(result.image)

    ledger.record({
        **base, "status": "ok", "file": path.relative_to(ledger.ROOT).as_posix(),
        "width": result.width, "height": result.height,
        "cost_usd": result.cost_usd, "cost_basis": result.cost_basis, "units": result.units, "meta": result.meta,
    })
    return path, result
