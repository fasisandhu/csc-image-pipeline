"""Sample design lookups (brand/designs/designs.json) and their raster paths."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def design_for(artwork: str) -> dict | None:
    path = ROOT / "brand" / "designs" / "designs.json"
    if not path.exists():
        return None
    designs = json.loads(path.read_text(encoding="utf-8"))["designs"]
    return next((d for d in designs if d["file"] == Path(artwork).as_posix() or d["id"] == artwork), None)


def rgba_png(artwork: str) -> str:
    """Transparent raster of the artwork: ground truth for compositing and outline checks."""
    p = Path(artwork)
    return p.as_posix() if p.suffix.lower() == ".png" else (Path("brand/designs/png") / f"{p.stem}.png").as_posix()


def ref_png(artwork: str) -> str:
    """Artwork flattened on a contrasting grey, used as a generator reference image (no SVG input)."""
    p = Path(artwork)
    return p.as_posix() if p.suffix.lower() == ".png" else (Path("brand/designs/png") / f"{p.stem}_ref.png").as_posix()
