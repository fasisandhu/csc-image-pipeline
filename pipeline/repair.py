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
from .providers import ProviderError
from .qa import rel, spec_path

KEEP = ("Keep everything else exactly as it is: garment shape, colour and construction, model, pose, "
        "lighting, background and framing.")


def repair_prompt(code: str, spec: dict) -> tuple[str, list[str]]:
    """Prompt and extra reference images (after Image 1, the render being repaired)."""
    rule = load_json("brand/repairs.json")["codes"][code]
    if rule["fix"] != "repair":
        raise SystemExit(f"error: {code} is fixed by {rule['fix']}, not a repair edit")
    shot = spec["shot"]
    cat = load_json(Path("catalogue") / f"{shot['garment']['catalogue']}.json")
    fill = {"shoulder": cat.get("shoulder", "")}
    refs: list[str] = []
    if code.startswith("logo_"):
        deco = next(d for d in shot["decoration"] if d.get("render_mode") == "generate_and_verify")
        design = design_for(deco["artwork"]) or {}
        chest = cat.get("measurements_cm", {}).get("M", {}).get("chest_width")
        chest = float(chest) if isinstance(chest, (int, float)) else ASSUMED_CHEST_WIDTH_CM
        fill.update(placement=deco["placement"], technique=deco["type"], text=design.get("text", ""),
                    size_cm=deco["size_cm"], torso_share=round(100 * deco["size_cm"] / chest))
        refs.append(ref_png(deco["artwork"]))
    return f"Edit Image 1. Change only {rule['instruction'].format(**fill)}. {KEEP}", refs


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ledger.ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--reason", required=True)
    args = ap.parse_args()

    image = Path(args.image).resolve()
    entry = ledger.find(image)
    if entry is None or not entry.get("shot_id"):
        print("error: image not in out/log.jsonl with a shot_id")
        return 2
    if not image.exists():
        image = ledger.ROOT / entry["file"]
    root = entry.get("root") or entry["file"]
    limit = load_json("brand/repairs.json")["limits"]["repairs_per_render"]
    done = [e for e in ledger.entries() if e.get("status") == "ok" and e.get("phase") == "repair" and e.get("root") == root]
    if len(done) >= limit:
        print(f"stop  {len(done)} repairs already made on {root}; regenerate instead")
        return 2

    spec = json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8"))
    prompt, extra = repair_prompt(args.reason, spec)
    try:
        path, res = run(
            entry["tier"], prompt, [image, *extra], entry.get("size", "2K"), entry.get("aspect", "4:5"),
            tag=f"{entry['shot_id']}_repair{len(done) + 1}",
            extra={"shot_id": entry["shot_id"], "phase": "repair", "root": root, "repair_of": rel(image),
                   "reason_code": args.reason, "requested_tier": entry.get("requested_tier", entry["tier"])},
        )
    except (ProviderError, ledger.BudgetError) as e:
        print(f"FAIL  {e}")
        return 1
    print(f"ok    {rel(path)}  {res.model}  ${res.cost_usd:.4f}  (repair {len(done) + 1}/{limit} on this render)")
    print(ledger.status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
