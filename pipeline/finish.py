"""Finishing, approval and export.

    python -m pipeline.finish colour <image>            # shift garment colour onto the spec hex (free)
    python -m pipeline.finish background <image>        # clean the studio background, keep shadows (free)
    python -m pipeline.finish sheet <image> [...]       # contact sheet with spec and QA for approval
    python -m pipeline.finish approve <image> [--note]  # record the user's approval
    python -m pipeline.finish reject <image> --note ""  # record a rejection; the note becomes a pending prompt rule
    python -m pipeline.finish export <image> [--pdf]    # approved only: WebP 1600/800/400 + alt text + caption
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import ledger
from .colour import hex_to_rgb, lab_to_srgb, srgb_to_lab
from .compile import load_json, resolve_colour
from .qa import MASK_CACHE, colour_check, garment_mask, load_rgb, measure, rel, spec_path

ROOT = ledger.ROOT
WEBP_WIDTHS = (1600, 800, 400)


def _spec_for(image: Path) -> tuple[dict, dict]:
    entry = ledger.find(image)
    if entry is None or not entry.get("shot_id"):
        raise SystemExit(f"error: {image.name} is not in out/log.jsonl with a shot_id")
    return entry, json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8"))


def _derived(image: Path, suffix: str, rgb: np.ndarray, entry: dict, op: str, extra: dict) -> Path:
    """Save a finished variant next to its source and log it as a free generation-like entry."""
    out = image.with_name(f"{image.stem}_{suffix}.jpg")
    Image.fromarray(rgb.astype(np.uint8)).save(out, quality=95)
    src_mask = MASK_CACHE / f"{image.stem}.png"
    if src_mask.exists():  # geometry is unchanged, so the mask carries over
        shutil.copy(src_mask, MASK_CACHE / f"{out.stem}.png")
    keep = {k: entry[k] for k in ("shot_id", "tier", "requested_tier", "model", "size", "aspect", "prompt", "refs") if k in entry}
    ledger.record({**keep, "provider": "local", "phase": "finish", "op": op, "status": "ok",
                   "file": rel(out), "source": rel(image), "root": entry.get("root") or entry.get("file"),
                   "cost_usd": 0.0, **extra})
    return out


def _region(mask: np.ndarray, frac: float) -> np.ndarray:
    k = max(3, int(min(mask.shape) * frac))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    return cv2.dilate(closed, np.ones((k // 2 + 1, k // 2 + 1), np.uint8))


def colour_weights(rgb: np.ndarray, mask: np.ndarray, garment_lab: np.ndarray) -> np.ndarray:
    """Selective-colour weight 0..1: pixels near the garment's chroma and lightness range, inside a
    generous region around the (coarse) mask. Gaps in the mask still get corrected; skin does not."""
    lab = srgb_to_lab(rgb)
    inside = lab[mask > 0]
    lo, hi = np.percentile(inside[:, 0], [2, 98])
    chroma_d = np.hypot(lab[..., 1] - garment_lab[1], lab[..., 2] - garment_lab[2])
    w_c = np.exp(-((chroma_d / 7.0) ** 2))
    L = lab[..., 0]
    w_l = np.clip(1 - np.maximum(lo - 12 - L, L - hi - 12) / 10.0, 0, 1)
    region = cv2.GaussianBlur(_region(mask, 0.06).astype(np.float32) / 255.0, (0, 0), 15)
    return (w_c * w_l * region)[..., None]


def colour_correct(image: Path) -> Path:
    entry, spec = _spec_for(image)
    on_model = bool(spec["shot"].get("model", {}).get("present"))
    target_hex = resolve_colour(spec["shot"]["garment"]["colour"])["hex"]
    cfg = load_json("brand/qa_checklist.json")["colour"]
    rgb = load_rgb(image)
    mask, _ = garment_mask(image, on_model)
    garment = measure(rgb, mask, cfg["trim_percent"])
    target = srgb_to_lab(np.array(hex_to_rgb(target_hex), float))
    lab = srgb_to_lab(rgb) + colour_weights(rgb, mask, garment) * (target - garment)
    before = colour_check(image, target_hex, on_model)
    out = _derived(image, "cc", lab_to_srgb(lab), entry, "colour_correct",
                   {"delta_lab": [round(float(v), 2) for v in target - garment], "de_before": before.get("de2000")})
    after = colour_check(out, target_hex, on_model)
    print(f"colour  dE2000 {before.get('de2000')} -> {after.get('de2000')}  ({rel(out)})")
    return out


def clean_background(image: Path) -> Path:
    """Replace the background with a heavily smoothed copy of itself: removes specks, banding and
    stray artefacts but keeps the studio gradient and the soft floor shadow."""
    entry, spec = _spec_for(image)
    rgb = load_rgb(image).astype(np.float32)
    mask, _ = garment_mask(image, bool(spec["shot"].get("model", {}).get("present")))
    keep = cv2.GaussianBlur(_region(mask, 0.02).astype(np.float32) / 255.0, (0, 0), 6)[..., None]
    smooth = cv2.GaussianBlur(rgb, (0, 0), 30)
    out = _derived(image, "bg", rgb * keep + smooth * (1 - keep), entry, "clean_background", {})
    print(f"background cleaned -> {rel(out)}")
    return out


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(images: list[Path]) -> Path:
    thumb_w, thumb_h, cols, pad = 420, 525, 4, 16
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (thumb_w + pad) + pad, rows * (thumb_h + 150 + pad) + pad), "white")
    draw, font, small = ImageDraw.Draw(sheet), _font(17), _font(14)
    for i, img_path in enumerate(images):
        entry = ledger.find(img_path) or {}
        qa = (ledger.events(img_path, "qa") or [{}])[-1]
        approved = bool(ledger.events(img_path, "approved"))
        x, y = pad + (i % cols) * (thumb_w + pad), pad + (i // cols) * (thumb_h + 150 + pad)
        im = Image.open(img_path).convert("RGB")
        im.thumbnail((thumb_w, thumb_h))
        sheet.paste(im, (x, y))
        lines = [
            (entry.get("shot_id", "?"), font),
            (f"{entry.get('phase', '?')} | {entry.get('model', '?')}", small),
            (f"QA {qa.get('result', 'not run')}  dE {qa.get('colour', {}).get('de2000', '-')}  "
             f"{' '.join(qa.get('reason_codes', []))}", small),
            ("APPROVED" if approved else "awaiting approval", small),
            (img_path.name, small),
        ]
        ty = y + thumb_h + 8
        for text, f in lines:
            draw.text((x, ty), text, fill="black", font=f)
            ty += 24
    out = ROOT / "out" / "sheets" / f"sheet_{ledger.now().strftime('%Y%m%dT%H%M%S')}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return out


def alt_text(spec: dict) -> str:
    shot = spec["shot"]
    g = shot["garment"]
    cat = load_json(Path("catalogue") / f"{g['catalogue']}.json")
    colour = resolve_colour(g["colour"])["name"]
    parts = [f"{colour} {cat['style_name'].lower()} in {g['gsm']} GSM heavyweight fleece"]
    for d in shot.get("decoration", []):
        parts.append(f"with {d['type']} on the {d['placement']}")
    if shot.get("model", {}).get("present"):
        parts.append("worn by an AI-generated model")
    return ", ".join(parts)


def export(image: Path, pdf: bool = False) -> Path:
    if not ledger.events(image, "approved"):
        raise SystemExit(f"error: {image.name} has no approval record; nothing is exported without approval")
    entry, spec = _spec_for(image)
    brand = load_json("brand/brand.json")
    on_model = bool(spec["shot"].get("model", {}).get("present"))
    caption = brand["captions"]["on_model" if on_model else "concept_render"]
    out_dir = ROOT / "out" / "export" / entry["shot_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(image).convert("RGB")
    files = []
    for w in WEBP_WIDTHS:
        if w > im.width:
            continue
        variant = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        path = out_dir / f"{entry['shot_id']}-{w}.webp"
        variant.save(path, "WEBP", quality=86, method=6)
        files.append(rel(path))
    record = {"shot_id": entry["shot_id"], "alt": alt_text(spec), "caption": caption, "files": files,
              "source": rel(image), "spec": rel(spec_path(entry["shot_id"])), "ai_generated": True}
    if pdf:
        record["pdf"] = rel(_approval_pdf(im, spec, record, out_dir))
    (out_dir / f"{entry['shot_id']}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger.record({"event": "exported", "file": rel(image), **record})
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return out_dir


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def _approval_pdf(im: Image.Image, spec: dict, record: dict, out_dir: Path) -> Path:
    """One-page A4 approval sheet for a client mockup."""
    page = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi
    draw = ImageDraw.Draw(page)
    shown = im.copy()
    shown.thumbnail((1080, 1200))
    page.paste(shown, ((1240 - shown.width) // 2, 80))
    shot, y = spec["shot"], 80 + shown.height + 40
    lines = [("Mockup for approval", _font(34)), (record["alt"], _font(22)),
             *[(f"{d['type']}, {d['placement']}, {d['size_cm']} cm", _font(22)) for d in shot.get("decoration", [])],
             (record["caption"], _font(20)), ("Approved by: ____________________   Date: __________", _font(22))]
    for text, f in lines:
        for line in _wrap(draw, text, f, 1240 - 160):
            draw.text((80, y), line, fill="black", font=f)
            y += 40
        y += 8
    path = out_dir / f"{record['shot_id']}-approval.pdf"
    page.save(path, "PDF", resolution=150)
    return path


def record_verdict(image: Path, verdict: str, note: str, by: str = "user") -> None:
    """Record a person's verdict. `by` is who decided: "user" from this CLI, the studio user id from the page."""
    entry, spec = _spec_for(image)
    ledger.record({"event": verdict, "file": rel(image), "shot_id": entry["shot_id"], "by": by, "note": note})
    if verdict == "rejected" and note:
        rules_path = ROOT / "brand" / "prompt_rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {"rules": []}
        rules["rules"].append({"status": "pending", "applies_to": "all", "note": note, "prompt_text": "",
                               "from_shot": entry["shot_id"], "date": ledger.now().date().isoformat()})
        rules_path.write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("note saved as a pending rule in brand/prompt_rules.json; Claude turns it into prompt_text")
    print(f"{verdict}: {rel(image)}")


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["colour", "background", "sheet", "approve", "reject", "export"])
    ap.add_argument("images", nargs="+")
    ap.add_argument("--note", default="")
    ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()
    images = [Path(p).resolve() for p in args.images]
    if args.op == "sheet":
        print(rel(contact_sheet(images)))
        return 0
    for image in images:
        if args.op == "colour":
            colour_correct(image)
        elif args.op == "background":
            clean_background(image)
        elif args.op == "approve":
            record_verdict(image, "approved", args.note)
        elif args.op == "reject":
            if not args.note:
                raise SystemExit("error: a rejection needs --note")
            record_verdict(image, "rejected", args.note)
        elif args.op == "export":
            export(image, args.pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
