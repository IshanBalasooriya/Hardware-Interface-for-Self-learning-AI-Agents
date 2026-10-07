import ast
from pathlib import Path

import numpy as np
import cv2
import pytest

import config
from vision import patterns as P
from vision.calibration import (Calibration, calibrate, classify, load_calibration, reduce_frames,
                                sample_cells, save_calibration)
from vision.fake_camera import DEFAULT_QUAD, N, PITCH, FakeCamera


@pytest.fixture(autouse=True)
def fast_capture(monkeypatch):
    """Fewer frames per capture; the fake renders ~20 ms each."""
    monkeypatch.setattr(config, "VISION_FLUSH_FRAMES", 0)
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 2)


def _show(cam):
    return lambda rows: cam.set_rows(rows)


def _run(cam, show=None, **kw):
    return calibrate(cam, show or _show(cam), intensity=2, settle_ms=0, **kw)


def _true_centres(quad):
    side = N * PITCH
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [side, 0], [0, side], [side, side]]), np.float32(quad))
    g = np.float32([[(c + 0.5) * PITCH - 0.5, (r + 0.5) * PITCH - 0.5] for r in range(N) for c in range(N)])
    return cv2.perspectiveTransform(g.reshape(-1, 1, 2), m).reshape(N, N, 2)


def _read(cam, cal, rows):
    cam.set_rows(rows)
    return classify(sample_cells(reduce_frames(cam.grab(2)), cal), cal)


def test_default_fake_ok_and_centres_accurate(tmp_path):
    cam = FakeCamera()
    res = _run(cam, debug_dir=tmp_path)
    assert res.ok, res
    v = res.calibration.verification
    assert v == {"patterns": 20, "cells_checked": 1280, "cells_wrong": 0, "cells_uncertain": 0, "passed": True}
    err = np.linalg.norm(np.asarray(res.calibration.centres_px) - _true_centres(DEFAULT_QUAD), axis=2)
    assert err.max() < 1.5
    assert res.calibration.geometry == {"min_pitch_px": res.calibration.geometry["min_pitch_px"],
                                        "orientation": "rot0", "mirrored": False}
    assert (tmp_path / "calib_overlay.png").is_file() and (tmp_path / "calib_levels.png").is_file()


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotations_and_mirror_identify_cells(rotation, mirrored):
    cam = FakeCamera(rotation=rotation, mirrored=mirrored)
    res = _run(cam)
    assert res.ok, (res.reason, res.detail)
    assert res.calibration.geometry["mirrored"] is mirrored
    assert _read(cam, res.calibration, P.single(2, 5)) == P.single(2, 5)


def test_strong_perspective():
    quad = ((600, 300), (700, 325), (555, 445), (745, 475))
    res = _run(FakeCamera(quad=quad))
    assert res.ok, (res.reason, res.detail)
    err = np.linalg.norm(np.asarray(res.calibration.centres_px) - _true_centres(quad), axis=2)
    assert err.max() < 2.0


def test_noise_blur_glow_banding_together(monkeypatch):
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 6)
    cam = FakeCamera(noise_sigma=4.0, blur_sigma=1.5, glow_frac=0.15, banding=0.2, seed=7)
    res = _run(cam)
    assert res.ok, (res.reason, res.detail)


def test_blocked_view_grid_not_found():
    assert _run(FakeCamera(blocked=True)).reason == "grid_not_found"


def test_tiny_quad_grid_too_small():
    quad = ((620, 330), (680, 330), (620, 390), (680, 390))  # ~7.5 px per LED
    res = _run(FakeCamera(quad=quad, on_level=255.0))  # bright, so it is found but too small
    assert res.reason == "grid_too_small"
    assert res.detail["min_pitch_px"] < config.VISION_MIN_PITCH_PX


def test_low_contrast():
    res = _run(FakeCamera(on_level=45.0, off_level=30.0))
    assert res.reason in ("low_contrast", "grid_not_found")


def test_occluded_corner_named():
    res = _run(FakeCamera(occluded={(0, 7)}))
    assert res.reason == "corner_not_found"
    assert res.detail["corner"] == "tr"


def test_occluded_middle_cell_listed():
    res = _run(FakeCamera(occluded={(3, 4)}))
    assert res.reason == "low_contrast"
    assert [(c["row"], c["col"]) for c in res.detail["cells"]] == [(3, 4)]


def test_bumped_grid_fails_verification(tmp_path):
    cam = FakeCamera()
    calls = [0]

    def show(rows):
        calls[0] += 1
        if calls[0] == 7:  # after all_off, all_on and the four corners: the grid moves
            cam.quad = tuple((x + 9, y) for x, y in DEFAULT_QUAD)
        cam.set_rows(rows)

    res = _run(cam, show, debug_dir=tmp_path)
    assert not res.ok and res.reason == "verification_failed"
    assert res.detail["cells_wrong"] + res.detail["cells_uncertain"] > 0
    assert list(tmp_path.glob("verify_*.png"))


def test_show_raising_is_device_error():
    def show(rows):
        raise RuntimeError("show failed: ERR timeout")

    res = _run(FakeCamera(), show)
    assert not res.ok and res.reason == "device_error"
    assert "ERR timeout" in res.detail["message"]


def test_save_load_roundtrip(tmp_path):
    cal = _run(FakeCamera()).calibration
    path = tmp_path / "cal.json"
    save_calibration(cal, path)
    assert load_calibration(path) == cal


def test_load_missing_corrupt_wrong_version(tmp_path):
    assert load_calibration(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_calibration(bad) is None
    old = tmp_path / "old.json"
    old.write_text('{"version": 0}', encoding="utf-8")
    assert load_calibration(old) is None


def test_reduce_frames_channels():
    f = np.zeros((2, 2, 3), np.uint8)
    f[..., 0], f[..., 1], f[..., 2] = 10, 20, 30
    assert reduce_frames([f], "max", "mean")[0, 0] == 30
    assert reduce_frames([f], "red", "mean")[0, 0] == 30
    assert reduce_frames([f], "green", "mean")[0, 0] == 20
    assert 20 < reduce_frames([f], "gray", "mean")[0, 0] < 30
    g = f.copy()
    g[..., 1] = 40
    assert reduce_frames([f, g], "green", "mean")[0, 0] == 30
    assert reduce_frames([f, g], "green", "max")[0, 0] == 40
    assert reduce_frames([f]).dtype == np.float32


def test_classify_band():
    flat = lambda v: [[v] * N for _ in range(N)]
    cal = Calibration(created=0.0, camera={}, intensity=2, corners_px={}, homography=[], centres_px=[],
                      sample_radius_px=2.0, channel="green", reduce="mean", off_level=flat(0.0),
                      on_level=flat(100.0), threshold=flat(50.0), scene_ref={}, geometry={})
    # band 0.30 of a gap of 100 -> '?' strictly between 35 and 65
    values = np.full((N, N), 50.0)
    values[0, :4] = [65.0, 64.9, 35.1, 35.0]
    rows = classify(values, cal)
    assert rows[0] == "1??0????"
    assert rows[1] == "????????"


def test_calibration_imports_no_link_or_serial():
    src = Path(__file__).resolve().parent.parent / "vision" / "calibration.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not any(n.split(".")[0] in ("link", "serial") for n in names), names
