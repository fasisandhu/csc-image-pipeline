"""Gemini brain: the fallback (and the brain for users without Claude). Counts toward the $9 Gemini cap."""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Sequence

from google import genai
from google.genai import types
from PIL import Image

from pipeline import ledger
from pipeline.providers.vision import MAX_SIDE, MODEL, USD_PER_M_INPUT, USD_PER_M_OUTPUT

from .base import BrainError, BrainResult, log_call, scrub

EST_COST_USD = 0.002  # pre-call allowance for a short JSON answer


def _image_part(path: Path) -> types.Part:
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


class GeminiBrain:
    name = "gemini"
    model = MODEL
    credential = "gemini"

    def __init__(self, client=None):
        self._client = client  # created on first use, so a fallback that never runs needs no key

    def _get_client(self):
        if self._client is None:
            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise BrainError("gemini: GEMINI_API_KEY not set")
            self._client = genai.Client(api_key=key)
        return self._client

    def ask_json(self, task: str, prompt: str, schema: dict, images: Sequence[Path] = (),
                 user: str = "owner", *, fallback: bool = False) -> BrainResult:
        base = {"event": "brain", "user": user, "task": task, "provider": "gemini", "model": MODEL,
                "credential": "gemini", "fallback": fallback}
        try:
            ledger.check("gemini", {"cost_usd": EST_COST_USD})
        except ledger.BudgetError as e:
            msg = f"gemini: {e}"
            log_call(base, "blocked", 0.0, 0.0, error=msg)
            raise BrainError(msg) from None

        t0 = time.perf_counter()
        cost, units = 0.0, {}
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            )
            contents = [_image_part(Path(p)) for p in images] + [prompt]
            resp = self._get_client().models.generate_content(model=MODEL, contents=contents, config=config)
            u = resp.usage_metadata
            inp = (u.prompt_token_count or 0) if u else 0
            out = ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0
            cost = (inp * USD_PER_M_INPUT + out * USD_PER_M_OUTPUT) / 1e6
            units = {"input_tokens": inp, "output_tokens": out}
            try:
                data = json.loads(resp.text)
            except (TypeError, ValueError):
                raise BrainError("gemini: answer was not JSON") from None
            if not isinstance(data, dict):
                raise BrainError("gemini: answer was not a JSON object")
        except Exception as e:  # noqa: BLE001 - any failure is logged and becomes a BrainError
            msg = scrub(str(e) if isinstance(e, BrainError) else f"gemini: {type(e).__name__}: {e}")
            log_call(base, "error", time.perf_counter() - t0, cost, error=msg, units=units)
            raise BrainError(msg) from None

        seconds = time.perf_counter() - t0
        log_call(base, "ok", seconds, cost, units=units)
        return BrainResult(data, "gemini", MODEL, "gemini", round(seconds, 2), round(cost, 6), fallback)
