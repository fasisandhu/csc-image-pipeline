"""Gemini API adapter (Nano Banana 2 standard finals, Nano Banana Pro premium finals).

Model IDs verified via models.list on 2026-11-01.
"""
from __future__ import annotations

import io
import mimetypes
import os
from pathlib import Path
from typing import Sequence

from google import genai
from google.genai import errors, types
from PIL import Image

from .base import GenerateResult, ProviderError

# Paid-tier prices per 1M tokens and output image tokens per size, Nov 2026.
MODELS = {
    "standard": {
        "id": "gemini-3.1-flash-image",
        "input": 0.50, "text_out": 3.00, "image_out": 60.00,
        "image_tokens": {"512": 747, "1K": 1120, "2K": 1680, "4K": 2520},
    },
    "premium": {
        "id": "gemini-3-pro-image",
        "input": 2.00, "text_out": 12.00, "image_out": 120.00,
        "image_tokens": {"1K": 1120, "2K": 1120, "4K": 2000},
    },
}
REF_IMAGE_TOKENS = 560  # approximate input tokens per reference image
PROMPT_TOKENS_EST = 400  # generous allowance for a 220-word prompt plus thinking overhead


MAX_REFS = 14


class GeminiProvider:
    name = "gemini"
    prompt_format = "paragraph"
    max_refs = MAX_REFS

    def __init__(self, tier: str = "standard"):
        if tier not in MODELS:
            raise ProviderError(f"gemini: unknown tier {tier!r}")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("gemini: GEMINI_API_KEY not set")
        self.tier = tier
        self.spec = MODELS[tier]
        self.model = self.spec["id"]
        self.client = genai.Client(api_key=key)

    def estimate(self, refs: Sequence[Path], size: str, aspect: str) -> dict:
        if size not in self.spec["image_tokens"]:
            raise ProviderError(f"gemini: {self.model} does not offer size {size!r}")
        s = self.spec
        input_tokens = PROMPT_TOKENS_EST + len(refs) * REF_IMAGE_TOKENS
        cost = s["image_tokens"][size] * s["image_out"] / 1e6 + input_tokens * s["input"] / 1e6
        return {"cost_usd": round(cost, 4)}

    def _actual_cost(self, usage) -> tuple[float, dict] | None:
        if usage is None or usage.prompt_token_count is None:
            return None
        s = self.spec
        image_out = sum(
            d.token_count or 0
            for d in (usage.candidates_tokens_details or [])
            if d.modality == types.MediaModality.IMAGE
        )
        text_out = (usage.candidates_token_count or 0) - image_out + (usage.thoughts_token_count or 0)
        units = {"input_tokens": usage.prompt_token_count, "image_out_tokens": image_out, "text_out_tokens": text_out}
        cost = (
            usage.prompt_token_count * s["input"] + image_out * s["image_out"] + text_out * s["text_out"]
        ) / 1e6
        return cost, units

    def generate(self, prompt, refs=(), size="2K", aspect="4:5") -> GenerateResult:
        self.estimate(refs, size, aspect)  # validates size
        contents: list = [prompt]
        for ref in refs:
            ref = Path(ref)
            mime = mimetypes.guess_type(ref.name)[0] or "image/png"
            contents.append(types.Part.from_bytes(data=ref.read_bytes(), mime_type=mime))
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect, image_size=size),
        )
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        except errors.APIError as e:
            raise ProviderError(f"gemini: {e.code} {e.status}: {e.message}") from None

        parts = (resp.candidates[0].content.parts or []) if resp.candidates and resp.candidates[0].content else []
        images = [p for p in parts if p.inline_data and p.inline_data.data and not p.thought]
        if not images:
            reason = resp.candidates[0].finish_reason if resp.candidates else getattr(resp, "prompt_feedback", None)
            raise ProviderError(f"gemini: no image returned (reason: {reason})")
        blob = images[-1].inline_data

        actual = self._actual_cost(resp.usage_metadata)
        if actual:
            cost, units, basis = *actual, "usage"
        else:
            cost, units, basis = self.estimate(refs, size, aspect)["cost_usd"], {}, "estimate"

        img = Image.open(io.BytesIO(blob.data))
        return GenerateResult(
            image=blob.data,
            mime_type=blob.mime_type or Image.MIME.get(img.format, "image/png"),
            width=img.width,
            height=img.height,
            provider=self.name,
            model=self.model,
            cost_usd=round(cost, 5),
            cost_basis=basis,
            units=units,
            meta={"tier": self.tier, "size": size, "aspect": aspect, "refs": len(refs)},
        )
