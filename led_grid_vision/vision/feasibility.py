"""Stage 1 feasibility report: can this camera resolve the 64 LEDs? Uses all_off and all_on only.

Thresholds are first estimates (VSTAGE_1_CAMERA.md).
"""

import numpy as np
import cv2

THRESHOLD_NOTE = "Thresholds are first estimates, not yet validated on real captures."
MIN_PEAK_DIFF = 15.0  # below this the all_on - all_off difference is treated as no signal
CLOSE_KERNELS = (1, 3, 5, 9, 15, 21, 31)
MIN_COVERAGE = 0.8  # largest region must hold this share of the foreground pixels


def max_channel(img: np.ndarray) -> np.ndarray:
    return img.max(axis=2) if img.ndim == 3 else img


def mean_frame(frames: list[np.ndarray]) -> np.ndarray:
    return np.mean(np.stack(frames).astype(np.float32), axis=0)


def find_grid_region(diff: np.ndarray):
    """Otsu on diff, then close with growing kernels until one region holds the lit dots.

    Returns (mask uint8 0/1, kernel) or (None, None)."""
    if float(diff.max()) < MIN_PEAK_DIFF:
        return None, None
    d8 = np.clip(cv2.GaussianBlur(diff, (0, 0), 1.0), 0, 255).astype(np.uint8)
    _, fg = cv2.threshold(d8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return largest_region(fg)


def largest_region(fg: np.ndarray):
    """Close a 0/1 foreground with growing kernels until its largest region holds the lit dots.

    Separate LEDs are separate blobs; closing merges them into one grid region.
    Returns (mask uint8 0/1, kernel) or (None, None)."""
    total = int(fg.sum())
    if total == 0:
        return None, None
    for k in CLOSE_KERNELS:
        closed = fg if k == 1 else cv2.morphologyEx(
            fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
        if n <= 1:
            continue
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = (labels == best).astype(np.uint8)
        if int((fg & mask).sum()) >= MIN_COVERAGE * total:
            return mask, k
    return None, None


def _grade(value, fail, warn, higher_is_better=True) -> str:
    """fail/warn are boundaries: higher-is-better -> fail below `fail`, warn up to `warn`."""
    if higher_is_better:
        return "fail" if value < fail else "warn" if value <= warn else "pass"
    return "fail" if value > fail else "warn" if value >= warn else "pass"


def analyse(off_frames: list[np.ndarray], on_frames: list[np.ndarray], camera_info: dict):
    """Return (report dict, images dict of name -> BGR/gray uint8 image)."""
    h, w = on_frames[0].shape[:2]
    mean_off, mean_on = mean_frame(off_frames), mean_frame(on_frames)
    diff = np.clip(max_channel(mean_on) - max_channel(mean_off), 0, None)

    metrics = {}
    metrics["resolution"] = {"value": [w, h],
                             "status": "warn" if (w < 1280 or h < 720) else "pass"}
    mask, kernel = find_grid_region(diff)
    metrics["grid_found"] = {"value": mask is not None, "status": "pass" if mask is not None else "fail"}

    images = {"mean_all_off": _u8(mean_off), "mean_all_on": _u8(mean_on), "diff": _stretch(diff)}
    if mask is not None:
        pts = cv2.findNonZero(mask)
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(pts)
        short = float(min(rw, rh))
        sel = mask.astype(bool)
        contrast = float(diff[sel].mean())
        on_max = max_channel(mean_on)
        saturated = float((on_max[sel] >= 254.5).mean())
        per_frame = [float(max_channel(f.astype(np.float32))[sel].mean()) for f in on_frames]
        banding = float((max(per_frame) - min(per_frame)) / max(np.mean(per_frame), 1e-6))
        lap = cv2.Laplacian(on_max, cv2.CV_32F)
        sharpness = float(lap[sel].var())
        metrics["box_short_px"] = {"value": round(short, 1), "status": _grade(short, 64, 96)}
        metrics["contrast"] = {"value": round(contrast, 1), "status": _grade(contrast, 25, 40)}
        metrics["saturated_frac"] = {"value": round(saturated, 3),
                                     "status": _grade(saturated, 0.6, 0.3, higher_is_better=False)}
        metrics["banding"] = {"value": round(banding, 3),
                              "status": _grade(banding, 0.25, 0.10, higher_is_better=False)}
        metrics["sharpness"] = {"value": round(sharpness, 1), "status": "reported"}
        x, y, bw, bh = cv2.boundingRect(pts)
        box = {"x": int(x), "y": int(y), "w": int(bw), "h": int(bh)}
        crop = _margin_box(x, y, bw, bh, w, h, 0.5)
        images["roi"] = _upscale(_u8(mean_on)[crop], 4)
        images["band_check"] = np.hstack([_upscale(f[crop], 2) for f in on_frames])
        region = {"box": box, "min_area_rect": {"centre": [round(cx, 1), round(cy, 1)],
                                                 "size": [round(rw, 1), round(rh, 1)],
                                                 "angle": round(angle, 1)},
                  "close_kernel": kernel, "banding_frame_means": [round(v, 1) for v in per_frame]}
    else:
        for name in ("box_short_px", "contrast", "saturated_frac", "banding", "sharpness"):
            metrics[name] = {"value": None, "status": "n/a"}
        region = None
    locked = bool(camera_info.get("exposure_locked"))
    metrics["exposure_locked"] = {"value": locked, "status": "reported" if locked else "warn"}

    statuses = [m["status"] for m in metrics.values()]
    verdict = "NO_GO" if "fail" in statuses else "GO_WITH_WARNINGS" if "warn" in statuses else "GO"
    report = {"verdict": verdict, "metrics": metrics, "region": region, "note": THRESHOLD_NOTE}
    return report, images


def format_report(report: dict) -> str:
    lines = [f"Feasibility verdict: {report['verdict']}"]
    for name, m in report["metrics"].items():
        lines.append(f"  {name:16s} {str(m['value']):>14s}  {m['status']}")
    if report["region"]:
        lines.append(f"  region box: {report['region']['box']}")
    lines.append(f"  ({report['note']})")
    return "\n".join(lines)


def _u8(img: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def _stretch(img: np.ndarray) -> np.ndarray:
    hi = float(img.max())
    return _u8(img * (255.0 / hi)) if hi > 0 else _u8(img)


def _upscale(img: np.ndarray, k: int) -> np.ndarray:
    return cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)


def _margin_box(x, y, bw, bh, w, h, frac):
    mx, my = int(round(bw * frac)), int(round(bh * frac))
    return (slice(max(0, y - my), min(h, y + bh + my)), slice(max(0, x - mx), min(w, x + bw + mx)))
