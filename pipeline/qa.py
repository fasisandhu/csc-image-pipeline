"""QA: garment mask, colour check against the spec hex, QA records and the next action.

Claude reviews each render visually against the checklist; this script adds the colour
measurement, records the verdict with reason codes, moves failures to out/reject/ and
says what happens next (finish, repair, regenerate or flag) under the CLAUDE.md limits.

    python -m pipeline.qa <image>                          # colour check + checklist, records nothing
    python -m pipeline.qa <image> --visual pass            # record a verdict
    python -m pipeline.qa <image> --fail drawcord_shape --note "round cords"
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import ledger
from .colour import de2000, hex_to_rgb, lab_to_srgb, rgb_to_hex, robust_median_lab, srgb_to_lab
from .compile import compile_file, load_json, resolve_colour
from .providers import ProviderError

ROOT = ledger.ROOT
MASK_CACHE = ROOT / "out" / "cache" / "masks"
_PARTS = ("each part as a separate entry: (1) body including hood and kangaroo pocket, "
          "(2) left sleeve including cuff, (3) right sleeve including cuff")
GARMENT_TARGET = f"the hoodie the person is wearing, {_PARTS}; fabric only, excluding skin, hands, face, hair, trousers and background"
PACKSHOT_TARGET = f"the hoodie, {_PARTS}; fabric only, excluding the background and the floor shadow"


def rel(path: str | Path) -> str:
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def load_rgb(path: str | Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def spec_path(shot_id: str) -> Path:
    """specs/<id>.json (hand-written, tracked) else out/specs/<id>.json (written by the studio worker)."""
    hand, dash = ROOT / "specs" / f"{shot_id}.json", ROOT / "out" / "specs" / f"{shot_id}.json"
    for p in (hand, dash):
        if p.exists():
            return p
    raise FileNotFoundError(f"no spec for shot {shot_id!r}: looked in {rel(hand)} and {rel(dash)}")


def _largest_component(mask: np.ndarray) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return mask
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(labels == biggest, 255, 0).astype(np.uint8)


def packshot_mask(rgb: np.ndarray, threshold: float = 12.0) -> np.ndarray:
    """Garment on a plain studio background. A colour-difference seed from the border background,
    refined with GrabCut so soft floor shadows and vignetting fall back to background."""
    scale = 0.25
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    lab = srgb_to_lab(small)
    border = np.concatenate([lab[:8].reshape(-1, 3), lab[-8:].reshape(-1, 3),
                             lab[:, :8].reshape(-1, 3), lab[:, -8:].reshape(-1, 3)])
    diff = np.linalg.norm(lab - np.median(border, axis=0), axis=-1)
    seed = np.where(diff > threshold, 255, 0).astype(np.uint8)
    seed = _largest_component(cv2.morphologyEx(seed, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)))

    gc = np.full(seed.shape, cv2.GC_PR_BGD, np.uint8)
    gc[seed > 0] = cv2.GC_PR_FGD
    gc[cv2.erode(seed, np.ones((25, 25), np.uint8)) > 0] = cv2.GC_FGD
    gc[:6, :] = gc[-6:, :] = cv2.GC_BGD
    gc[:, :6] = gc[:, -6:] = cv2.GC_BGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    mask = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    mask = _largest_component(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mask, contours, -1, 255, thickness=cv2.FILLED)
    return cv2.resize(mask, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)


def not_background(rgb: np.ndarray, threshold: float = 10.0) -> np.ndarray:
    """Pixels that differ from the plain studio background sampled at the image border."""
    lab = srgb_to_lab(rgb[::4, ::4])
    border = np.concatenate([lab[:6].reshape(-1, 3), lab[-6:].reshape(-1, 3),
                             lab[:, :6].reshape(-1, 3), lab[:, -6:].reshape(-1, 3)])
    diff = np.linalg.norm(lab - np.median(border, axis=0), axis=-1)
    small = np.where(diff > threshold, 255, 0).astype(np.uint8)
    return cv2.resize(small, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)


def polygon_mask(shape: tuple[int, int], polygons: list[list[list[int]]]) -> np.ndarray:
    """Union of polygons, each filled on its own so overlapping parts never cancel out."""
    mask = np.zeros(shape, np.uint8)
    for poly in polygons:
        if len(poly) >= 3:
            part = np.zeros(shape, np.uint8)
            cv2.fillPoly(part, [np.array(poly, np.int32)], 255)
            mask = np.maximum(mask, part)
    return mask


def garment_mask(image: str | Path, on_model: bool) -> tuple[np.ndarray, str]:
    """Binary garment mask (uint8 0/255) from Gemini vision segmentation, cached per image so
    re-checks are free. Packshots fall back to free background subtraction if the budget blocks it."""
    image = Path(image)
    rgb = load_rgb(image)
    cached = MASK_CACHE / f"{image.stem}.png"
    if cached.exists():
        return np.asarray(Image.open(cached).convert("L")), "vision (cached)"
    from .providers.vision import GeminiVision

    vision = GeminiVision()
    target = GARMENT_TARGET if on_model else PACKSHOT_TARGET
    try:
        res = ledger.logged_call(
            {"event": "vision", "purpose": "garment_mask", "provider": vision.name, "model": vision.model,
             "tier": "qa", "image": rel(image)},
            vision.estimate(),
            lambda: vision.segment(image, target),
        )
    except ledger.BudgetError:
        if on_model:
            raise
        return packshot_mask(rgb), "background (budget fallback)"
    parts = polygon_mask(rgb.shape[:2], [i["polygon"] for i in res.data if i.get("polygon")])
    mask = cv2.bitwise_and(parts, not_background(rgb))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    MASK_CACHE.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask).save(cached)
    return mask, "vision"


def inner(mask: np.ndarray, frac: float = 0.012) -> np.ndarray:
    """Erode so seams and edges against the background do not pollute the measurement."""
    k = max(3, int(min(mask.shape) * frac))
    return cv2.erode(mask, np.ones((k, k), np.uint8))


def measure(rgb: np.ndarray, mask: np.ndarray, trim: float) -> np.ndarray:
    lab = srgb_to_lab(rgb[inner(mask) > 0])
    return robust_median_lab(lab, trim)


def colour_check(image: str | Path, target_hex: str, on_model: bool) -> dict:
    cfg = load_json("brand/qa_checklist.json")["colour"]
    rgb = load_rgb(image)
    mask, source = garment_mask(image, on_model)
    if (mask > 0).mean() < 0.02:
        return {"result": "error", "reason": "garment mask too small", "mask_source": source}
    lab = measure(rgb, mask, cfg["trim_percent"])
    target = srgb_to_lab(np.array(hex_to_rgb(target_hex), float))
    de = de2000(lab, target)
    code = None if de <= cfg["tolerance"] else "colour_drift" if de <= cfg["drift_max"] else "colour_wrong"
    return {
        "result": "pass" if code is None else "fail", "code": code, "de2000": round(de, 2),
        "measured_hex": rgb_to_hex(lab_to_srgb(lab)), "target_hex": target_hex,
        "dL": round(lab[0] - target[0], 1), "da": round(lab[1] - target[1], 1), "db": round(lab[2] - target[2], 1),
        "mask_source": source, "mask_fraction": round(float((mask > 0).mean()), 3),
    }


def allowed_texts(spec: dict) -> list[str]:
    """Text the shot may legitimately show: each decoration design's text plus the neck label."""
    from .designs import design_for

    shot = spec["shot"]
    out = []
    for d in shot.get("decoration", []):
        design = design_for(d.get("artwork", ""))
        if design and design.get("text"):
            out.append(design["text"])
    neck = (shot.get("labels") or {}).get("neck")
    if neck:
        out.append(neck)
    return out


def stray_text_check(image: str | Path, allowed: list[str]) -> dict:
    """Read all text in the render; flag any that is not the shot's own artwork or neck label."""
    from .logo import normalise_text
    from .providers.vision import GeminiVision

    image = Path(image)
    vision = GeminiVision()
    res = ledger.logged_call(
        {"event": "vision", "purpose": "stray_text", "provider": vision.name, "model": vision.model,
         "tier": "qa", "image": rel(image)},
        vision.estimate(),
        lambda: vision.read_all_text(image),
    )
    found = [i["text"] for i in res.data]
    norm_allowed = [normalise_text(a) for a in allowed]
    stray = []
    for t in found:
        n = normalise_text(t)
        if n and not any(n in a for a in norm_allowed):
            stray.append(t)
    return {"result": "fail" if stray else "pass", "code": "unintended_text" if stray else None,
            "found": found, "stray": stray}


def next_action(entry: dict, codes: list[str]) -> tuple[str, str | None]:
    """(action, code): approve | finish | repair | composite | regenerate | flag, following the CLAUDE.md limits."""
    rules = load_json("brand/repairs.json")
    limits = rules["limits"]
    if not codes:
        return "approve", None
    fixes = {c: rules["codes"].get(c, {"fix": "regenerate"})["fix"] for c in codes}
    shot = entry.get("shot_id")
    root = entry.get("root") or entry.get("file")
    finals = [e for e in ledger.entries() if e.get("status") == "ok" and e.get("shot_id") == shot and e.get("phase") == "final"]
    repairs = [e for e in ledger.entries() if e.get("status") == "ok" and e.get("phase") == "repair" and e.get("root") == root]

    def regenerate_or_flag() -> tuple[str, str | None]:
        regens = max(0, len(finals) - 1)
        return ("regenerate", None) if regens < limits["regenerations_per_shot"] else ("flag", None)

    if any(f == "regenerate" for f in fixes.values()):
        return regenerate_or_flag()
    repair_codes = [c for c, f in fixes.items() if f == "repair"]
    if any(c.startswith("logo_") for c in repair_codes) and repairs:
        return "composite", next(c for c in repair_codes if c.startswith("logo_"))  # logo: repair once, then composite
    if repair_codes:
        if len(repairs) < limits["repairs_per_render"]:
            return "repair", repair_codes[0]  # one change per repair
        return regenerate_or_flag()
    return "finish", next(c for c, f in fixes.items() if f == "finish")


def record_qa(entry: dict, image: str | Path, codes: list[str], colour: dict, note: str, action: str,
              code: str | None, result: str | None = None, **extra) -> dict:
    """The ledger `qa` event, shared by this CLI and the studio checks (which add `by` and their results)."""
    return ledger.record({"event": "qa", "shot_id": entry["shot_id"], "file": rel(image),
                          "result": result or ("pass" if not codes else "fail"), "reason_codes": codes,
                          "colour": colour, "note": note, "next": action, "next_code": code, **extra})


REJECT_ACTIONS = ("repair", "regenerate", "flag", "composite")  # a render judged bad leaves out/finals


def move_to_reject(image: Path, rel_image: str | None = None) -> Path:
    """Move a render judged bad to out/reject/ and log the move, so ledger.find keeps its root and the
    repair and regeneration limits count it. Shared by this CLI and the studio's review chain."""
    rel_image = rel_image or rel(image)
    dest = ROOT / "out" / "reject" / image.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(image, dest)
    ledger.record({"event": "moved", "file": rel_image, "moved_to": dest.relative_to(ROOT).as_posix()})
    return dest


def checklist(shot_id: str) -> list[str]:
    compiled = ROOT / "prompts" / f"{shot_id}.json"
    if compiled.exists():
        return json.loads(compiled.read_text(encoding="utf-8"))["qa"]
    return compile_file(spec_path(shot_id)).qa


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--visual", choices=["pass"], help="Claude's visual checklist verdict")
    ap.add_argument("--fail", nargs="*", default=[], help="reason codes from brand/repairs.json")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    image = Path(args.image).resolve()
    entry = ledger.find(image)
    if entry is None or not entry.get("shot_id"):
        print("error: image not in out/log.jsonl with a shot_id")
        return 2
    spec = json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8"))
    on_model = bool(spec["shot"].get("model", {}).get("present"))
    target = resolve_colour(spec["shot"]["garment"]["colour"])["hex"]

    try:
        colour = colour_check(image, target, on_model)
    except (ProviderError, ledger.BudgetError) as e:
        print(f"error: {e}")
        return 1
    print(f"colour  {colour['result']}  dE2000 {colour.get('de2000')}  measured {colour.get('measured_hex')} "
          f"vs {target}  (dL {colour.get('dL')}, da {colour.get('da')}, db {colour.get('db')}; mask: {colour['mask_source']})")
    try:
        stray = stray_text_check(image, allowed_texts(spec))
    except (ProviderError, ledger.BudgetError) as e:
        print(f"stray text: error {e}")
    else:
        print("stray text: pass" if stray["result"] == "pass" else f"stray text: FAIL {stray['stray']}")
    if not args.visual and not args.fail:
        print("\nChecklist for visual review:")
        for q in checklist(entry["shot_id"]):
            print(f"  [ ] {q}")
        print("\nRecord with --visual pass or --fail <codes>.")
        return 0

    unknown = [c for c in args.fail if c not in load_json("brand/repairs.json")["codes"]]
    if unknown:
        print(f"error: unknown reason codes {unknown}")
        return 2
    codes = list(args.fail) + ([colour["code"]] if colour.get("code") else [])
    action, code = next_action(entry, codes)
    rel_image = rel(image)
    record_qa(entry, image, codes, colour, args.note, action, code)
    print(f"verdict {'pass' if not codes else 'fail'}  codes {codes or '-'}  next: {action}{' ' + code if code else ''}")

    if action in REJECT_ACTIONS:
        dest = move_to_reject(image, rel_image)
        print(f"moved   -> {dest.relative_to(ROOT).as_posix()}")
    if action == "flag":
        print("FLAG    two repairs and two regenerations used; needs the user's decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
