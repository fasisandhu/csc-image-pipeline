"""Gemini vision adapter: segmentation masks, object location and text reading for QA.

Coordinates come back normalised to 0-1000 (box_2d = [ymin, xmin, ymax, xmax],
mask = polygon of [x, y]); this adapter converts them to pixels of the input image.
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import errors, types
from PIL import Image

from .base import ProviderError

MODEL = "gemini-3.8-flash"
USD_PER_M_INPUT = 0.75   # through 2026-12-31, then 1.50
USD_PER_M_OUTPUT = 3.75  # through 2026-12-31, then 7.50
EST_COST_USD = 0.005     # pre-call allowance for one image plus a short JSON answer
MAX_SIDE = 1536          # downscale before sending; coordinates are normalised anyway


@dataclass
class VisionResult:
    data: list | dict
    cost_usd: float
    units: dict = field(default_factory=dict)


def _looks_yx(points: list, box_2d: list) -> bool:
    """True if polygon extents fit the box better read as [y, x] than as [x, y]."""
    y0, x0, y1, x1 = box_2d
    xs, ys = [p[0] for p in points], [p[1] for p in points]

    def err(px, py):
        return abs(min(px) - x0) + abs(max(px) - x1) + abs(min(py) - y0) + abs(max(py) - y1)

    return err(ys, xs) < err(xs, ys)


class GeminiVision:
    name = "gemini"
    model = MODEL

    def __init__(self):
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("gemini vision: GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=key)

    def estimate(self) -> dict:
        return {"cost_usd": EST_COST_USD}

    def _ask(self, image: Path, prompt: str) -> tuple[list | dict, float, dict, tuple[int, int]]:
        img = Image.open(image).convert("RGB")
        size = img.size
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )
        try:
            resp = self.client.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), prompt],
                config=config,
            )
        except errors.APIError as e:
            raise ProviderError(f"gemini vision: {e.code} {e.status}: {e.message}") from None
        try:
            data = json.loads(resp.text)
        except (TypeError, ValueError):
            raise ProviderError("gemini vision: response was not JSON") from None
        u = resp.usage_metadata
        inp = (u.prompt_token_count or 0) if u else 0
        out = ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0
        cost = (inp * USD_PER_M_INPUT + out * USD_PER_M_OUTPUT) / 1e6
        return data, cost, {"input_tokens": inp, "output_tokens": out}, size

    @staticmethod
    def _to_px(item: dict, size: tuple[int, int]) -> dict:
        w, h = size
        out = {"label": item.get("label", "")}
        if "box_2d" in item:
            y0, x0, y1, x1 = item["box_2d"]
            out["box"] = [round(x0 * w / 1000), round(y0 * h / 1000), round(x1 * w / 1000), round(y1 * h / 1000)]
        if item.get("mask"):
            pts = item["mask"]
            if "box_2d" in item and _looks_yx(pts, item["box_2d"]):
                pts = [[x, y] for y, x in pts]  # model sometimes answers [y, x] despite the prompt
            out["polygon"] = [[round(x * w / 1000), round(y * h / 1000)] for x, y in pts]
        for k, v in item.items():
            if k not in ("label", "box_2d", "mask"):
                out[k] = v
        return out

    def segment(self, image: Path, target: str) -> VisionResult:
        """Polygon mask(s) for `target`, in pixel coordinates."""
        prompt = (
            f"Give the segmentation mask for {target}. Output a JSON list; each entry has "
            '"label", "box_2d" as [ymin, xmin, ymax, xmax] normalised to 0-1000, and "mask" as a polygon '
            "of [x, y] points normalised to 0-1000 that traces the outline closely."
        )
        data, cost, units, size = self._ask(Path(image), prompt)
        items = data if isinstance(data, list) else [data]
        return VisionResult([self._to_px(i, size) for i in items], cost, units)

    def locate_and_read(self, image: Path, target: str) -> VisionResult:
        """Bounding box of `target` plus the exact text visible inside it."""
        prompt = (
            f"Find {target}. Output a JSON list with one entry per match; each entry has "
            '"label", "box_2d" as [ymin, xmin, ymax, xmax] normalised to 0-1000, and "text": the exact '
            "characters visible inside the box, letter by letter, preserving spelling and case. "
            'Do not correct or guess; use "" if there is no text. Output [] if not found.'
        )
        data, cost, units, size = self._ask(Path(image), prompt)
        items = data if isinstance(data, list) else [data]
        return VisionResult([self._to_px(i, size) for i in items], cost, units)

    def read_all_text(self, image: Path) -> VisionResult:
        """Every piece of visible text anywhere in the image, with its box in pixels."""
        prompt = (
            "Find every piece of visible text anywhere in this image: on the garment, on labels, tags, "
            "stickers, signs and in the background. Output a JSON list with one entry per piece of text; "
            'each entry has "box_2d" as [ymin, xmin, ymax, xmax] normalised to 0-1000 and "text": the exact '
            "characters, letter by letter, preserving spelling and case. Do not correct or guess. "
            "Output [] if there is no text."
        )
        data, cost, units, size = self._ask(Path(image), prompt)
        items = data if isinstance(data, list) else [data]
        out = []
        for i in items:
            if not isinstance(i, dict):  # a stray string or null in the list is not a reading
                continue
            px = self._to_px(i, size)
            out.append({"text": str(i.get("text") or ""), "box": px.get("box")})  # JSON null is no text
        return VisionResult(out, cost, units)
