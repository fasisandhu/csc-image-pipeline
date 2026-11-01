"""Logo step: rasterise artwork, verify a generated logo, composite the real artwork as the fallback.

    python -m pipeline.logo rasterize                      # brand/designs/*.svg -> brand/designs/png/
    python -m pipeline.logo verify <image>                 # text (vision OCR), outline IoU, ink colour
    python -m pipeline.logo composite <image> --design kilnworks-kw --placement "left chest" \
        --size-cm 8 --preset flat_embroidery

Verification never trusts the generator: text must match exactly, the outline must overlap the
artwork, and ink colours must be close. Anything else is repaired once, then composited from the
real file.
"""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import ledger
from .colour import de2000, hex_to_rgb, lab_to_srgb, robust_median_lab, srgb_to_lab
from .compile import is_missing, load_json
from .designs import design_for, rgba_png

ROOT = ledger.ROOT
PNG_DIR = ROOT / "brand" / "designs" / "png"
RASTER_WIDTH = 1200
ASSUMED_CHEST_WIDTH_CM = 60.0  # flat chest width, used only until catalogue measurements are filled in


# --- artwork ------------------------------------------------------------------------------

def rasterize(svg: Path) -> tuple[Path, Path]:
    import resvg_py

    PNG_DIR.mkdir(parents=True, exist_ok=True)
    data = bytes(resvg_py.svg_to_bytes(svg_path=str(svg), width=RASTER_WIDTH))
    rgba = Image.open(io.BytesIO(data)).convert("RGBA")
    rgba = rgba.crop(rgba.getbbox())  # size_cm applies to the visible artwork, not the SVG canvas
    out = PNG_DIR / f"{svg.stem}.png"
    rgba.save(out)
    arr = np.asarray(rgba).astype(float)
    ink = arr[..., 3] > 128
    ink_l = srgb_to_lab(arr[..., :3][ink]).mean(axis=0)[0] if ink.any() else 50
    bg = (217, 217, 217) if ink_l < 50 else (51, 51, 51)
    flat = Image.new("RGB", rgba.size, bg)
    pad = Image.new("RGB", (int(rgba.width * 1.15), int(rgba.height * 1.15)), bg)
    flat.paste(rgba, (0, 0), rgba)
    pad.paste(flat, ((pad.width - flat.width) // 2, (pad.height - flat.height) // 2))
    ref = PNG_DIR / f"{svg.stem}_ref.png"
    pad.save(ref)
    return out, ref


def _chest_cm(spec: dict) -> float | None:
    cat = load_json(Path("catalogue") / f"{spec['shot']['garment']['catalogue']}.json")
    m = cat.get("measurements_cm", {}).get("M", {}).get("chest_width")
    return None if is_missing(m) else float(m)


# --- verification -------------------------------------------------------------------------

def normalise_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def outline_iou(crop_rgb: np.ndarray, art_alpha: np.ndarray) -> tuple[float, np.ndarray]:
    """IoU between the decoration's ink (Otsu on lightness, best polarity) and the artwork alpha.
    The artwork is fitted into the crop at a few uniform scales and aligned with a rigid (rotation +
    translation) ECC step only, so a different shape cannot be warped into a match.
    Returns the IoU and the ink mask in crop coordinates (for colour sampling)."""
    h, w = crop_rgb.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ah, aw = art_alpha.shape
    fit = min(w / aw, h / ah)
    best = (0.0, ink)  # (iou, ink mask in crop coordinates)
    for scale in (0.85, 0.92, 1.0):
        art = cv2.resize(art_alpha, (max(1, int(aw * fit * scale)), max(1, int(ah * fit * scale))), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((h, w), np.uint8)
        y0, x0 = (h - art.shape[0]) // 2, (w - art.shape[1]) // 2
        canvas[y0:y0 + art.shape[0], x0:x0 + art.shape[1]] = art
        target = (canvas > 128).astype(np.uint8)
        for cand in (ink, 1 - ink):
            warp = np.eye(2, 3, dtype=np.float32)
            try:
                _, warp = cv2.findTransformECC(cv2.GaussianBlur(target.astype(np.float32), (0, 0), 3),
                                               cv2.GaussianBlur(cand.astype(np.float32), (0, 0), 3),
                                               warp, cv2.MOTION_EUCLIDEAN,
                                               (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
                aligned = cv2.warpAffine(cand, warp, (w, h), flags=cv2.INTER_NEAREST + cv2.WARP_INVERSE_MAP)
            except cv2.error:
                aligned = cand
            union = np.logical_or(aligned, target).sum()
            iou = float(np.logical_and(aligned, target).sum() / union) if union else 0.0
            if iou > best[0]:
                best = (iou, cand)
    return best


def verify(image: Path) -> dict:
    from .providers.vision import GeminiVision
    from .qa import load_rgb, rel, spec_path

    entry = ledger.find(image)
    spec = json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8"))
    cfg = load_json("brand/qa_checklist.json")["logo"]
    rgb = load_rgb(image)
    vision = GeminiVision()
    results = []
    for deco in [d for d in spec["shot"].get("decoration", []) if d.get("render_mode") == "generate_and_verify"]:
        design = design_for(deco["artwork"]) or {}
        res = ledger.logged_call(
            {"event": "vision", "purpose": "logo_locate_read", "provider": vision.name, "model": vision.model,
             "tier": "qa", "image": rel(image)},
            vision.estimate(),
            lambda: vision.locate_and_read(image, f"the {deco['type']} logo or graphic on the {deco['placement']} of the hoodie"),
        )
        if not res.data or "box" not in res.data[0]:
            results.append({"placement": deco["placement"], "found": False, "codes": ["logo_shape"]})
            continue
        hit = res.data[0]
        x0, y0, x1, y1 = hit["box"]
        px, py = int((x1 - x0) * 0.06), int((y1 - y0) * 0.06)
        x0, y0, x1, y1 = max(0, x0 - px), max(0, y0 - py), min(rgb.shape[1], x1 + px), min(rgb.shape[0], y1 + py)
        crop = rgb[y0:y1, x0:x1]

        expected = design.get("text", "")
        text_ok = (not expected) or normalise_text(hit.get("text", "")) == normalise_text(expected)
        alpha = np.asarray(Image.open(ROOT / rgba_png(deco["artwork"])).convert("RGBA"))[..., 3]
        iou, ink = outline_iou(crop, alpha)
        ink_px = crop[ink > 0]
        ink_de = None
        if len(ink_px) and design.get("colours"):
            ink_lab = robust_median_lab(srgb_to_lab(ink_px), 10)
            ink_de = min(de2000(ink_lab, srgb_to_lab(np.array(hex_to_rgb(c), float))) for c in design["colours"])
        # size: logo width against the torso panel, whose real width comes from the catalogue
        geo = placement_geometry(image, deco["placement"])
        torso_px = abs(geo["torso_box"][2] - geo["torso_box"][0])
        chest_cm = _chest_cm(spec)
        est_cm = (hit["box"][2] - hit["box"][0]) / torso_px * (chest_cm or ASSUMED_CHEST_WIDTH_CM) if torso_px else None
        size_ok = est_cm is None or deco["size_cm"] * cfg["size_low"] <= est_cm <= deco["size_cm"] * cfg["size_high"]
        codes = []
        if not size_ok:
            codes.append("logo_size")
        if not text_ok:
            codes.append("logo_text")
        if iou < cfg["min_iou"]:
            codes.append("logo_shape")
        if ink_de is not None and ink_de > cfg["max_ink_de"]:
            codes.append("logo_colour")
        results.append({"placement": deco["placement"], "found": True, "box": [x0, y0, x1, y1],
                        "text_read": hit.get("text", ""), "text_expected": expected, "text_ok": text_ok,
                        "iou": round(iou, 3), "ink_de": None if ink_de is None else round(ink_de, 2),
                        "size_cm_est": None if est_cm is None else round(est_cm, 1), "size_cm_spec": deco["size_cm"],
                        "chest_cm_assumed": chest_cm is None, "codes": codes})
    verdict = {"result": "pass" if results and not any(r["codes"] for r in results) else "fail", "decorations": results}
    ledger.record({"event": "logo_verify", "file": rel(image), "shot_id": entry["shot_id"], **verdict})
    return verdict


# --- composite ----------------------------------------------------------------------------

PRESETS = {
    "print":           {"fold": 0.85, "texture": 0.6, "bevel": 0.0, "shadow": 0.0, "shadow_px": 0, "thread": 0.0},
    "flat_embroidery": {"fold": 0.6, "texture": 0.2, "bevel": 8.0, "shadow": 0.25, "shadow_px": 2, "thread": 7.0},
    "3d_puff":         {"fold": 0.45, "texture": 0.1, "bevel": 22.0, "shadow": 0.45, "shadow_px": 5, "thread": 5.0},
}
TECHNIQUE_PRESET = {"screen print": "print", "flat embroidery": "flat_embroidery", "3D puff embroidery": "3d_puff"}


def placement_geometry(image: Path, placement: str) -> dict:
    """Vision: centre of the placement area, the torso panel box (for scale) and polygons of anything
    hanging in front of the fabric near the print (drawcords, aglets, hands), all in pixels."""
    from .providers.vision import GeminiVision, VisionResult, _looks_yx
    from .qa import rel

    vision = GeminiVision()
    prompt = (
        "This is a hoodie. Return one JSON object with: "
        f"\"centre\": [y, x], the centre of the {placement} print area (left and right are the wearer's); "
        "\"torso_box\": [ymin, xmin, ymax, xmax], the torso panel between the two armhole seams, excluding sleeves "
        "and hood; \"occluders\": a list of objects that hang or sit in front of the fabric near that print area, "
        "such as each drawcord with its aglet, or hands, each as {\"label\", \"box_2d\": [ymin, xmin, ymax, xmax], "
        "\"mask\": polygon of [x, y] points}. All coordinates normalised to 0-1000."
    )

    def call() -> VisionResult:
        data, cost, units, (w, h) = vision._ask(image, prompt)
        if isinstance(data, list):
            data = data[0] if data else {}
        y, x = data["centre"]
        y0, x0, y1, x1 = data["torso_box"]
        occ = []
        for o in data.get("occluders", []):
            pts = o.get("mask") or []
            if len(pts) < 3:
                continue
            if "box_2d" in o and _looks_yx(pts, o["box_2d"]):
                pts = [[b, a] for a, b in pts]
            occ.append({"label": o.get("label", ""), "polygon": [[round(a * w / 1000), round(b * h / 1000)] for a, b in pts]})
        geo = {"centre": [round(x * w / 1000), round(y * h / 1000)],
               "torso_box": [round(x0 * w / 1000), round(y0 * h / 1000), round(x1 * w / 1000), round(y1 * h / 1000)],
               "occluders": occ}
        return VisionResult(geo, cost, units)

    res = ledger.logged_call(
        {"event": "vision", "purpose": "placement_geometry", "provider": vision.name, "model": vision.model,
         "tier": "qa", "image": rel(image)}, vision.estimate(), call)
    ledger.record({"event": "geometry", "file": rel(image), "placement": placement, **res.data})
    return res.data


def _bevel(alpha: np.ndarray) -> np.ndarray:
    """Shading in [-1, 1] (light from top-left) from a height map that rounds each stroke across
    its full width, so 3D puff reads as raised and flat embroidery as slightly domed."""
    a = (alpha > 0.5).astype(np.uint8)
    dist = cv2.distanceTransform(a, cv2.DIST_L2, 5)
    half = max(2.0, float(np.percentile(dist[a > 0], 90))) if a.any() else 2.0
    height = cv2.GaussianBlur(np.sqrt(np.clip(dist / half, 0, 1)).astype(np.float32), (0, 0), 1.0)
    gy, gx = np.gradient(height)
    return np.clip((-gx - gy) / np.sqrt(2) * half, -1, 1)


def composite(image: Path, artwork: str, placement: str, size_cm: float, preset: str,
              geometry: dict | None = None) -> tuple[np.ndarray, dict]:
    """Place the real artwork on the garment: scale from size_cm, follow folds with a displacement
    map from the fabric, carry the fabric's light and texture, then apply the decoration finish."""
    from .qa import load_rgb

    p = PRESETS[preset]
    rgb = load_rgb(image).astype(np.float32)
    H, W = rgb.shape[:2]
    geo = geometry or placement_geometry(image, placement)
    chest_px = abs(geo["torso_box"][2] - geo["torso_box"][0])
    cat_chest = None
    entry = ledger.find(image)
    if entry and entry.get("shot_id"):
        from .qa import spec_path
        cat_chest = _chest_cm(json.loads(spec_path(entry["shot_id"]).read_text(encoding="utf-8")))
    chest_cm = cat_chest or ASSUMED_CHEST_WIDTH_CM
    px_per_cm = chest_px / chest_cm

    art = Image.open(ROOT / rgba_png(artwork)).convert("RGBA")
    tw = max(8, int(size_cm * px_per_cm))
    art = np.asarray(art.resize((tw, max(1, int(art.height * tw / art.width))), Image.LANCZOS)).astype(np.float32)
    ah, aw = art.shape[:2]
    cx, cy = geo["centre"]
    x0, y0 = int(cx - aw / 2), int(cy - ah / 2)

    # artwork on a full-size canvas
    layer = np.zeros((H, W, 4), np.float32)
    sx0, sy0, sx1, sy1 = max(0, -x0), max(0, -y0), min(aw, W - x0), min(ah, H - y0)
    layer[y0 + sy0:y0 + sy1, x0 + sx0:x0 + sx1] = art[sy0:sy1, sx0:sx1]

    # displacement: folds shift the decoration along the fabric's lightness gradient
    lab = srgb_to_lab(rgb)
    L = lab[..., 0].astype(np.float32)
    folds = cv2.GaussianBlur(L, (0, 0), 6) - cv2.GaussianBlur(L, (0, 0), 40)
    gy, gx = np.gradient(cv2.GaussianBlur(folds, (0, 0), 4))
    strength = 0.004 * aw
    mx, my = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    layer = cv2.remap(layer, mx + gx * strength, my + gy * strength, cv2.INTER_LINEAR)
    alpha = np.clip(layer[..., 3] / 255.0, 0, 1)
    if geo.get("occluders"):  # drawcords and hands stay in front of the decoration
        from .qa import polygon_mask
        occ = polygon_mask((H, W), [o["polygon"] for o in geo["occluders"]])
        occ = cv2.GaussianBlur(occ.astype(np.float32) / 255.0, (0, 0), 1.0)  # outlines already run slightly wide
        alpha = alpha * (1 - occ)

    # artwork colour in Lab, carrying the fabric's folds and texture
    art_lab = srgb_to_lab(layer[..., :3])
    region = alpha > 0.01
    base_L = np.median(L[region]) if region.any() else 0
    texture = L - cv2.GaussianBlur(L, (0, 0), 2)
    art_lab[..., 0] += p["fold"] * (cv2.GaussianBlur(L, (0, 0), 3) - base_L) + p["texture"] * texture

    if p["thread"]:  # satin-stitch texture at 45 degrees
        period = max(3.0, aw / 160)
        stripes = np.sin(2 * np.pi * (mx + my) / (period * np.sqrt(2)))
        noise = cv2.GaussianBlur(np.random.default_rng(7).normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.8)
        art_lab[..., 0] += p["thread"] * (0.7 * stripes + 0.3 * noise)
    if p["bevel"]:
        art_lab[..., 0] += p["bevel"] * _bevel(alpha)

    out = rgb.copy()
    if p["shadow"]:  # soft contact shadow cast down-right onto the fabric
        k = p["shadow_px"] * max(1, W // 1000)
        sh = cv2.GaussianBlur(np.roll(np.roll(alpha, k, axis=0), k, axis=1), (0, 0), k + 1)
        out *= (1 - p["shadow"] * np.clip(sh - alpha, 0, 1))[..., None]
    art_rgb = lab_to_srgb(art_lab).astype(np.float32)
    out = out * (1 - alpha[..., None]) + art_rgb * alpha[..., None]
    info = {"artwork": artwork, "placement": placement, "size_cm": size_cm, "preset": preset,
            "px_per_cm": round(px_per_cm, 2), "chest_cm": chest_cm, "chest_cm_assumed": cat_chest is None,
            "centre": geo["centre"], "torso_box": geo["torso_box"], "occluders": len(geo.get("occluders", []))}
    return np.clip(out, 0, 255), info


def main() -> int:
    from dotenv import load_dotenv

    from .finish import _derived, _spec_for
    from .qa import rel

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["rasterize", "verify", "composite"])
    ap.add_argument("image", nargs="?")
    ap.add_argument("--design")
    ap.add_argument("--placement")
    ap.add_argument("--size-cm", type=float)
    ap.add_argument("--preset", choices=list(PRESETS))
    ap.add_argument("--as-shot", help="log the composite under this shot (fallback for a failed logo render)")
    args = ap.parse_args()

    if args.op == "rasterize":
        for svg in sorted((ROOT / "brand" / "designs").glob("*.svg")):
            out, ref = rasterize(svg)
            print(f"{svg.name} -> {rel(out)}, {rel(ref)}")
        return 0
    image = Path(args.image).resolve()
    if args.op == "verify":
        print(json.dumps(verify(image), indent=2))
        return 0
    design = design_for(args.design) or {}
    artwork = design.get("file", args.design)
    placement = args.placement or design.get("suggested", {}).get("placement")
    size_cm = args.size_cm or design.get("suggested", {}).get("size_cm")
    preset = args.preset or TECHNIQUE_PRESET.get(design.get("suggested", {}).get("technique", ""), "print")
    entry, spec = _spec_for(image)
    if args.as_shot:
        entry = {**entry, "shot_id": args.as_shot, "root": entry.get("root") or entry.get("file")}
        spec = json.loads((ROOT / "specs" / f"{args.as_shot}.json").read_text(encoding="utf-8"))
    cat = load_json(Path("catalogue") / f"{spec['shot']['garment']['catalogue']}.json")
    limit = cat["decoration_placements"].get(placement, {}).get("max_cm")
    if not is_missing(limit) and size_cm > limit:
        raise SystemExit(f"error: {placement} max is {limit} cm")
    rgb, info = composite(image, artwork, placement, size_cm, preset)
    out = _derived(image, f"logo-{Path(artwork).stem}", rgb, entry, "composite", {"composite": info})
    if info["chest_cm_assumed"]:
        print(f"warn  chest width not in catalogue; assumed {ASSUMED_CHEST_WIDTH_CM} cm for scale")
    print(f"composited -> {rel(out)}  ({info['preset']}, {info['size_cm']} cm, {info['px_per_cm']} px/cm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
