"""Pick a provider tier per call from the shot tier and remaining budget.

Rules (CLAUDE.md, Budget rules):
- drafts go to the draft tier; past 9,500 neurons today they queue for 00:00 UTC
- standard finals use the standard split; when it is used up they fall back to premium
  if premium's split has room, otherwise ask
- premium finals never downgrade; past their split they ask
- nothing crosses the $9.00 monthly cap; a plan above $1.00 needs approval

    python -m pipeline.router specs/<shot>.json [...] [--drafts N]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from . import ledger
from .compile import Compiled, compile_file
from .providers import get_provider

DRAFT_SIZE = "1K"


@dataclass
class Route:
    shot_id: str
    phase: str  # "draft" or "final"
    requested_tier: str
    tier: str
    provider: str
    model: str
    size: str
    aspect: str
    estimate: dict
    action: str  # "run", "queue" or "ask"
    reason: str = ""


@dataclass
class Pending:
    """Estimated spend of calls already planned in this run but not yet logged."""
    usd: dict = field(default_factory=lambda: {"standard": 0.0, "premium": 0.0})
    neurons: float = 0.0

    def add(self, r: Route) -> None:
        if r.action != "run":
            return
        if r.phase == "draft":
            self.neurons += r.estimate.get("neurons", 0.0)
        else:
            self.usd[r.tier] += r.estimate["cost_usd"]


def _make(c: Compiled, phase: str, tier: str, size: str, action: str, reason: str, requested: str) -> Route:
    p = get_provider(tier)
    refs = [r["path"] for r in c.refs][: p.max_refs]
    return Route(c.shot_id, phase, requested, tier, p.name, p.model, size, c.aspect,
                 p.estimate(refs, size, c.aspect), action, reason)


def route(c: Compiled, phase: str = "final", pending: Pending | None = None) -> Route:
    pending = pending or Pending()
    if phase == "draft" or c.tier == "draft":
        r = _make(c, "draft", "draft", DRAFT_SIZE, "run", "", c.tier)
        used = ledger.cf_day_neurons() + pending.neurons
        if used + r.estimate["neurons"] > ledger.CF_DAILY_NEURON_CAP:
            r.action, r.reason = "queue", f"{used:.0f} neurons used today; queue for 00:00 UTC"
        return r

    total_room = ledger.GEMINI_MONTHLY_CAP_USD - ledger.gemini_month_spend() - sum(pending.usd.values())

    def split_room(tier: str) -> float:
        return ledger.GEMINI_SPLIT_USD[tier] - ledger.gemini_month_spend(tier) - pending.usd[tier]

    want = _make(c, "final", c.tier, c.size, "run", "", c.tier)
    cost = want.estimate["cost_usd"]
    if cost <= split_room(c.tier) and cost <= total_room:
        return want
    if c.tier == "standard":
        alt = _make(c, "final", "premium", c.size, "run", "standard split used; fell back to premium", c.tier)
        if alt.estimate["cost_usd"] <= min(split_room("premium"), total_room):
            return alt
    if cost <= total_room:
        want.action, want.reason = "ask", f"{c.tier} split of ${ledger.GEMINI_SPLIT_USD[c.tier]:.2f} used up; approve to exceed it"
    else:
        want.action, want.reason = "ask", f"would cross the ${ledger.GEMINI_MONTHLY_CAP_USD:.2f} monthly cap"
    return want


def plan(compiled: list[Compiled], drafts_per_shot: int = 0) -> tuple[list[Route], dict]:
    """Route every call of a run in order, counting earlier planned calls against the budget."""
    pending, routes = Pending(), []
    for c in compiled:
        phases = ["draft"] if c.tier == "draft" else ["draft"] * drafts_per_shot + ["final"]
        for phase in phases:
            r = route(c, phase, pending)
            pending.add(r)
            routes.append(r)
    total_usd = round(sum(r.estimate.get("cost_usd", 0.0) for r in routes if r.action == "run"), 4)
    summary = {
        "total_usd": total_usd,
        "total_neurons": round(pending.neurons, 1),
        "needs_approval": total_usd > ledger.RUN_APPROVAL_USD or any(r.action == "ask" for r in routes),
        "queued": sum(r.action == "queue" for r in routes),
    }
    return routes, summary


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ledger.ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--drafts", type=int, default=0, help="Cloudflare drafts per final shot")
    args = ap.parse_args()
    routes, summary = plan([compile_file(p) for p in args.specs], args.drafts)
    for r in routes:
        cost = f"~{r.estimate['neurons']:.0f} neurons" if r.phase == "draft" else f"~${r.estimate['cost_usd']:.3f}"
        print(f"{r.action:5} {r.shot_id:32} {r.phase:5} {r.tier:8} {r.model:38} {r.size} {r.aspect}  {cost}  {r.reason}")
    print(f"\nPlan: ${summary['total_usd']:.3f}, ~{summary['total_neurons']:.0f} neurons, "
          f"{summary['queued']} queued, needs approval: {summary['needs_approval']}")
    print(ledger.status())


if __name__ == "__main__":
    main()
