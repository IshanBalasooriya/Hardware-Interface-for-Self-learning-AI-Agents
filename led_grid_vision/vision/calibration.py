"""Calibration: locate the grid, map all 64 cells, per-cell levels, self-verification (VSTAGE_2).

`show(rows)` is supplied by the caller and returns once the device confirmed the picture; this
module knows nothing about serial links (P1). `calibrate` never raises (P5).
"""

import json
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import cv2

import config
from vision import patterns as P
from vision.camera import CameraError
from vision.feasibility import largest_region
from vision.session import write_json_atomic, write_png

VERSION = 1
N = 8
CHANNELS = ("max", "red", "green", "gray")
REGION_EXPAND = 1.3  # search region = grid rectangle scaled by this
CORNER_MAX_FRAC = 0.25  # a corner blob larger than this share of the grid rectangle is not one LED
SCENE_SCALE = 2.5  # scene-reference ring: corner quad scaled about its centre
SCENE_POINTS = 16
SCENE_MIN_PITCHES = 3.0
OVERLAY_SCALE = 4

MESSAGES = {
    "grid_not_found": "Camera cannot see the lit grid. Check it is in view and the lens is clear",
    "corner_not_found": "One corner LED was not seen. It may be out of frame or behind a wire",
    "corners_degenerate": "Corners are in impossible positions. Reflections or a second light source are likely",
    "grid_too_small": "Grid is too small in the image. Move it closer or raise the resolution",
    "low_contrast": "Lit and unlit look too similar. Reduce room and screen light, or raise intensity",
    "verification_failed": "Mapping does not hold on test patterns. See the listed cells and saved images",
    "camera_error": "Camera problem",
    "device_error": "Board problem",
    "internal_error": "Unexpected error in calibration",
}


@dataclass
class Calibration:
    """Mirrors the calibration file (master section 6.5). Plain Python types only."""

    created: float
    camera: dict
    intensity: int
    corners_px: dict
    homography: list
    centres_px: list
    sample_radius_px: float
    channel: str
    reduce: str
    off_level: list
    on_level: list
    threshold: list
    scene_ref: dict
    geometry: dict
    verification: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)  # stage 3: 36 positions just outside the grid, and their off levels
    version: int = VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        return {"version": d.pop("version"), **d}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(**d)


@dataclass
class CalibrationResult:
    ok: bool
    calibration: Calibration | None = None
    reason: str | None = None
    detail: dict = field(default_factory=dict)


class _DeviceError(Exception):
    pass


class _Fail(Exception):
    def __init__(self, reason: str, **detail) -> None:
        super().__init__(reason)
        self.reason, self.detail = reason, detail


# ---------------------------------------------------------------- frames and sampling

def _channel(frame: np.ndarray, channel: str) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    if channel == "max":
        b, g, r = cv2.split(frame)
        return cv2.max(cv2.max(b, g), r)
    if channel == "red":
        return frame[..., 2]
    if channel == "green":
        return frame[..., 1]
    if channel == "gray":
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"unknown channel {channel!r}; use one of {CHANNELS}")


def reduce_frames(frames, channel=None, reduce=None) -> np.ndarray:
    """One channel per frame, then mean or max across frames. float32, (H, W)."""
    channel = channel or config.VISION_CHANNEL
    reduce = reduce or config.VISION_REDUCE
    stack = np.stack([_channel(f, channel) for f in frames]).astype(np.float32)
    if reduce == "mean":
        return stack.mean(axis=0)
    if reduce == "max":
        return stack.max(axis=0)
    raise ValueError(f"unknown reduce {reduce!r}; use mean or max")


@lru_cache(maxsize=16)
def _disc_offsets(radius: float) -> tuple[np.ndarray, np.ndarray]:
    r = int(np.ceil(radius))
    dy, dx = np.mgrid[-r:r + 1, -r:r + 1]
    sel = dx * dx + dy * dy <= radius * radius
    return dy[sel], dx[sel]


def _disc_means(image: np.ndarray, points, radius: float) -> np.ndarray:
    """Mean of `image` in a disc at each (x, y) point (rounded to the nearest pixel)."""
    h, w = image.shape[:2]
    dy, dx = _disc_offsets(float(radius))
    pts = np.rint(np.asarray(points, np.float64).reshape(-1, 2)).astype(int)
    ys = np.clip(pts[:, 1:2] + dy[None, :], 0, h - 1)
    xs = np.clip(pts[:, 0:1] + dx[None, :], 0, w - 1)
    return image[ys, xs].astype(np.float32).mean(axis=1)


def sample_cells(image: np.ndarray, cal: Calibration) -> np.ndarray:
    """Per cell, the mean of the sample disc at its centre. float32, (8, 8)."""
    return _disc_means(image, cal.centres_px, cal.sample_radius_px).reshape(N, N)


def classify(values, cal: Calibration) -> list[str]:
    """Rows of '0' / '1' / '?' against each cell's threshold with an uncertain band."""
    v = np.asarray(values, np.float64)
    on, off, thr = (np.asarray(a, np.float64) for a in (cal.on_level, cal.off_level, cal.threshold))
    half = config.VISION_UNCERTAIN_BAND * (on - off) / 2
    out = np.where(v >= thr + half, "1", np.where(v <= thr - half, "0", "?"))
    return ["".join(row) for row in out]


# ---------------------------------------------------------------- save / load

def save_calibration(cal: Calibration, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, cal.to_dict())


def load_calibration(path) -> Calibration | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("version") != VERSION:
            return None
        return Calibration.from_dict(d)
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------- calibrate

def _capture(camera, show, rows, settle_ms, viewfinder, label):
    """Show `rows`, wait, flush, grab; returns (reduced image, last raw frame)."""
    try:
        show(rows)
    except Exception as e:
        raise _DeviceError(str(e)) from e
    if hasattr(camera, "select"):
        camera.select(rows)
    if viewfinder is not None:
        viewfinder.idle(camera, settle_ms, label)
    elif settle_ms > 0:
        time.sleep(settle_ms / 1000)
    camera.flush(config.VISION_FLUSH_FRAMES)
    frames = camera.grab(config.VISION_AVG_FRAMES)
    if viewfinder is not None:
        viewfinder.update(frames[-1], label)
    return reduce_frames(frames), frames[-1]


def calibrate(camera, show, *, intensity, settle_ms=None, debug_dir=None, viewfinder=None) -> CalibrationResult:
    """Run the stage 2 calibration. Never raises; failures come back as a reason."""
    settle_ms = config.VISION_SETTLE_MS if settle_ms is None else settle_ms
    state = {}
    try:
        cal = _calibrate(camera, show, intensity, settle_ms, viewfinder, state)
        if debug_dir is not None:
            _debug_images(debug_dir, cal, state)
        return CalibrationResult(True, cal)
    except _Fail as f:
        if debug_dir is not None and "cal" in state:
            _try(lambda: _debug_images(debug_dir, state["cal"], state))
        return CalibrationResult(False, state.get("cal"), f.reason, f.detail)
    except _DeviceError as e:
        return CalibrationResult(False, None, "device_error", {"message": str(e)})
    except CameraError as e:
        return CalibrationResult(False, None, "camera_error", {"message": str(e)})
    except Exception as e:  # P5: nothing propagates into the caller
        return CalibrationResult(False, None, "internal_error", {"message": f"{type(e).__name__}: {e}"})


def _try(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def _calibrate(camera, show, intensity, settle_ms, viewfinder, state) -> Calibration:
    min_sep = config.VISION_MIN_SEPARATION
    step = [0]

    def cap(name, rows):
        step[0] += 1
        return _capture(camera, show, rows, settle_ms, viewfinder, f"calibrate {step[0]} {name}")

    # 1. locate the grid
    off, _ = cap("all_off", P.all_off())
    on, on_raw = cap("all_on", P.all_on())
    state["on_raw"] = on_raw
    diff = np.clip(on - off, 0, None)
    rect, region = _locate(diff)
    state["region"] = region

    # 2. the four corner cells
    rect_area = float(rect[1][0] * rect[1][1])
    corners = {}
    for name in ("tl", "tr", "bl", "br"):
        img, _ = cap(f"corner_{name}", P.corner(name))
        corners[name] = _corner_centroid(np.clip(img - off, 0, None), region, rect_area, name)
    quad = np.float64([corners[k] for k in ("tl", "tr", "br", "bl")])
    _check_quad(quad, corners)

    # 3. homography and centres
    src = np.float32([[0, 0], [7, 0], [0, 7], [7, 7]])
    dst = np.float32([corners[k] for k in ("tl", "tr", "bl", "br")])
    hom = cv2.getPerspectiveTransform(src, dst)
    grid = np.float32([[c, r] for r in range(N) for c in range(N)]).reshape(-1, 1, 2)
    centres = cv2.perspectiveTransform(grid, hom).reshape(N, N, 2).astype(np.float64)
    pitch = _local_pitch(centres)
    min_pitch = float(pitch.min())
    if min_pitch < config.VISION_MIN_PITCH_PX:
        raise _Fail("grid_too_small", min_pitch_px=round(min_pitch, 2),
                    required_px=config.VISION_MIN_PITCH_PX)
    radius = max(1.5, config.VISION_SAMPLE_RADIUS_FRAC * min_pitch)

    cal = Calibration(
        created=time.time(), camera=_plain(camera.info()), intensity=int(intensity),
        corners_px={k: [round(float(v), 2) for v in corners[k]] for k in ("tl", "tr", "bl", "br")},
        homography=[[float(v) for v in row] for row in hom],
        centres_px=[[[round(float(x), 2), round(float(y), 2)] for x, y in row] for row in centres],
        sample_radius_px=round(float(radius), 3), channel=config.VISION_CHANNEL,
        reduce=config.VISION_REDUCE, off_level=[], on_level=[], threshold=[], scene_ref={},
        geometry={"min_pitch_px": round(min_pitch, 2), "orientation": _orientation(corners),
                  "mirrored": _mirrored(quad)})
    state["cal"] = cal

    # 4. levels
    off_level, on_level = sample_cells(off, cal), sample_cells(on, cal)
    cal.off_level = _grid_list(off_level)
    cal.on_level = _grid_list(on_level)
    cal.threshold = _grid_list((on_level + off_level) / 2)
    gap = on_level - off_level
    low = [{"row": r, "col": c, "separation": round(float(gap[r, c]), 1)}
           for r in range(N) for c in range(N) if gap[r, c] < min_sep]
    if low:
        raise _Fail("low_contrast", cells=low, required=min_sep)

    # 5. scene reference, and the guard ring's own off levels (used by the reader's movement check)
    cal.scene_ref = _scene_ref(off, quad, centres, min_pitch, radius)
    cal.guard = _guard_ring(off, hom, radius)

    # 6. self-verification on patterns not used for fitting
    checks = [("checker_0", P.checker(0)), ("checker_1", P.checker(1))]
    checks += [(f"row_{r}", P.row_only(r)) for r in range(N)]
    checks += [(f"col_{c}", P.col_only(c)) for c in range(N)]
    checks += [("all_off", P.all_off()), ("all_on", P.all_on())]
    failures, wrong_total, unsure_total = [], 0, 0
    state["verify_raw"] = {}
    for name, rows in checks:
        img, raw = cap(f"verify {name}", rows)
        values = sample_cells(img, cal)
        observed = classify(values, cal)
        wrong, unsure = [], []
        for r in range(N):
            for c in range(N):
                o, e = observed[r][c], rows[r][c]
                cell = {"row": r, "col": c, "expected": e, "observed": o,
                        "value": round(float(values[r, c]), 1),
                        "threshold": round(float(cal.threshold[r][c]), 1)}
                if o == "?":
                    unsure.append(cell)
                elif o != e:
                    wrong.append(cell)
        wrong_total += len(wrong)
        unsure_total += len(unsure)
        if wrong or unsure:
            failures.append({"pattern": name, "wrong": wrong, "uncertain": unsure})
            state["verify_raw"][name] = (raw, wrong, unsure)
    cal.verification = {"patterns": len(checks), "cells_checked": len(checks) * N * N,
                        "cells_wrong": wrong_total, "cells_uncertain": unsure_total,
                        "passed": wrong_total == 0 and unsure_total == 0}
    if not cal.verification["passed"]:
        raise _Fail("verification_failed", failures=failures, **{k: cal.verification[k] for k in
                    ("cells_checked", "cells_wrong", "cells_uncertain")})
    return cal


def _locate(diff: np.ndarray):
    """Grid rectangle (cv2 RotatedRect) and the search-region mask, or _Fail grid_not_found."""
    d8 = np.clip(cv2.GaussianBlur(diff, (0, 0), 1.0), 0, 255).astype(np.uint8)
    if int(d8.max()) < config.VISION_MIN_SEPARATION:
        raise _Fail("grid_not_found", peak=int(d8.max()))
    _, fg = cv2.threshold(d8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask, _ = largest_region(fg)
    if mask is None:
        raise _Fail("grid_not_found", peak=int(d8.max()))
    lit = (fg & mask).astype(bool)
    level = float(diff[lit].mean())
    if level < config.VISION_MIN_SEPARATION:
        raise _Fail("grid_not_found", mean_diff=round(level, 1))
    rect = cv2.minAreaRect(cv2.findNonZero(mask))
    (cx, cy), (rw, rh), angle = rect
    grown = ((cx, cy), (rw * REGION_EXPAND, rh * REGION_EXPAND), angle)
    region = np.zeros(diff.shape, np.uint8)
    cv2.fillConvexPoly(region, np.int32(np.round(cv2.boxPoints(grown))), 1)
    return rect, region.astype(bool)


def _corner_centroid(d: np.ndarray, region: np.ndarray, rect_area: float, name: str):
    d = np.where(region, d, 0).astype(np.float32)
    peak = float(d.max())
    if peak < config.VISION_MIN_SEPARATION:
        raise _Fail("corner_not_found", corner=name, peak=round(peak, 1))
    blob = (d >= peak / 2).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[best, cv2.CC_STAT_AREA])
    if area > CORNER_MAX_FRAC * rect_area:
        raise _Fail("corner_not_found", corner=name, blob_px=area, limit_px=round(CORNER_MAX_FRAC * rect_area))
    ys, xs = np.nonzero(labels == best)
    w = d[ys, xs].astype(np.float64)
    return float((xs * w).sum() / w.sum()), float((ys * w).sum() / w.sum())


def _check_quad(quad: np.ndarray, corners: dict) -> None:
    """quad is tl, tr, br, bl. Convex, and every side longer than 7 minimum pitches.

    A convex quad that is merely too short reports grid_too_small, not corners_degenerate."""
    edges = np.roll(quad, -1, axis=0) - quad
    nxt = np.roll(edges, -1, axis=0)
    cross = edges[:, 0] * nxt[:, 1] - edges[:, 1] * nxt[:, 0]
    sides = np.linalg.norm(edges, axis=1)
    need = 7 * config.VISION_MIN_PITCH_PX
    detail = {"corners_px": {k: [round(v, 1) for v in p] for k, p in corners.items()},
              "sides_px": [round(float(s), 1) for s in sides], "min_side_px": need}
    if not (np.all(cross > 0) or np.all(cross < 0)):
        raise _Fail("corners_degenerate", **detail)
    if sides.min() < need:
        raise _Fail("grid_too_small", min_pitch_px=round(float(sides.min()) / 7, 2),
                    required_px=config.VISION_MIN_PITCH_PX, **detail)


def _local_pitch(centres: np.ndarray) -> np.ndarray:
    """Per cell, the smaller of the distances to its nearest horizontal and vertical neighbours."""
    dc = np.linalg.norm(np.diff(centres, axis=1), axis=2)  # (8, 7) along a row
    dr = np.linalg.norm(np.diff(centres, axis=0), axis=2)  # (7, 8) along a column
    inf = np.full((N, 1), np.inf)
    horiz = np.minimum(np.hstack([inf, dc]), np.hstack([dc, inf]))
    vert = np.minimum(np.vstack([inf.T, dr]), np.vstack([dr, inf.T]))
    return np.minimum(horiz, vert)


def _orientation(corners: dict) -> str:
    """Image corner (relative to the four centroids' mean) that grid tl landed nearest."""
    mid = np.mean([corners[k] for k in corners], axis=0)
    dx, dy = np.asarray(corners["tl"]) - mid
    return {(False, False): "rot0", (True, False): "rot90",
            (True, True): "rot180", (False, True): "rot270"}[(bool(dx > 0), bool(dy > 0))]


def _mirrored(quad: np.ndarray) -> bool:
    """tl -> tr -> br winds clockwise in image coordinates (y down) unless mirrored."""
    a, b = quad[1] - quad[0], quad[2] - quad[1]
    return bool(a[0] * b[1] - a[1] * b[0] < 0)


def _scene_ref(off: np.ndarray, quad: np.ndarray, centres: np.ndarray, pitch: float, radius: float) -> dict:
    h, w = off.shape
    mid = quad.mean(axis=0)
    ring = mid + (quad - mid) * SCENE_SCALE
    per_side = SCENE_POINTS // 4
    pts = [ring[i] + (ring[(i + 1) % 4] - ring[i]) * t / per_side for i in range(4) for t in range(per_side)]
    flat = centres.reshape(-1, 2)
    keep = [p for p in pts
            if radius <= p[0] < w - radius and radius <= p[1] < h - radius
            and np.min(np.linalg.norm(flat - p, axis=1)) >= SCENE_MIN_PITCHES * pitch]
    if len(keep) < 4:
        return {"points_px": [], "level": None}
    level = float(_disc_means(off, keep, radius).mean())
    return {"points_px": [[round(float(x), 1), round(float(y), 1)] for x, y in keep], "level": round(level, 2)}


# Grid (col, row) coordinates of the 36 virtual cells just outside the grid: rows -1 and 8, columns -1 and 8.
GUARD_CELLS = ([(c, r) for r in (-1, N) for c in range(-1, N + 1)]
               + [(c, r) for c in (-1, N) for r in range(N)])


def guard_points(hom) -> np.ndarray:
    """Image (x, y) of the 36 guard positions, projected through the grid homography."""
    g = np.float64(GUARD_CELLS).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(g, np.asarray(hom, np.float64)).reshape(-1, 2)


def _guard_ring(off: np.ndarray, hom, radius: float) -> dict:
    """Guard positions and their level in all_off; null level for positions outside the image."""
    h, w = off.shape
    pts = guard_points(hom)
    levels = []
    for x, y in pts:
        inside = radius <= x < w - radius and radius <= y < h - radius
        levels.append(round(float(_disc_means(off, [(x, y)], radius)[0]), 2) if inside else None)
    return {"points_px": [[round(float(x), 2), round(float(y), 2)] for x, y in pts], "off_level": levels}


def _grid_list(a: np.ndarray) -> list:
    return [[round(float(v), 2) for v in row] for row in a]


def _plain(obj):
    """JSON-safe copy (no numpy types)."""
    return json.loads(json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


# ---------------------------------------------------------------- reporting

def separation_stats(cal: Calibration) -> tuple[float, float]:
    gap = np.asarray(cal.on_level) - np.asarray(cal.off_level)
    return float(gap.min()), float(np.median(gap))


def format_summary(result: CalibrationResult) -> str:
    cal = result.calibration
    if result.ok:
        lo, med = separation_stats(cal)
        v, g = cal.verification, cal.geometry
        return "\n".join([
            "Calibration OK",
            f"  min pitch        {g['min_pitch_px']:.2f} px   (minimum {config.VISION_MIN_PITCH_PX})",
            f"  sample radius    {cal.sample_radius_px:.2f} px   channel {cal.channel}",
            f"  orientation      {g['orientation']}   mirrored {g['mirrored']}",
            f"  separation       min {lo:.1f}   median {med:.1f}   (minimum {config.VISION_MIN_SEPARATION})",
            f"  scene ref        {cal.scene_ref.get('level')} from {len(cal.scene_ref.get('points_px', []))} points",
            f"  verification     {v['patterns']} patterns, {v['cells_checked']} cells, "
            f"{v['cells_wrong']} wrong, {v['cells_uncertain']} uncertain",
        ])
    lines = [f"Calibration FAILED: {result.reason}", f"  {MESSAGES.get(result.reason, '')}"]
    detail = dict(result.detail)
    failures = detail.pop("failures", None)
    cells = detail.pop("cells", None)
    for k, v in detail.items():
        lines.append(f"  {k}: {v}")
    if cells:
        lines.append("  cells: " + ", ".join(f"({c['row']},{c['col']}) {c['separation']}" for c in cells))
    for f in failures or []:
        for kind in ("wrong", "uncertain"):
            if f[kind]:
                lines.append(f"  {f['pattern']:10s} {kind:9s} " + ", ".join(
                    f"({c['row']},{c['col']}) exp {c['expected']} got {c['observed']} "
                    f"v {c['value']} thr {c['threshold']}" for c in f[kind]))
    if cal is not None:
        lines.append(f"  min pitch {cal.geometry['min_pitch_px']:.2f} px, orientation "
                     f"{cal.geometry['orientation']}, mirrored {cal.geometry['mirrored']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- debug images

def _crop_box(cal: Calibration, shape) -> tuple[int, int, int, int]:
    pts = np.asarray(cal.centres_px, np.float64).reshape(-1, 2)
    m = 2.5 * max(cal.geometry["min_pitch_px"], 4.0) + 12
    h, w = shape[:2]
    x0, y0 = max(0, int(pts[:, 0].min() - m)), max(0, int(pts[:, 1].min() - m))
    x1, y1 = min(w, int(pts[:, 0].max() + m) + 1), min(h, int(pts[:, 1].max() + m) + 1)
    return x0, y0, x1, y1


def draw_overlay(frame: np.ndarray, cal: Calibration, marks=None) -> np.ndarray:
    """Crop around the grid, upscaled, with every sample disc, corner labels and row/col indices.

    `marks` maps (row, col) -> BGR colour for cells to circle (verification failures)."""
    k = OVERLAY_SCALE
    x0, y0, x1, y1 = _crop_box(cal, frame.shape)
    img = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    out = cv2.resize(img[y0:y1, x0:x1], None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    c = np.asarray(cal.centres_px, np.float64)
    to = lambda p: (int(round((p[0] - x0 + 0.5) * k - 0.5)), int(round((p[1] - y0 + 0.5) * k - 0.5)))
    rad = max(2, int(round(cal.sample_radius_px * k)))
    for r in range(N):
        for col in range(N):
            cv2.circle(out, to(c[r, col]), rad, (255, 255, 0), 1, cv2.LINE_AA)
            cv2.circle(out, to(c[r, col]), 1, (255, 255, 0), -1)
    for (r, col), colour in (marks or {}).items():
        cv2.circle(out, to(c[r, col]), rad + 6, colour, 2, cv2.LINE_AA)
    font = cv2.FONT_HERSHEY_SIMPLEX
    put = lambda text, p, colour: cv2.putText(out, text, (p[0] - 6, p[1] + 6), font, 0.5, colour, 1, cv2.LINE_AA)
    for r in range(N):  # row index beyond column 0
        put(str(r), to(c[r, 0] + (c[r, 0] - c[r, 1]) * 1.1), (0, 255, 0))
    for col in range(N):  # column index beyond row 0
        put(str(col), to(c[0, col] + (c[0, col] - c[1, col]) * 1.1), (255, 128, 255))
    for name, (r, col) in P.CORNERS.items():
        p = c[r, col] + (c[r, col] - c.reshape(-1, 2).mean(axis=0)) * 0.12
        put(name, to(p), (0, 255, 255))
    cv2.putText(out, "rows green, cols pink", (6, 18), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def draw_levels(cal: Calibration) -> np.ndarray:
    gap = np.asarray(cal.on_level) - np.asarray(cal.off_level)
    cell = 64
    norm = np.clip(gap / 255.0 * 255, 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(cv2.resize(norm, (N * cell, N * cell), interpolation=cv2.INTER_NEAREST),
                             cv2.COLORMAP_VIRIDIS)
    for r in range(N):
        for c in range(N):
            cv2.putText(heat, f"{gap[r, c]:.0f}", (c * cell + 12, r * cell + 38), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (255, 255, 255) if gap[r, c] < 160 else (0, 0, 0), 1, cv2.LINE_AA)
    return heat


def _debug_images(debug_dir, cal: Calibration, state: dict) -> None:
    d = Path(debug_dir)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("verify_*.png"):  # from an earlier run; would be mistaken for this one's
        old.unlink()
    if "on_raw" in state:
        write_png(d / "calib_overlay.png", draw_overlay(state["on_raw"], cal))
    if cal.on_level:
        write_png(d / "calib_levels.png", draw_levels(cal))
    for name, (raw, wrong, unsure) in state.get("verify_raw", {}).items():
        marks = {(c["row"], c["col"]): (0, 0, 255) for c in wrong}
        marks.update({(c["row"], c["col"]): (0, 255, 255) for c in unsure})
        write_png(d / f"verify_{name}.png", draw_overlay(raw, cal, marks))
