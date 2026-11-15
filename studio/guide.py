"""Guide pages for teammates. Markdown with {{key}} placeholders filled from the live rule files,
so the guide can never drift from the numbers the pipeline actually enforces."""
from __future__ import annotations

import re
from pathlib import Path

from pipeline import finish, ledger
from pipeline.compile import load_json

from .worker import COLOUR_CORRECTIONS_PER_RENDER, DRAFTS

DIR = Path(__file__).resolve().parent / "guide"
# the only pages, in the order the guide lists them; nothing else is ever read from disk
PAGES = ("start-here", "make-a-website-image", "review-and-approve", "read-the-checks", "rules")


def _num(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else str(x)


def _listed(items) -> str:
    """(1600, 800, 400) -> "1600, 800 and 400"."""
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def values() -> dict[str, str]:
    qa, lim = load_json("brand/qa_checklist.json"), load_json("brand/repairs.json")["limits"]
    return {
        "colour.tolerance": _num(qa["colour"]["tolerance"]),
        "colour.drift_max": _num(qa["colour"]["drift_max"]),
        "logo.size_low_pct": _num(round(qa["logo"]["size_low"] * 100)),
        "logo.size_high_pct": _num(round(qa["logo"]["size_high"] * 100)),
        "limits.repairs_per_render": _num(lim["repairs_per_render"]),
        "limits.regenerations_per_shot": _num(lim["regenerations_per_shot"]),
        "limits.colour_corrections_per_render": _num(COLOUR_CORRECTIONS_PER_RENDER),  # free; the chain's own cap
        "budget.cap_usd": f"{ledger.GEMINI_MONTHLY_CAP_USD:.2f}",
        "budget.ask_over_usd": f"{ledger.RUN_APPROVAL_USD:.2f}",
        "sketches.count": _num(DRAFTS),
        "export.web_sizes": _num(len(finish.WEBP_WIDTHS)),  # the web export's widths, as finish.export makes them
        "export.web_widths": _listed(finish.WEBP_WIDTHS),
    }


def _read(page: str) -> str:
    return (DIR / f"{page}.md").read_text(encoding="utf-8")


def render(page: str) -> str:
    """The page with every {{key}} filled. KeyError for an unknown page or an unknown key."""
    if page not in PAGES:
        raise KeyError(f"unknown guide page: {page}")
    vals = values()
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", lambda m: vals[m.group(1)], _read(page))
