"""One-change repair edits, within the CLAUDE.md limits (two repairs per render, then regenerate).

    python -m pipeline.repair <image> --reason drawcord_shape
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ledger
from .compile import load_json
from .designs import design_for, ref_png
from .generate import run
from .logo import ASSUMED_CHEST_WIDTH_CM
from .providers import GenerateResult, ProviderError
from .qa import rel, spec_path

KEEP = ("Keep everything else exactly as it is: garment shape, colour and construction, model, pose, "
        "lighting, background and framing.")


class NotARender(ValueError):
    """The image is not in out/log.jsonl with a shot_id. Nothing was spent."""


class RepairLimitReached(RuntimeError):
    """The render already has repairs_per_render repairs; regenerate instead. Nothing was spent."""


def repair_prompt(code: str, spec: dict) -> tuple[str, list[str]]:
    """Prompt and extra reference images (after Image 1, the render being repaired).

    A shot whose design the model renders (generate_and_verify) uses a code's `instruction_decorated` when it has
    one, so removing stray text keeps the intended design."""
    rule = load_json("brand/repairs.json")["codes"][code]
    if rule["fix"] != "repair":
        raise SystemExit(f"error: {code} is fixed by {rule['fix']}, not a repair edit")
    shot = spec["shot"]
    cat = load_json(Path("catalogue") / f"{shot['garment']['catalogue']}.json")
    fill = {"shoulder": cat.get("shoulder", "")}
    refs: list[str] = []
    template, decorated = rule["instruction"], False
    deco = next((d for d in shot.get("decoration", []) if d.get("render_mode") == "generate_and_verify"), None)
    if deco is not None:
        design = design_for(deco["artwork"]) or {}
        fill.update(placement=deco["placement"], technique=deco["type"], text=design.get("text", ""))
        if "instruction_decorated" in rule:
            template, decorated = rule["instruction_decorated"], True
    if code.startswith("logo_"):
        if deco is None:
            raise SystemExit(f"error: {code} needs a decoration the model renders; this shot has none")
        chest = cat.get("measurements_cm", {}).get("M", {}).get("chest_width")
        chest = float(chest) if isinstance(chest, (int, float)) else ASSUMED_CHEST_WIDTH_CM
        fill.update(size_cm=deco["size_cm"], torso_share=round(100 * deco["size_cm"] / chest))
        refs.append(ref_png(deco["artwork"]))
    change = template.format(**fill)
    if decorated:  # a design without text would leave a double space
        change = " ".join(change.split())
    return f"Edit Image 1. Change only {change}. {KEEP}", refs


def repairs_made(root: str) -> list[dict]:
    """Logged repairs of one render (its root file), however they were made: CLI or studio."""
    return [e for e in ledger.entries() if e.get("status") == "ok" and e.get("phase") == "repair" and e.get("root") == root]


def repair_image(image: Path, code: str, tier: str | None = None) -> tuple[Path, GenerateResult]:
    """One repair edit of a logged render for reason `code`, within repairs_per_render. One call, no retry.

    tier: the tier to run on, when a router fell back from the render's own (default: the render's tier).
    Raises NotARender or RepairLimitReached before anything is spent; ProviderError or BudgetError from the call.
    """
    image = Path(image).resolve()
    entry = ledger.find(image)
    if entry is None or not entry.get("shot_id"):
        raise NotARender("image not in out/log.jsonl with a shot_id")
    if not image.exists():
        image = ledger.ROOT / entry["file"]
    root = entry.get("root") or entry["file"]
    limit = load_json("brand/repairs.json")["limits"]["repairs_per_render"]
    done = repairs_made(root)
    if len(done) >= limit:
        raise RepairLimitReached(f"{len(done)} repairs already made on {root}; regenerate instead")

    spec = json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8"))
    prompt, extra = repair_prompt(code, spec)
    return run(
        tier or entry["tier"], prompt, [image, *extra], entry.get("size", "2K"), entry.get("aspect", "4:5"),
        tag=f"{entry['shot_id']}_repair{len(done) + 1}",
        extra={"shot_id": entry["shot_id"], "phase": "repair", "root": root, "repair_of": rel(image),
               "reason_code": code, "requested_tier": entry.get("requested_tier", entry["tier"])},
    )


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ledger.ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--reason", required=True)
    args = ap.parse_args()

    try:
        path, res = repair_image(Path(args.image), args.reason)
    except NotARender as e:
        print(f"error: {e}")
        return 2
    except RepairLimitReached as e:
        print(f"stop  {e}")
        return 2
    except (ProviderError, ledger.BudgetError) as e:
        print(f"FAIL  {e}")
        return 1
    limit = load_json("brand/repairs.json")["limits"]["repairs_per_render"]
    made = len(repairs_made(ledger.find(path)["root"]))  # this repair included
    print(f"ok    {rel(path)}  {res.model}  ${res.cost_usd:.4f}  (repair {made}/{limit} on this render)")
    print(ledger.status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
