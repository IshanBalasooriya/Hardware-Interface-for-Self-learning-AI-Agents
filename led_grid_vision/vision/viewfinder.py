"""Viewfinder: the live window the owner watches while a script has the camera open (master 9.1, P9).

Display only. `render_view` is pure and draws on a new canvas; it never changes the frames used
for measurement. The inset is a digital zoom for the owner's eyes; it adds no detail and nothing
outside this module uses it. Auto-lock is a positioning aid, not tracking.
"""

import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import cv2

import config
from vision.fake_camera import FakeCamera
from vision.feasibility import largest_region

MAIN_W, MAIN_H = 960, 540  # main panel: the frame scaled to fit
INSET = 480  # magnified inset, right of the main panel
READOUT_H = 64  # positioning readout band under both panels
BAR_H = 56  # status line under the readout
CANVAS_W, CANVAS_H = MAIN_W + INSET, MAIN_H + READOUT_H + BAR_H
INSET_X, INSET_Y = MAIN_W, (MAIN_H - INSET) // 2

FONT = cv2.FONT_HERSHEY_SIMPLEX
YELLOW, GREEN, AMBER, RED, WHITE, GREY = ((0, 255, 255), (0, 220, 0), (0, 170, 255), (0, 0, 255),
                                          (255, 255, 255), (170, 170, 170))
CELL_COLOURS = {"1": GREEN, "0": GREY, "?": YELLOW, None: (255, 200, 0)}
CORNER_CELLS = {"tl": (0, 0), "tr": (0, 7), "bl": (7, 0), "br": (7, 7)}

LIT_MIN_PEAK = 60  # max-channel peak below this: nothing lit in view
LIT_MIN_LEVEL = 40  # lower bound on the lit threshold (Otsu can go lower in a dark room)
INSET_MARGIN = 1.5  # crop side = box long side x this
LOCK_FRAMES = 5  # boxes averaged for smoothing
LOCK_STABLE_S = 0.5  # a full grid must hold still this long to lock
LOCK_TOLERANCE = 0.10  # allowed centre shift and size change, as a fraction of the short side

AUTO = object()  # render_view(box=AUTO): detect the lit grid in this frame


@dataclass(frozen=True)
class GridBox:
    """A grid outline in image pixels: four points ordered tl, tr, br, bl."""

    points: tuple

    @classmethod
    def from_points(cls, pts) -> "GridBox":
        p = np.asarray(pts, np.float64).reshape(4, 2)
        s, d = p.sum(axis=1), p[:, 1] - p[:, 0]
        ordered = p[[np.argmin(s), np.argmin(d), np.argmax(s), np.argmax(d)]]
        if len({tuple(q) for q in ordered}) < 4:  # degenerate (e.g. axis-aligned ties): sort by angle
            c = p.mean(axis=0)
            ordered = p[np.argsort(np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0]))]
            ordered = np.roll(ordered, -int(np.argmin(ordered.sum(axis=1))), axis=0)
        return cls(tuple((float(x), float(y)) for x, y in ordered))

    @property
    def array(self) -> np.ndarray:
        return np.asarray(self.points, np.float64)

    @property
    def centre(self) -> tuple[float, float]:
        c = self.array.mean(axis=0)
        return float(c[0]), float(c[1])

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        p = self.array
        return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())

    def _sides(self) -> tuple[float, float]:
        p = self.array
        side = lambda a, b: float(np.linalg.norm(p[a] - p[b]))
        return (side(0, 1) + side(3, 2)) / 2, (side(0, 3) + side(1, 2)) / 2

    @property
    def short(self) -> float:
        return min(self._sides())

    @property
    def long(self) -> float:
        return max(self._sides())

    @property
    def pitch(self) -> float:
        return self.short / 8


def _max_channel(img: np.ndarray) -> np.ndarray:
    """Per-pixel max of B, G, R (cv2 is several times faster than ndarray.max(axis=2))."""
    if img.ndim == 2:
        return img
    b, g, r = cv2.split(img)
    return cv2.max(cv2.max(b, g), r)


def find_lit_grid(frame: np.ndarray) -> GridBox | None:
    """Largest bright compact region in the frame (the lit grid), or None. Pure."""
    gray = cv2.GaussianBlur(_max_channel(frame), (0, 0), 1.0)
    if int(gray.max()) < LIT_MIN_PEAK:
        return None
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = (gray > max(otsu, LIT_MIN_LEVEL)).astype(np.uint8)
    pts = cv2.findNonZero(fg)
    if pts is None:
        return None
    # Work only on the area around the lit pixels (pad > largest closing kernel); same result, faster.
    x, y, bw, bh = cv2.boundingRect(pts)
    pad = 32
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(fg.shape[1], x + bw + pad), min(fg.shape[0], y + bh + pad)
    mask, _ = largest_region(fg[y0:y1, x0:x1])
    if mask is None:
        return None
    rect = cv2.minAreaRect(cv2.findNonZero(mask) + np.int32([x0, y0]))
    return GridBox.from_points(cv2.boxPoints(rect))


def calibrated_box(calibration) -> GridBox | None:
    """Grid outline from a calibration: the corner cell centres pushed out by half a pitch (x 8/7)."""
    try:
        c = calibration["centres_px"]
        pts = np.asarray([c[0][0], c[0][7], c[7][7], c[7][0]], np.float64)
    except (KeyError, IndexError, TypeError):
        return None
    mid = pts.mean(axis=0)
    return GridBox.from_points(mid + (pts - mid) * 8 / 7)


def position_readout(box: GridBox | None, frame_shape, sharpness: float | None = None) -> dict:
    """Pixels per LED, in frame, sharpness and the overall line. Measured on the real frame. Pure."""
    h, w = frame_shape[:2]
    if box is None:
        return {"px_per_led": None, "px_colour": RED, "in_frame": False, "sharpness": sharpness,
                "overall": "GRID NOT FOUND", "overall_colour": RED}
    ppl = box.short / 8
    colour = GREEN if ppl >= 12 else AMBER if ppl >= 8 else RED
    x0, y0, x1, y1 = box.bbox
    margin = box.pitch
    in_frame = x0 >= margin and y0 >= margin and x1 <= w - 1 - margin and y1 <= h - 1 - margin
    if not in_frame:
        overall, oc = "GRID CUT OFF", RED
    elif ppl < config.VISION_MIN_PITCH_PX:
        overall, oc = "MOVE CLOSER", RED
    else:
        overall, oc = "READY", GREEN
    return {"px_per_led": round(ppl, 1), "px_colour": colour, "in_frame": bool(in_frame),
            "sharpness": sharpness, "overall": overall, "overall_colour": oc}


class LockTracker:
    """Smooths detected grid boxes, then locks and holds once a full grid is stable. No window."""

    def __init__(self, min_short: float | None = None) -> None:
        self.min_short = 8 * config.VISION_MIN_PITCH_PX if min_short is None else min_short
        self.release()

    def release(self) -> None:
        self.locked = False
        self.box: GridBox | None = None
        self._history: deque = deque(maxlen=LOCK_FRAMES)
        self._stable_since = None

    def update(self, detected: GridBox | None, now: float | None = None) -> GridBox | None:
        if self.locked:
            return self.box
        now = time.monotonic() if now is None else now
        if detected is None:
            self._history.clear()
            self._stable_since = None
            self.box = None
            return None
        prev = self.box
        self._history.append(detected.array)
        self.box = GridBox.from_points(np.mean(self._history, axis=0))
        full = detected.short >= self.min_short
        steady = prev is not None and _close(detected, prev)
        if full and steady:
            if self._stable_since is None:
                self._stable_since = now
            if now - self._stable_since >= LOCK_STABLE_S:
                self.locked = True
        else:
            self._stable_since = now if full else None
        return self.box


def _close(a: GridBox, b: GridBox) -> bool:
    tol = LOCK_TOLERANCE * b.short
    (ax, ay), (bx, by) = a.centre, b.centre
    return np.hypot(ax - bx, ay - by) <= tol and abs(a.short - b.short) <= tol and abs(a.long - b.long) <= tol


def main_geometry(frame_shape) -> tuple[float, int, int]:
    """Scale and offset of the frame inside the main panel."""
    h, w = frame_shape[:2]
    scale = min(MAIN_W / w, MAIN_H / h)
    return scale, (MAIN_W - int(round(w * scale))) // 2, (MAIN_H - int(round(h * scale))) // 2


def inset_rect(frame_shape, centre, zoom) -> tuple[int, int, int, int]:
    """Square source region (x, y, side, side) at a fixed zoom, clamped to the frame."""
    return _square(frame_shape, centre, INSET / zoom)


def _square(frame_shape, centre, side) -> tuple[int, int, int, int]:
    h, w = frame_shape[:2]
    side = max(1, min(int(round(side)), w, h))
    x = min(max(int(round(centre[0] - side / 2)), 0), w - side)
    y = min(max(int(round(centre[1] - side / 2)), 0), h - side)
    return x, y, side, side


def choose_inset(frame_shape, calibration=None, box=None, zoom_centre=None, zoom=None):
    """Inset source: calibrated grid, else manual click, else detected box, else frame centre."""
    zoom = zoom or config.VISION_VIEW_ZOOM
    grid = calibrated_box(calibration) if calibration else None
    if grid is not None:
        return _square(frame_shape, grid.centre, grid.long * INSET_MARGIN)
    if zoom_centre is not None:
        return inset_rect(frame_shape, zoom_centre, zoom)
    if box is not None:
        return _square(frame_shape, box.centre, box.long * INSET_MARGIN)
    h, w = frame_shape[:2]
    return inset_rect(frame_shape, (w / 2, h / 2), zoom)


def sharpness(frame: np.ndarray, rect) -> float:
    x, y, sw, sh = rect
    gray = _max_channel(np.ascontiguousarray(frame[y:y + sh, x:x + sw]))
    return float(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F).var())


def _draw_parts(canvas, parts, origin, scale, thickness) -> None:
    x, y = origin
    for text, colour in parts:
        cv2.putText(canvas, text, (x, y), FONT, scale, colour, thickness, cv2.LINE_AA)
        x += cv2.getTextSize(text, FONT, scale, thickness)[0][0] + 28


def render_view(frame, *, label="", info=None, calibration=None, rows=None,
                zoom_centre=None, zoom=None, box=AUTO, locked=False) -> np.ndarray:
    """Pure: returns a new BGR canvas; `frame` is never modified.

    box=AUTO detects the lit grid in `frame` (when there is no calibration); pass a GridBox
    (e.g. a locked one) or None to override."""
    info = info or {}
    canvas = np.zeros((CANVAS_H, CANVAS_W, 3), np.uint8)
    src = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    h, w = src.shape[:2]

    grid = calibrated_box(calibration) if calibration else None
    if grid is None and not calibration:
        grid = find_lit_grid(src) if box is AUTO else box

    scale, ox, oy = main_geometry(src.shape)
    sw, sh = int(round(w * scale)), int(round(h * scale))
    canvas[oy:oy + sh, ox:ox + sw] = cv2.resize(src, (sw, sh), interpolation=cv2.INTER_AREA)
    to_panel = lambda x, y: (int(round(ox + x * scale)), int(round(oy + y * scale)))

    rect = choose_inset(src.shape, calibration, grid, zoom_centre, zoom)
    x, y, side, _ = rect
    canvas[INSET_Y:INSET_Y + INSET, INSET_X:INSET_X + INSET] = cv2.resize(
        src[y:y + side, x:x + side], (INSET, INSET), interpolation=cv2.INTER_NEAREST)
    cv2.rectangle(canvas, (INSET_X, INSET_Y), (INSET_X + INSET - 1, INSET_Y + INSET - 1), YELLOW, 1)

    sharp = info.get("sharpness")
    if sharp is None:
        sharp = sharpness(src, rect)
    readout = position_readout(grid, src.shape, sharp)

    if calibration and calibration.get("centres_px"):
        radius = max(2, int(round(float(calibration.get("sample_radius_px", 3.0)) * scale)))
        centres = calibration["centres_px"]
        for r in range(8):
            for c in range(8):
                state = rows[r][c] if rows else None
                cv2.circle(canvas, to_panel(*centres[r][c]), radius, CELL_COLOURS.get(state, CELL_COLOURS[None]), 1)
        for name, (r, c) in CORNER_CELLS.items():
            px, py = to_panel(*centres[r][c])
            cv2.putText(canvas, name, (px + radius + 2, py - radius - 2), FONT, 0.5, YELLOW, 1, cv2.LINE_AA)
    elif grid is not None:
        colour = GREEN if readout["overall"] == "READY" else AMBER
        poly = np.int32([to_panel(px, py) for px, py in grid.points])
        cv2.polylines(canvas, [poly], True, colour, 2, cv2.LINE_AA)
        tx, ty = poly[:, 0].min(), max(poly[:, 1].min() - 8, 18)
        cv2.putText(canvas, "LOCKED" if locked else "searching", (int(tx), int(ty)), FONT, 0.7, colour, 2, cv2.LINE_AA)
    if zoom_centre is not None or grid is None:
        cv2.rectangle(canvas, to_panel(x, y), to_panel(x + side, y + side), YELLOW, 1)

    ppl = readout["px_per_led"]
    parts = [(readout["overall"], readout["overall_colour"]),
             (f"px/LED {ppl:.1f}" if ppl is not None else "px/LED -", readout["px_colour"]),
             (f"in frame: {'yes' if readout['in_frame'] else 'no'}", GREEN if readout["in_frame"] else RED),
             (f"sharp {sharp:.0f}", WHITE)]
    if calibration:
        parts.append(("CALIBRATED", GREEN))
    elif grid is not None:
        parts.append(("LOCKED", GREEN) if locked else ("searching (a = re-lock)", AMBER))
    _draw_parts(canvas, parts, (14, MAIN_H + 46), 1.15, 3)

    status = [label] if label else []
    status.append(f"{info.get('width', w)}x{info.get('height', h)}")
    if info.get("fps") is not None:
        status.append(f"{info['fps']:.1f} fps")
    if info.get("status"):
        status.append(f"status {info['status']}")
    cv2.putText(canvas, "   ".join(status), (14, CANVAS_H - 18), FONT, 0.85, WHITE, 2, cv2.LINE_AA)
    return canvas


def _in_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


class Viewfinder:
    def __init__(self, title="LED grid camera", enabled=None, calibration=None, camera=None) -> None:
        if enabled is None:
            enabled = config.VISION_VIEW == "1" and not isinstance(camera, FakeCamera)
        self.enabled = bool(enabled)
        self.title = title
        self.calibration = calibration
        self.zoom = config.VISION_VIEW_ZOOM
        self.zoom_centre = None  # manual centre from a click
        self.tracker = LockTracker()
        self.quit_requested = False
        self._window = False
        self._warned = False
        self._frame_shape = None
        self._last_t = None
        self._fps = None

    @property
    def active(self) -> bool:
        """Enabled and allowed to open a window (never under pytest)."""
        return self.enabled and not _in_pytest()

    def release(self) -> None:
        """Drop the lock and any manual centre; search for the lit grid again."""
        self.tracker.release()
        self.zoom_centre = None

    def update(self, frame, label="", rows=None, status=None) -> None:
        if not self.active:
            return
        try:
            self._frame_shape = frame.shape
            self._ensure_window()
            now = time.monotonic()
            if self._last_t is not None and now > self._last_t:
                inst = 1.0 / (now - self._last_t)
                self._fps = inst if self._fps is None else 0.8 * self._fps + 0.2 * inst
            self._last_t = now
            box = None
            if not self.calibration:
                detected = None if self.tracker.locked else find_lit_grid(frame)
                box = self.tracker.update(detected, now)
            rect = choose_inset(frame.shape, self.calibration, box, self.zoom_centre, self.zoom)
            info = {"width": frame.shape[1], "height": frame.shape[0], "fps": self._fps,
                    "sharpness": sharpness(frame, rect), "status": status}
            view = render_view(frame, label=label, info=info, calibration=self.calibration, rows=rows,
                               zoom_centre=self.zoom_centre, zoom=self.zoom, box=box,
                               locked=self.tracker.locked)
            cv2.imshow(self.title, view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("s"):
                self._snapshot(frame, view)
            elif key == ord("a"):
                self.release()
            elif key in (ord("q"), 27):
                self._quit()
            elif cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1:
                self._quit()  # closed with the window's X
        except Exception as e:  # a window problem must never stop a script
            self._fail(e)

    def idle(self, camera, ms, label="") -> None:
        """Keep showing live frames for `ms`; plain sleep when the viewfinder is off. Frames are discarded."""
        deadline = time.monotonic() + ms / 1000
        while self.active and time.monotonic() < deadline:
            self.update(camera.grab(1)[0], label)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def close(self) -> None:
        if self._window:
            self._window = False
            try:
                cv2.destroyWindow(self.title)
                cv2.waitKey(1)
            except Exception:
                pass

    # internals
    def _ensure_window(self) -> None:
        if self._window:
            return
        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.title, CANVAS_W, CANVAS_H)
        cv2.setMouseCallback(self.title, self._on_mouse)
        self._window = True

    def _on_mouse(self, event, x, y, flags, param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or self._frame_shape is None:
            return
        scale, ox, oy = main_geometry(self._frame_shape)
        h, w = self._frame_shape[:2]
        fx, fy = (x - ox) / scale, (y - oy) / scale
        if 0 <= fx < w and 0 <= fy < h:
            self.zoom_centre = (fx, fy)

    def _snapshot(self, frame, view) -> None:
        try:
            base = Path(config.VISION_DEBUG_DIR)
            base.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for suffix, img in (("raw", frame), ("view", view)):
                ok, buf = cv2.imencode(".png", img)
                if ok:
                    (base / f"snapshot_{stamp}_{suffix}.png").write_bytes(buf.tobytes())
            print(f"viewfinder: snapshot saved to {base / f'snapshot_{stamp}_*.png'}")
        except Exception as e:
            print(f"viewfinder: snapshot failed: {e}")

    def _quit(self) -> None:
        self.quit_requested = True
        self.enabled = False
        self.close()

    def _fail(self, e: Exception) -> None:
        if not self._warned:
            print(f"warning: viewfinder disabled after a window error: {e}")
            self._warned = True
        self.enabled = False
        self.close()
