"""Viewfinder tests. No test opens a window: nothing here calls update() on an active viewfinder."""

import time

import numpy as np
import pytest

import config
from link.serial_link import FakeLink
from vision import patterns as P
from vision.fake_camera import FakeCamera
from vision.fake_camera import DEFAULT_QUAD
from vision.viewfinder import (CANVAS_H, CANVAS_W, INSET, INSET_X, INSET_Y, GridBox, LockTracker,
                               Viewfinder, find_lit_grid, inset_rect, position_readout, render_view)

H, W = 720, 1280


def _frame_with_square(x, y, half=6):
    f = np.zeros((H, W, 3), np.uint8)
    f[y - half:y + half, x - half:x + half] = 255
    return f


def _inset(view):
    return view[INSET_Y:INSET_Y + INSET, INSET_X:INSET_X + INSET]


def test_render_view_new_image_input_unchanged():
    frame = FakeCamera(rows=P.all_on()).grab(1)[0]
    before = frame.copy()
    view = render_view(frame, label="capture 2/24 all_on",
                       info={"width": W, "height": H, "fps": 29.7, "sharpness": 123.0})
    assert np.array_equal(frame, before)
    assert view is not frame and not np.shares_memory(view, frame)
    assert view.shape == (CANVAS_H, CANVAS_W, 3) and view.dtype == np.uint8


def test_render_view_no_calibration_no_rows():
    view = render_view(np.zeros((H, W, 3), np.uint8))
    assert view.shape == (CANVAS_H, CANVAS_W, 3)
    small = render_view(np.zeros((480, 640, 3), np.uint8))
    assert small.shape == view.shape


def test_inset_follows_zoom_centre():
    x, y = 300, 200
    frame = _frame_with_square(x, y)
    on = _inset(render_view(frame, zoom_centre=(x, y)))
    off = _inset(render_view(frame, zoom_centre=(1000, 600)))
    c = INSET // 2
    assert on[c - 10:c + 10, c - 10:c + 10].min() == 255
    assert off[2:-2, 2:-2].max() == 0  # ignore the inset border line


def test_inset_defaults_to_frame_centre():
    frame = _frame_with_square(W // 2, H // 2)
    c = INSET // 2
    assert _inset(render_view(frame))[c - 10:c + 10, c - 10:c + 10].min() == 255


def test_inset_rect_clamps():
    side = round(INSET / 6)
    assert inset_rect((H, W, 3), (0, 0), 6) == (0, 0, side, side)
    assert inset_rect((H, W, 3), (W + 50, H + 50), 6) == (W - side, H - side, side, side)
    assert inset_rect((H, W, 3), (640, 360), 6) == (640 - side // 2, 360 - side // 2, side, side)


def test_render_view_with_calibration():
    centres = [[[400 + 10 * c, 300 + 10 * r] for c in range(8)] for r in range(8)]
    cal = {"centres_px": centres, "sample_radius_px": 3.0}
    frame = _frame_with_square(435, 335)  # the grid centre
    before = frame.copy()
    view = render_view(frame, calibration=cal, rows=P.checker(0), zoom_centre=(1000, 600),
                       info={"status": "ok"})
    assert np.array_equal(frame, before)
    c = INSET // 2
    assert _inset(view)[c - 10:c + 10, c - 10:c + 10].min() == 255  # calibration beats zoom_centre
    render_view(frame, calibration=cal, rows=["10?10?10"] * 8)


class _NoGrabCamera:
    def grab(self, n):
        raise AssertionError("idle must not grab when disabled")

    flush = grab


class _FakeClock:
    """Stands in for the `time` module inside vision.viewfinder: sleep advances the clock, no real wait."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_disabled_idle_sleeps_without_camera(monkeypatch):
    import vision.viewfinder as vfmod

    clock = _FakeClock()
    monkeypatch.setattr(vfmod, "time", clock)
    vf = Viewfinder(enabled=False)
    vf.idle(_NoGrabCamera(), 60)
    assert clock.slept == [pytest.approx(0.060)]
    vf.update(np.zeros((H, W, 3), np.uint8))  # no-op
    vf.close()
    vf.close()


def test_default_enabled_rules(monkeypatch):
    monkeypatch.setattr(config, "VISION_VIEW", "1")
    assert Viewfinder(camera=FakeCamera()).enabled is False
    real_like = Viewfinder(camera=object())
    assert real_like.enabled is True
    assert real_like.active is False  # never opens a window under pytest
    monkeypatch.setattr(config, "VISION_VIEW", "0")
    assert Viewfinder(camera=object()).enabled is False
    assert Viewfinder(enabled=True).enabled is True


def test_view_script_light_fake(monkeypatch, capsys):
    from scripts import view

    link = FakeLink()
    monkeypatch.setattr(view, "open_link", lambda: link)
    view.main(["--light"])
    assert link.sent == [config.WAKE_HEX, "01FF02FF03FF04FF05FF06FF07FF08FF", config.CLEAR_HEX]
    assert "viewfinder is off" in capsys.readouterr().out


def test_view_script_without_light_opens_no_link(monkeypatch):
    from scripts import view

    monkeypatch.setattr(view, "open_link", lambda: pytest.fail("link opened without --light"))
    view.main([])


# --- auto-lock: detection and positioning readout ---

TINY_QUAD = ((600, 300), (640, 300), (600, 340), (640, 340))
EDGE_QUAD = ((0, 300), (130, 302), (0, 420), (132, 420))


def _lit(quad=DEFAULT_QUAD, rows=None, **kw):
    return FakeCamera(rows=rows or P.all_on(), quad=quad, **kw).grab(1)[0]


def test_find_lit_grid_matches_quad():
    box = find_lit_grid(_lit())
    assert box is not None
    q = np.float32(DEFAULT_QUAD)
    x0, y0, x1, y1 = box.bbox
    assert abs(x0 - q[:, 0].min()) <= 6 and abs(x1 - q[:, 0].max()) <= 6
    assert abs(y0 - q[:, 1].min()) <= 6 and abs(y1 - q[:, 1].max()) <= 6


def test_find_lit_grid_none_when_nothing_lit():
    assert find_lit_grid(FakeCamera(rows=P.all_on(), blocked=True).grab(1)[0]) is None
    assert find_lit_grid(FakeCamera(rows=P.all_off()).grab(1)[0]) is None


def test_readout_states():
    shape = (H, W, 3)
    ready = position_readout(find_lit_grid(_lit()), shape, 100.0)
    assert ready["overall"] == "READY" and ready["in_frame"] and ready["px_per_led"] >= 12
    tiny = position_readout(find_lit_grid(_lit(TINY_QUAD)), shape)
    assert tiny["overall"] == "MOVE CLOSER" and tiny["in_frame"] and tiny["px_per_led"] < 8
    cut = position_readout(find_lit_grid(_lit(EDGE_QUAD)), shape)
    assert cut["overall"] == "GRID CUT OFF" and not cut["in_frame"]
    assert position_readout(None, shape)["overall"] == "GRID NOT FOUND"


def test_gridbox_point_order():
    box = GridBox.from_points([(10, 110), (110, 10), (10, 10), (110, 110)])
    assert box.points == ((10, 10), (110, 10), (110, 110), (10, 110))
    assert box.short == 100 and box.centre == (60, 60)


# --- lock and hold ---

def _feed(tracker, cam, rows, n, t0, dt=0.1):
    cam.set_rows(rows)
    box = None
    for i in range(n):
        box = tracker.update(find_lit_grid(cam.grab(1)[0]), t0 + i * dt)
    return box, t0 + n * dt


def test_lock_and_hold():
    cam = FakeCamera(seed=5)
    tracker = LockTracker()
    box, t = _feed(tracker, cam, P.all_on(), 12, 0.0)
    assert tracker.locked and box is not None
    held = box
    for rows in (P.all_off(), P.corner("tl"), P.checker(1), P.row_only(3)):
        box, t = _feed(tracker, cam, rows, 3, t)
        assert tracker.locked and box == held  # frozen, not tracking
    tracker.release()
    assert not tracker.locked
    box, t = _feed(tracker, cam, P.all_off(), 2, t)
    assert box is None and not tracker.locked
    box, t = _feed(tracker, cam, P.all_on(), 12, t)
    assert tracker.locked and box is not None


def test_no_lock_on_small_or_moving_grid():
    tracker = LockTracker()
    _feed(tracker, FakeCamera(quad=TINY_QUAD), P.all_on(), 15, 0.0)
    assert not tracker.locked  # below 8 * VISION_MIN_PITCH_PX
    tracker = LockTracker()
    for i in range(15):
        shift = 25 * (i % 2)
        quad = tuple((x + shift, y) for x, y in DEFAULT_QUAD)
        tracker.update(find_lit_grid(_lit(quad)), i * 0.1)
    assert not tracker.locked  # jumps around: never stable


def test_viewfinder_release_resets():
    vf = Viewfinder(enabled=False)
    vf.zoom_centre = (10, 10)
    vf.tracker.locked = True
    vf.release()
    assert vf.zoom_centre is None and not vf.tracker.locked


def test_inset_sized_to_lit_grid():
    frame = _lit()
    before = frame.copy()
    view = render_view(frame)
    assert np.array_equal(frame, before)
    inset = _inset(view)[2:-2, 2:-2]
    bright_cols = np.where((inset[..., 2] > 150).any(axis=0))[0]
    assert bright_cols.max() - bright_cols.min() > 0.5 * INSET  # grid fills the inset, not a speck
    locked_view = render_view(frame, box=find_lit_grid(frame), locked=True)
    assert locked_view.shape == view.shape


# --- the viewfinder never touches the saved frames ---

def test_capture_saves_raw_full_resolution_frames(tmp_path, monkeypatch):
    import cv2
    from scripts import capture
    from vision.session import load_session

    seen = []

    class SpyViewfinder:
        def __init__(self, camera=None, **kw):
            pass

        def idle(self, camera, ms, label=""):
            pass

        def update(self, frame, label="", rows=None):
            seen.append(frame.copy())
            render_view(frame, label=label)  # the real drawing path, on the real frame

        def close(self):
            pass

    monkeypatch.setattr(capture, "Viewfinder", SpyViewfinder)
    sdir = capture.main(["--out", str(tmp_path), "--settle-ms", "0", "--frames", "2"])
    items = load_session(sdir)["items"]
    assert len(seen) == len(items) == 24
    for item, shown in zip(items, seen):
        saved = cv2.imread(item["paths"][-1], cv2.IMREAD_COLOR)
        assert saved.shape == (H, W, 3)
        assert np.array_equal(saved, shown)


def test_preview_state_reference_is_overlay_only():
    """Stage 5 fix: a loaded calibration drawn as a reference never changes the live tracker or readout."""
    from vision.viewfinder import PreviewState

    cam = FakeCamera(rows=P.all_on())
    frame = cam.grab(1)[0]
    centres = [[[600 + 14 * c, 310 + 14 * r] for c in range(8)] for r in range(8)]
    plain, with_ref = PreviewState(), PreviewState()
    with_ref.reference = {"centres_px": centres, "sample_radius_px": 3.0}
    view_a, m_a = plain.step(frame, "live", now=1.0)
    view_b, m_b = with_ref.step(frame, "live", now=1.0)
    assert m_a == m_b and m_b["lock"] == "SEARCHING"
    assert not np.array_equal(view_a, view_b)  # the reference discs are drawn
    view_c, m_c = with_ref.step(frame, "read 1", now=1.1, calibration=with_ref.reference)
    assert m_c["lock"] == "CALIBRATED"  # an operation frame renders calibrated
