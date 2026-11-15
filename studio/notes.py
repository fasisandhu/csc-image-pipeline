"""Plain-language versions of compiler warnings for the page."""
from __future__ import annotations

import re

CATALOGUE_NOTE = "Some catalogue details for this style are still being confirmed with the factory."
CAST = re.compile(r"cast (.+?): no approved reference sheet yet")


def plain_warning(text: str) -> str | None:
    """A compiler warning as one plain sentence, or None when it is not meant for the page."""
    from .app import file_names_only  # lazy: app imports this module
    if re.match(r"catalogue [\w-]+: (unconfirmed |.+ missing$)", text):
        return CATALOGUE_NOTE
    if m := CAST.match(text):
        return f"{m.group(1)} has no approved reference photo yet, so the face may vary between images."
    if text.startswith("paragraph prompt is "):
        return "The prompt is a little long; the result may ignore a detail."
    if text.startswith("set style_block differs"):
        return None
    return file_names_only(text)


def plain_warnings(warnings: list[str]) -> list[str]:
    """Every warning in plain words, without repeats or the ones the page does not show."""
    out: list[str] = []
    for w in warnings:
        p = plain_warning(w)
        if p is not None and p not in out:
            out.append(p)
    return out
