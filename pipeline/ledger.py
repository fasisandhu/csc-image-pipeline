"""Cost log (out/log.jsonl) and hard budget guards. Every provider call goes through here."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "out" / "log.jsonl"

GEMINI_MONTHLY_CAP_USD = 9.00
GEMINI_SPLIT_USD = {"standard": 4.00, "premium": 6.00}  # soft split, used by the router
CF_DAILY_NEURON_CAP = 9500
RUN_APPROVAL_USD = 1.00  # any run estimated above this needs explicit approval
_LOCK = threading.Lock()  # the studio worker and request threads append at the same time


class BudgetError(RuntimeError):
    """A call would cross a hard cap. Do not retry; ask the user."""


class QueueForTomorrow(BudgetError):
    """Cloudflare daily neuron cap reached; queue until 00:00 UTC."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def entries() -> list[dict]:
    if not LOG.exists():
        return []
    with LOG.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def record(entry: dict) -> dict:
    entry = {"ts": now().isoformat(timespec="seconds"), **entry}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _LOCK:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    return entry


def gemini_month_spend(tier: str | None = None) -> float:
    month = now().strftime("%Y-%m")
    return round(sum(
        e.get("cost_usd", 0.0) for e in entries()
        if e.get("provider") == "gemini" and e["ts"].startswith(month) and (tier is None or e.get("tier") == tier)
    ), 5)


def claude_month_spend(user: str) -> float:
    """This month's billed Claude spend for one user (API-key calls; subscription calls record 0)."""
    month = now().strftime("%Y-%m")
    return round(sum(
        e.get("cost_usd", 0.0) for e in entries()
        if e.get("provider") == "claude" and e.get("user") == user and e["ts"].startswith(month)
    ), 5)


def cf_day_neurons() -> float:
    day = now().strftime("%Y-%m-%d")
    return round(sum(
        e.get("units", {}).get("neurons", 0.0) for e in entries()
        if e.get("provider") == "cloudflare" and e["ts"].startswith(day)
    ), 2)


def check(provider: str, estimate: dict) -> None:
    """Raise before a call that would cross a cap."""
    if provider == "gemini":
        spent = gemini_month_spend()
        if spent + estimate["cost_usd"] > GEMINI_MONTHLY_CAP_USD:
            raise BudgetError(
                f"Gemini cap: ${spent:.2f} spent this month + ${estimate['cost_usd']:.3f} "
                f"would exceed ${GEMINI_MONTHLY_CAP_USD:.2f}"
            )
    elif provider == "cloudflare":
        used = cf_day_neurons()
        if used + estimate["neurons"] > CF_DAILY_NEURON_CAP:
            raise QueueForTomorrow(
                f"Cloudflare: {used:.0f} neurons used today + {estimate['neurons']:.0f} "
                f"would exceed {CF_DAILY_NEURON_CAP}; queue for 00:00 UTC"
            )


def status() -> str:
    return (
        f"Gemini this month: ${gemini_month_spend():.4f} / ${GEMINI_MONTHLY_CAP_USD:.2f} "
        f"(standard ${gemini_month_spend('standard'):.4f}, premium ${gemini_month_spend('premium'):.4f}) | "
        f"Cloudflare today: {cf_day_neurons():.0f} / {CF_DAILY_NEURON_CAP} neurons"
    )


def logged_call(base: dict, estimate: dict, fn):
    """Guard and log a non-image provider call (vision QA). fn() returns an object with cost_usd and units."""
    from .providers import ProviderError

    check(base["provider"], estimate)
    try:
        result = fn()
    except ProviderError as e:
        record({**base, "status": "error", "error": str(e), "cost_usd": 0.0})
        raise
    record({**base, "status": "ok", "cost_usd": round(result.cost_usd, 5), "units": result.units})
    return result


def find(file: str | Path) -> dict | None:
    """The generation entry for an image, following later moves (e.g. to out/reject/)."""
    gen: dict[str, dict] = {}
    for e in entries():
        if e.get("status") == "ok" and e.get("file"):
            gen[Path(e["file"]).name] = e
        elif e.get("event") == "moved" and Path(e["file"]).name in gen:
            src = gen[Path(e["file"]).name]
            gen[Path(e["moved_to"]).name] = {**src, "file": e["moved_to"], "root": src.get("root") or src["file"]}
    return gen.get(Path(file).name)


def events(file: str | Path, kind: str) -> list[dict]:
    name = Path(file).name
    return [e for e in entries() if e.get("event") == kind and Path(e.get("file", "")).name == name]


if __name__ == "__main__":  # python -m pipeline.ledger  -> month and day budget status
    print(status())
