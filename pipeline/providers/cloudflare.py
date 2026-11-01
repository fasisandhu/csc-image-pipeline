"""Cloudflare Workers AI adapter (FLUX.2 klein 4B drafts).

Input fields (verified 2026-11-01 against the changelog and /ai/models/schema):
multipart form with prompt, width, height (256-1920), seed, guidance,
input_image_0..3 (each under 512x512). Steps fixed at 4.
"""
from __future__ import annotations

import base64
import io
import math
import os
from pathlib import Path
from typing import Sequence

import requests
from PIL import Image

from .base import GenerateResult, ProviderError, parse_aspect

MODEL = "@cf/black-forest-labs/flux-2-klein-4b"

# Workers AI pricing, Nov 2026: per 512x512 tile.
NEURONS_PER_OUTPUT_TILE = 26.05
NEURONS_PER_INPUT_TILE = 5.37
USD_PER_1K_NEURONS = 0.011  # only charged above the free 10,000/day

SIZE_BOX = {"1K": 1024, "2K": 1920}
MAX_REFS = 4
MAX_REF_SIDE = 511


def _dims(size: str, aspect: str) -> tuple[int, int]:
    box = SIZE_BOX.get(size)
    if box is None:
        raise ProviderError(f"cloudflare: unsupported size {size!r}; use one of {list(SIZE_BOX)}")
    aw, ah = parse_aspect(aspect)
    scale = box / max(aw, ah)
    return int(aw * scale) // 16 * 16, int(ah * scale) // 16 * 16


def _tiles(w: int, h: int) -> int:
    return math.ceil(w / 512) * math.ceil(h / 512)


def _shrink_ref(path: Path) -> bytes:
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_REF_SIDE, MAX_REF_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class CloudflareProvider:
    name = "cloudflare"
    model = MODEL

    def __init__(self, timeout: int = 120):
        self.account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        self.token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not self.account_id or not self.token:
            raise ProviderError("cloudflare: CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
        self.timeout = timeout

    def estimate(self, refs: Sequence[Path], size: str, aspect: str) -> dict:
        w, h = _dims(size, aspect)
        neurons = _tiles(w, h) * NEURONS_PER_OUTPUT_TILE + len(refs) * NEURONS_PER_INPUT_TILE
        return {"neurons": round(neurons, 2), "cost_usd": 0.0, "width": w, "height": h}

    def generate(self, prompt, refs=(), size="1K", aspect="4:5", seed: int | None = None) -> GenerateResult:
        if len(refs) > MAX_REFS:
            raise ProviderError(f"cloudflare: at most {MAX_REFS} reference images")
        est = self.estimate(refs, size, aspect)
        fields = {"prompt": (None, prompt), "width": (None, str(est["width"])), "height": (None, str(est["height"]))}
        if seed is not None:
            fields["seed"] = (None, str(seed))
        for i, ref in enumerate(refs):
            fields[f"input_image_{i}"] = (f"ref{i}.png", _shrink_ref(Path(ref)), "image/png")

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{MODEL}"
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {self.token}"}, files=fields, timeout=self.timeout)
        except requests.RequestException as e:
            raise ProviderError(f"cloudflare: request failed ({type(e).__name__})") from None
        try:
            body = r.json()
        except ValueError:
            raise ProviderError(f"cloudflare: HTTP {r.status_code}, non-JSON response") from None
        if r.status_code != 200 or not body.get("success", False):
            errors = [e.get("message", "") for e in body.get("errors", [])]
            raise ProviderError(f"cloudflare: HTTP {r.status_code}: {'; '.join(errors) or 'unknown error'}")

        data = base64.b64decode(body["result"]["image"])
        img = Image.open(io.BytesIO(data))
        mime = Image.MIME.get(img.format, "application/octet-stream")
        return GenerateResult(
            image=data,
            mime_type=mime,
            width=img.width,
            height=img.height,
            provider=self.name,
            model=MODEL,
            cost_usd=0.0,
            cost_basis="estimate",
            units={"neurons": est["neurons"]},
            meta={"seed": seed, "refs": len(refs)},
        )
