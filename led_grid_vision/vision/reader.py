"""Reader: the camera's current view -> the vision LED map (VSTAGE_3, master section 6.3).

`read` never raises (P5) and never guesses (P3): doubtful cells are '?', and a view that does not
match the calibration (light, levels, position) is reported `unreliable` with a warning.
`check_position(show)` takes a `show(rows)` callable from the caller; this module knows nothing
about serial links (P1).
"""

import time
from dataclasses import dataclass, field, replace

import numpy as np
import cv2

import config
from vision import patterns as P
from vision.calibration import (N, Calibration, _corner_centroid, _disc_means, _Fail, _local_pitch, _locate,
                                classify, guard_points, reduce_frames, sample_cells)
from vision.camera import CameraError
from vision.ledmap import build_led_map, failed_led_map

CENTROID_ITERATIONS = 3
LEVEL_MIN_CELLS = 8  # cells a class ('0' or '1') needs before its level statistics are tested
GUARD_FRAC = 0.5  # guard limit: the position's own off level plus this share of the smallest on-off gap


@dataclass
class PositionResult:
    ok: bool
    max_corner_shift_px: float | None = None
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------- pure checks

def _cell_windows(image: np.ndarray, cal: Calibration):
    """Per cell: the peak value within half a pitch of the calibrated centre, and the offset / local
    pitch of the light above the cell's threshold. The centroid is weighted by (value - threshold)
    and re-centred CENTROID_ITERATIONS times, so a shifted LED is not pulled back toward the old
    centre by the window edge. Offset is inf when nothing in the window is above threshold."""
    centres = np.asarray(cal.centres_px, np.float64)
    pitch = _local_pitch(centres)
    thr = np.asarray(cal.threshold, np.float64)
    peaks, offsets = np.zeros((N, N)), np.full((N, N), np.inf)
    for r in range(N):
        for c in range(N):
            cx, cy = centres[r, c]
            rad = 0.5 * pitch[r, c]
            vals, xs, ys = _window(image, cx, cy, rad)
            if vals.size == 0:
                continue
            peaks[r, c] = vals.max()
            px, py = cx, cy
            for _ in range(CENTROID_ITERATIONS):
                vals, xs, ys = _window(image, px, py, rad)
                wts = np.clip(vals - thr[r, c], 0, None)
                total = wts.sum()
                if total <= 0:
                    break
                px, py = (xs * wts).sum() / total, (ys * wts).sum() / total
            else:
                offsets[r, c] = np.hypot(px - cx, py - cy) / pitch[r, c]
    return peaks, offsets


def _window(image: np.ndarray, cx: float, cy: float, rad: float):
    """Values and pixel coordinates inside the disc of radius `rad` at (cx, cy)."""
    h, w = image.shape[:2]
    x0, x1 = max(0, int(np.floor(cx - rad))), min(w, int(np.ceil(cx + rad)) + 1)
    y0, y1 = max(0, int(np.floor(cy - rad))), min(h, int(np.ceil(cy + rad)) + 1)
    if x1 <= x0 or y1 <= y0:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    disc = (xs - cx) ** 2 + (ys - cy) ** 2 <= rad * rad
    return image[y0:y1, x0:x1][disc].astype(np.float64), xs[disc], ys[disc]


def guard_limits(cal: Calibration) -> tuple[np.ndarray, np.ndarray]:
    """Guard positions (from the calibration's homography) and the level above which each one looks
    like a lit LED: its own all_off level, recorded at calibration, plus GUARD_FRAC of the smallest
    on-off gap. Calibration files from before stage 3 have no guard levels; the brightest cell off
    level is used instead."""
    off, on = np.asarray(cal.off_level, np.float64), np.asarray(cal.on_level, np.float64)
    pts = guard_points(cal.homography)
    base = np.full(len(pts), off.max())
    recorded = (cal.guard or {}).get("off_level") or []
    if len(recorded) == len(pts):
        base = np.array([off.max() if v is None else v for v in recorded], np.float64)
    return pts, base + GUARD_FRAC * (on - off).min()


def movement_metrics(image: np.ndarray, rows: list[str], cal: Calibration) -> dict:
    """Both movement tests on a reduced image. `moved` is True if either fires.

    Centroid test: cells read '1', plus cells whose window holds a peak above their threshold (a
    shifted LED can sit between sample discs and read '0' or '?' in both cells it straddles)."""
    peaks, offsets = _cell_windows(image, cal)
    thr = np.asarray(cal.threshold, np.float64)
    states = np.array([list(r) for r in rows])
    lit = (states == "1") | (peaks > thr)
    n_lit = int(lit.sum())
    median = float(np.median(offsets[lit])) if n_lit else None
    centroid_applied = n_lit >= config.VISION_MOVE_MIN_CELLS
    centroid_moved = bool(centroid_applied and median > config.VISION_MOVE_TOLERANCE_FRAC)

    pts, limits = guard_limits(cal)
    h, w = image.shape[:2]
    rad = cal.sample_radius_px
    inside = (pts[:, 0] >= rad) & (pts[:, 0] < w - rad) & (pts[:, 1] >= rad) & (pts[:, 1] < h - rad)
    guard = _disc_means(image, pts[inside], rad) if inside.any() else np.zeros(0)
    excess = guard - limits[inside]  # > 0: light above that position's limit
    worst = int(np.argmax(excess)) if guard.size else None
    guard_moved = bool(worst is not None and excess[worst] > 0)
    return {"lit_cells": n_lit, "median_offset_frac": None if median is None else round(median, 3),
            "centroid_applied": centroid_applied, "centroid_moved": centroid_moved,
            "guard_points": int(inside.sum()),
            "guard_max": None if worst is None else round(float(guard[worst]), 1),
            "guard_limit": None if worst is None else round(float(limits[inside][worst]), 1),
            "guard_moved": guard_moved,
            "moved": centroid_moved or guard_moved}


def level_metrics(values: np.ndarray, rows: list[str], cal: Calibration) -> dict:
    """Do the levels still match the calibration? Per cell, its distance from the level it was
    classified as, as a share of its on-off gap: (v - off)/gap for '0', (v - on)/gap for '1'.

    A gain or light change moves a whole class; neighbour glow lifts only a few unlit cells. So the
    tests are on class statistics: '0' cells' lower quartile darker than -tol (gain down: truly unlit
    cells fall below their off level) or median brighter than +tol (light up); '1' cells' median
    darker than -tol (gain down). A '1' brighter than calibrated is harmless and not tested. A class
    with fewer than LEVEL_MIN_CELLS cells is not tested (a few covered cells are not a gain change)."""
    on, off = np.asarray(cal.on_level, np.float64), np.asarray(cal.off_level, np.float64)
    gap = np.maximum(on - off, 1e-6)
    states = np.array([list(r) for r in rows])
    v = np.asarray(values, np.float64)
    d0 = ((v - off) / gap)[states == '0']
    d1 = ((v - on) / gap)[states == '1']
    tol = config.VISION_LEVEL_TOLERANCE
    enough0, enough1 = d0.size >= LEVEL_MIN_CELLS, d1.size >= LEVEL_MIN_CELLS
    off_low = float(np.percentile(d0, 25)) if enough0 else None
    off_mid = float(np.median(d0)) if enough0 else None
    on_mid = float(np.median(d1)) if enough1 else None
    changed = ((off_low is not None and (off_low < -tol or off_mid > tol))
               or (on_mid is not None and on_mid < -tol))
    r3 = lambda x: None if x is None else round(x, 3)
    return {'off_low': r3(off_low), 'off_mid': r3(off_mid), 'on_mid': r3(on_mid), 'changed': bool(changed)}


def scene_metrics(image: np.ndarray, cal: Calibration) -> dict:
    ref = cal.scene_ref or {}
    level, pts = ref.get("level"), ref.get("points_px") or []
    if level is None or not pts:
        return {"level": None, "changed": False}
    now = float(_disc_means(image, pts, cal.sample_radius_px).mean())
    delta = abs(now - level)
    changed = delta > config.VISION_SCENE_TOLERANCE * level and delta > config.VISION_SCENE_MIN_DELTA
    return {"level": round(now, 2), "baseline": level, "changed": bool(changed)}


def shifted_calibration(cal: Calibration, m) -> Calibration:
    """The calibration with its geometry moved by the 3x3 image transform `m` (levels unchanged)."""
    m = np.asarray(m, np.float64)
    move = lambda pts: cv2.perspectiveTransform(np.float64(pts).reshape(-1, 1, 2), m).reshape(-1, 2)
    centres = move(cal.centres_px).reshape(N, N, 2)
    corners = {k: [float(v) for v in move([p])[0]] for k, p in cal.corners_px.items()}
    scene = dict(cal.scene_ref)
    if scene.get("points_px"):
        scene["points_px"] = [[float(x), float(y)] for x, y in move(scene["points_px"])]
    hom = m @ np.asarray(cal.homography, np.float64)
    guard = dict(cal.guard or {})
    if guard.get("points_px"):
        guard["points_px"] = [[float(x), float(y)] for x, y in move(guard["points_px"])]
    return replace(cal, guard=guard, centres_px=[[[float(x), float(y)] for x, y in row] for row in centres],
                   corners_px=corners, homography=(hom / hom[2, 2]).tolist(), scene_ref=scene)


# ---------------------------------------------------------------- reader

class GridReader:
    def __init__(self, camera, calibration: Calibration | None, viewfinder=None) -> None:
        self.camera = camera
        self.calibration = calibration
        self.viewfinder = viewfinder
        self.seq = 0
        self.last_values: np.ndarray | None = None
        self.last_frame: np.ndarray | None = None
        self.last_image: np.ndarray | None = None
        self.last_checks: dict = {}
        if viewfinder is not None and calibration is not None:
            viewfinder.calibration = calibration.to_dict()

    def read(self, *, settle_ms=None) -> dict:
        """One read: the vision LED map. Never raises."""
        start = time.perf_counter()
        elapsed = lambda: (time.perf_counter() - start) * 1000
        try:
            return self._read(settle_ms, start, elapsed)
        except Exception as e:  # P5: nothing propagates into the caller
            self.seq += 1
            return failed_led_map(self.seq, time.time(), "camera_error",
                                  [f"internal_error: {type(e).__name__}: {e}"], elapsed())

    def _fail(self, status, warnings, elapsed) -> dict:
        self.seq += 1
        self.last_values, self.last_checks = None, {}
        return failed_led_map(self.seq, time.time(), status, warnings, elapsed())

    def _read(self, settle_ms, start, elapsed) -> dict:
        cal = self.calibration
        if cal is None:
            return self._fail("uncalibrated", [], elapsed)
        settle_ms = config.VISION_SETTLE_MS if settle_ms is None else settle_ms
        vf = self.viewfinder
        try:
            if vf is not None:
                vf.idle(self.camera, settle_ms, "read: settling")
            elif settle_ms > 0:
                time.sleep(settle_ms / 1000)
            self.camera.flush(config.VISION_FLUSH_FRAMES)
            frames = self.camera.grab(config.VISION_AVG_FRAMES)
        except CameraError as e:
            return self._fail("camera_error", [f"camera_error: {e}"], elapsed)
        timestamp = time.time()
        if not frames:
            return self._fail("camera_error", ["camera_error: no frames"], elapsed)
        h, w = frames[-1].shape[:2]
        if (w, h) != (cal.camera.get("width"), cal.camera.get("height")):
            return self._fail("uncalibrated", ["camera_settings_changed"], elapsed)

        image = reduce_frames(frames, cal.channel, cal.reduce)
        values = sample_cells(image, cal)
        rows = classify(values, cal)

        scene = scene_metrics(image, cal)
        levels = level_metrics(values, rows, cal)
        move = movement_metrics(image, rows, cal)
        warnings = []
        if scene["changed"] or levels["changed"]:
            warnings.append("lighting_changed")
        if move["moved"]:
            warnings.append("grid_moved")
        uncertain = sum(r.count("?") for r in rows)
        if uncertain > config.VISION_MAX_UNCERTAIN:
            warnings.append("too_many_uncertain")
        status = "unreliable" if warnings else "ok"

        self.seq += 1
        self.last_values, self.last_frame, self.last_image = values, frames[-1], image
        self.last_checks = {"scene": scene, "levels": levels, "movement": move}
        result = build_led_map(self.seq, timestamp, rows, warnings, status, elapsed())
        if vf is not None:
            vf.update(frames[-1], f"read {self.seq}", rows=rows, status=status)
        return result

    # ------------------------------------------------------------ explicit position check

    def _capture(self, show, rows, settle_ms) -> np.ndarray:
        show(rows)
        if hasattr(self.camera, "select"):
            self.camera.select(rows)
        if self.viewfinder is not None:
            self.viewfinder.idle(self.camera, settle_ms, "check position")
        elif settle_ms > 0:
            time.sleep(settle_ms / 1000)
        self.camera.flush(config.VISION_FLUSH_FRAMES)
        frames = self.camera.grab(config.VISION_AVG_FRAMES)
        if self.viewfinder is not None:
            self.viewfinder.update(frames[-1], "check position")
        return reduce_frames(frames, self.calibration.channel, self.calibration.reduce)

    def check_position(self, show, *, settle_ms=None) -> PositionResult:
        """Show all_off, all_on and the four corners; compare the corner centroids with the calibration.

        Overwrites the picture on the grid; the caller restores it. Never raises."""
        cal = self.calibration
        if cal is None:
            return PositionResult(False, None, {"reason": "uncalibrated"})
        settle_ms = config.VISION_SETTLE_MS if settle_ms is None else settle_ms
        try:
            off = self._capture(show, P.all_off(), settle_ms)
            on = self._capture(show, P.all_on(), settle_ms)
            rect, region = _locate(np.clip(on - off, 0, None))
            area = float(rect[1][0] * rect[1][1])
            found, shifts = {}, {}
            for name in ("tl", "tr", "bl", "br"):
                img = self._capture(show, P.corner(name), settle_ms)
                found[name] = _corner_centroid(np.clip(img - off, 0, None), region, area, name)
                shifts[name] = float(np.hypot(*(np.asarray(found[name]) - np.asarray(cal.corners_px[name]))))
            worst = max(shifts.values())
            tol = config.VISION_MOVE_TOLERANCE_FRAC * float(cal.geometry["min_pitch_px"])
            return PositionResult(worst < tol, round(worst, 2), {
                "tolerance_px": round(tol, 2), "shifts_px": {k: round(v, 2) for k, v in shifts.items()},
                "corners_px": {k: [round(float(v), 2) for v in p] for k, p in found.items()}})
        except _Fail as f:
            return PositionResult(False, None, {"reason": f.reason, **f.detail})
        except Exception as e:
            return PositionResult(False, None, {"reason": "error", "message": f"{type(e).__name__}: {e}"})
