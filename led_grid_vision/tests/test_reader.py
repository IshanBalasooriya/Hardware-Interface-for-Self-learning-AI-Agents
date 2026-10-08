"""GridReader on the fake camera. Each calibration is fitted once per module (the fake is deterministic)."""

import ast
import copy
import json
import random
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import config
from vision import patterns as P
from vision.calibration import calibrate, save_calibration
from vision.camera import CameraError
from vision.fake_camera import DEFAULT_QUAD, FakeCamera
from vision.ledmap import compare, rows_to_hex
from vision.calibration import GUARD_CELLS, guard_points
from vision.reader import GridReader
from vision.viewfinder import Viewfinder

KEYS = ["seq", "timestamp", "display", "intensity", "rows", "bytes", "warnings", "source", "vision"]
PITCH_X = (DEFAULT_QUAD[1][0] - DEFAULT_QUAD[0][0]) / 8  # image pitch along a row of the default quad


def _calibrate(avg_frames=2, **kw):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "VISION_FLUSH_FRAMES", 0)
        mp.setattr(config, "VISION_AVG_FRAMES", avg_frames)
        cam = FakeCamera(**kw)
        res = calibrate(cam, cam.set_rows, intensity=2, settle_ms=0)
    assert res.ok, (res.reason, res.detail)
    return res.calibration


@pytest.fixture(scope="module")
def cal():
    return _calibrate()


# A dark room as on the real webcam: unlit LEDs and the scene read close to 0 (a blocked fake frame is 3)
ROOM = dict(ambient=0.0, off_level=3.0)


@pytest.fixture(scope="module")
def room_cal():
    return _calibrate(**ROOM)


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(config, "VISION_FLUSH_FRAMES", 0)
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 2)


def _read(reader, cam, rows):
    cam.set_rows(rows)
    return reader.read(settle_ms=0)


def _shifted(dx):
    return tuple((x + dx, y) for x, y in DEFAULT_QUAD)


def _random_set(n=50):
    return [(f"random_{s}", P.random_frame(random.Random(s), 0.1 + 0.8 * s / (n - 1))) for s in range(n)]


def _assert_clean(m, rows, name=""):
    lit = any("1" in r for r in rows)
    assert m["vision"]["status"] == ("ok" if lit else "dark"), (name, m)
    assert m["rows"] == rows, name
    assert m["bytes"] == rows_to_hex(rows), name
    assert m["warnings"] == [], name


# ---------------------------------------------------------------- correct reads

def test_standard_and_random_patterns(cal):
    cam = FakeCamera()
    reader = GridReader(cam, cal)
    for name, rows in P.standard_set() + _random_set():
        _assert_clean(_read(reader, cam, rows), rows, name)


def test_noise_blur_glow_banding(monkeypatch):
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 6)
    kw = dict(noise_sigma=3.0, blur_sigma=1.3, glow_frac=0.12, banding=0.15, seed=11)
    cal = _calibrate(avg_frames=6, **kw)
    cam = FakeCamera(**kw)
    reader = GridReader(cam, cal)
    for name, rows in P.standard_set() + _random_set(10):
        _assert_clean(_read(reader, cam, rows), rows, name)


def test_object_shape_seq_and_display(cal):
    cam = FakeCamera()
    reader = GridReader(cam, cal)
    a = _read(reader, cam, P.single(3, 4))
    b = _read(reader, cam, P.all_off())
    assert list(a) == KEYS and list(b) == KEYS
    assert json.loads(json.dumps(a)) == a
    assert a["intensity"] is None and a["source"] == "camera"
    assert a["display"] == "on" and b["display"] == "unknown"
    assert (a["vision"]["status"], b["vision"]["status"]) == ("ok", "dark")
    assert (a["seq"], b["seq"]) == (1, 2)
    assert set(a["vision"]) == {"status", "uncertain", "read_ms"} and isinstance(a["vision"]["read_ms"], int)
    assert reader.last_values.shape == (8, 8)


def test_read_ms_includes_settle(cal):
    cam = FakeCamera(rows=P.all_on())
    m = GridReader(cam, cal).read(settle_ms=40)
    assert m["vision"]["read_ms"] >= 40


def test_occluded_cells_read_off(cal):
    cam = FakeCamera(occluded={(2, 3), (5, 6)})
    m = _read(GridReader(cam, cal), cam, P.all_on())
    expected = P.all_on()
    expected[2] = "11101111"
    expected[5] = "11111101"
    assert m["rows"] == expected
    assert m["vision"]["status"] == "ok"


# ---------------------------------------------------------------- dark status (stage 4)

def test_all_off_is_dark_not_ok(cal):
    cam = FakeCamera()
    m = _read(GridReader(cam, cal), cam, P.all_off())
    assert m["vision"]["status"] == "dark" and m["warnings"] == []
    assert m["rows"] == P.all_off() and m["display"] == "unknown"
    assert m["bytes"] == "01000200030004000500060007000800" and m["vision"]["uncertain"] == 0


def test_one_lit_cell_is_ok(cal):
    cam = FakeCamera()
    _assert_clean(_read(GridReader(cam, cal), cam, P.single(3, 4)), P.single(3, 4))


def test_no_lit_cell_is_never_ok(cal):
    cam = FakeCamera()
    reader = GridReader(cam, cal)
    for name, rows in P.standard_set() + _random_set(20):
        m = _read(reader, cam, rows)
        if not any("1" in r for r in m["rows"]):
            assert m["vision"]["status"] != "ok", name


def test_far_shift_lit_pattern_never_ok(cal, room_cal):
    for c in (cal, room_cal):
        kw = {} if c is cal else ROOM
        cam = FakeCamera(quad=_shifted(10 * PITCH_X), **kw)
        reader = GridReader(cam, c)
        for rows in (P.all_on(), P.checker(0), P.row_only(3)):
            m = _read(reader, cam, rows)
            assert m["vision"]["status"] in ("dark", "unreliable"), (m, reader.last_checks)


def test_room_blocked_lit_pattern_is_dark(room_cal):
    cam = FakeCamera(**ROOM)
    _assert_clean(_read(GridReader(cam, room_cal), cam, P.checker(0)), P.checker(0))
    cam = FakeCamera(blocked=True, **ROOM)
    reader = GridReader(cam, room_cal)
    m = _read(reader, cam, P.checker(0))
    assert m["vision"]["status"] == "dark", (m, reader.last_checks)
    assert m["rows"] == P.all_off() and m["display"] == "unknown"


def test_rotation_after_calibration(cal):
    cam = FakeCamera(rotation=90)
    shown = [r if i else "1" * 8 for i, r in enumerate(P.col_only(0))]  # row_0 plus col_0
    lit = np.array([[ch == "1" for ch in r] for r in shown])
    rotated = ["".join("1" if v else "0" for v in row) for row in np.rot90(lit, -1)]  # module turned clockwise
    m = _read(GridReader(cam, cal), cam, shown)
    assert m["rows"] == rotated
    assert m["rows"] != shown


# ---------------------------------------------------------------- movement

def test_half_pitch_shift_dense_is_grid_moved(cal):
    cam = FakeCamera(quad=_shifted(0.5 * PITCH_X))
    reader = GridReader(cam, cal)
    m = _read(reader, cam, P.checker(0))
    assert "grid_moved" in m["warnings"] and m["vision"]["status"] == "unreliable"
    assert reader.last_checks["movement"]["centroid_moved"]


def test_tiny_shift_no_warning(cal):
    cam = FakeCamera(quad=_shifted(0.05 * PITCH_X))
    reader = GridReader(cam, cal)
    for rows in (P.checker(0), P.all_on()):
        _assert_clean(_read(reader, cam, rows), rows)


def test_four_pitch_shift_all_on_guard_ring(cal):
    cam = FakeCamera(quad=_shifted(4 * PITCH_X))
    reader = GridReader(cam, cal)
    m = _read(reader, cam, P.all_on())
    assert "grid_moved" in m["warnings"] and m["vision"]["status"] == "unreliable"
    assert reader.last_checks["movement"]["guard_moved"]


def test_centroid_test_needs_min_cells(cal):
    cam = FakeCamera(quad=_shifted(0.25 * PITCH_X))
    reader = GridReader(cam, cal)
    _read(reader, cam, P.corner("tl"))
    mv = reader.last_checks["movement"]
    assert mv["lit_cells"] < config.VISION_MOVE_MIN_CELLS
    assert mv["median_offset_frac"] > config.VISION_MOVE_TOLERANCE_FRAC  # moved, but too few cells to judge
    assert not mv["centroid_applied"] and not mv["centroid_moved"]


def test_guard_ring_geometry(cal):
    assert len(GUARD_CELLS) == len(set(GUARD_CELLS)) == 36
    pts = guard_points(cal.homography)
    assert np.allclose(np.asarray(cal.guard["points_px"]), pts, atol=0.01)
    assert len(cal.guard["off_level"]) == 36 and all(v is not None for v in cal.guard["off_level"])
    centres = np.asarray(cal.centres_px).reshape(-1, 2)
    nearest = np.min(np.linalg.norm(pts[:, None] - centres[None], axis=2), axis=1)
    assert nearest.min() > 0.8 * cal.geometry["min_pitch_px"]


# ---------------------------------------------------------------- check_position

def test_check_position_unchanged_ok(cal):
    cam = FakeCamera()
    res = GridReader(cam, cal).check_position(cam.set_rows, settle_ms=0)
    assert res.ok, res
    assert res.max_corner_shift_px < 1.5


def test_check_position_shifted_reports_shift(cal):
    cam = FakeCamera(quad=_shifted(0.5 * PITCH_X))
    res = GridReader(cam, cal).check_position(cam.set_rows, settle_ms=0)
    assert not res.ok
    assert res.max_corner_shift_px == pytest.approx(0.5 * PITCH_X, abs=2.0)
    assert set(res.detail["shifts_px"]) == {"tl", "tr", "bl", "br"}


def test_check_position_show_raising_never_propagates(cal):
    def show(rows):
        raise RuntimeError("board gone")

    res = GridReader(FakeCamera(), cal).check_position(show, settle_ms=0)
    assert not res.ok and "board gone" in res.detail["message"]
    assert GridReader(FakeCamera(), None).check_position(show).ok is False


# ---------------------------------------------------------------- lighting, levels, blocked

@pytest.fixture(scope="module")
def dark_cal():
    return _calibrate(ambient=0.0)


@pytest.fixture(scope="module")
def bright_cal():
    return _calibrate(ambient=80.0)


def test_lighting_zero_baseline_absolute_floor(dark_cal):
    assert dark_cal.scene_ref["level"] < 2.0
    cam = FakeCamera(ambient=30.0)
    m = _read(GridReader(cam, dark_cal), cam, P.checker(0))
    assert "lighting_changed" in m["warnings"] and m["vision"]["status"] == "unreliable"
    cam = FakeCamera(ambient=5.0)
    _assert_clean(_read(GridReader(cam, dark_cal), cam, P.checker(0)), P.checker(0))


def test_lighting_nonzero_baseline_needs_both(bright_cal):
    base = bright_cal.scene_ref["level"]
    assert base == pytest.approx(80.0, abs=5.0)
    # +15: above the absolute floor (12) but under 25% of ~80 -> not flagged
    cam = FakeCamera(ambient=95.0)
    _assert_clean(_read(GridReader(cam, bright_cal), cam, P.checker(1)), P.checker(1))
    # +30: above both
    cam = FakeCamera(ambient=110.0)
    m = _read(GridReader(cam, bright_cal), cam, P.checker(1))
    assert "lighting_changed" in m["warnings"]


def test_blocked_view_never_ok_with_lit_cells(cal):
    cam = FakeCamera(blocked=True)
    m = _read(GridReader(cam, cal), cam, P.checker(0))
    assert m["vision"]["status"] in ("dark", "unreliable")
    assert m["rows"] == P.all_off() and m["display"] == "unknown"


def test_gain_sweep_never_confident_wrong():
    # A scene reference well above the noise, as in a lit room. With the fake's weak green contrast
    # (off 30, on ~81) all_on at gain 0.4 equals all_off at gain 1 in every cell; only the scene can tell.
    gcal = _calibrate(ambient=40.0)
    cam = FakeCamera(ambient=40.0)
    reader = GridReader(cam, gcal)
    for gain in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0):
        cam.gain = gain
        for name, rows in P.standard_set() + _random_set(6):
            m = _read(reader, cam, rows)
            if m["vision"]["status"] == "ok":  # '?' cells are allowed; a 0/1 that is wrong is not
                assert compare(rows, m["rows"]) == [], (gain, name, m["rows"], reader.last_checks)
                assert any("1" in r for r in m["rows"]), (gain, name)  # an all-off reading is never ok


def test_too_many_uncertain(cal):
    shaky = replace(cal, threshold=copy.deepcopy(cal.threshold))
    cells = [(0, 0), (1, 2), (3, 3), (4, 6), (6, 1), (7, 7)]  # more than VISION_MAX_UNCERTAIN (4)
    for r, c in cells:
        shaky.threshold[r][c] = cal.off_level[r][c]  # an unlit cell now sits in the middle of the band
    cam = FakeCamera()
    m = _read(GridReader(cam, shaky), cam, P.all_off())
    assert m["vision"]["uncertain"] == len(cells)
    assert m["vision"]["status"] == "unreliable" and "too_many_uncertain" in m["warnings"]
    assert m["bytes"] is None


# ---------------------------------------------------------------- failures never raise

def test_uncalibrated():
    m = GridReader(FakeCamera(), None).read(settle_ms=0)
    assert m["vision"] == {"status": "uncalibrated", "uncertain": 64, "read_ms": m["vision"]["read_ms"]}
    assert m["rows"] == ["????????"] * 8 and m["bytes"] is None and m["display"] == "unknown"


class _Broken:
    def __init__(self, exc):
        self.exc = exc

    def flush(self, n):
        raise self.exc

    def grab(self, n):
        raise self.exc


def test_camera_error(cal):
    m = GridReader(_Broken(CameraError("frame read failed")), cal).read(settle_ms=0)
    assert m["vision"]["status"] == "camera_error" and m["vision"]["uncertain"] == 64
    assert any("frame read failed" in w for w in m["warnings"])


def test_unexpected_error_does_not_raise(cal):
    m = GridReader(_Broken(RuntimeError("boom")), cal).read(settle_ms=0)
    assert m["vision"]["status"] == "camera_error" and any("boom" in w for w in m["warnings"])


def test_frame_size_changed(cal):
    m = GridReader(FakeCamera(size=(640, 480)), cal).read(settle_ms=0)
    assert m["vision"]["status"] == "uncalibrated"
    assert m["warnings"] == ["camera_settings_changed"]


def test_disabled_viewfinder_is_harmless(cal):
    cam = FakeCamera()
    vf = Viewfinder(enabled=False)
    reader = GridReader(cam, cal, viewfinder=vf)
    assert vf.calibration["centres_px"] == cal.centres_px
    _assert_clean(_read(reader, cam, P.checker(1)), P.checker(1))


def test_reader_imports_no_link_or_serial():
    src = (Path(__file__).resolve().parent.parent / "vision" / "reader.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert not any(m.split(".")[0] in ("link", "serial") for m in mods), mods
    assert {m.split(".")[0] for m in mods} <= {"time", "dataclasses", "numpy", "cv2", "config", "vision"}


# ---------------------------------------------------------------- scripts.read in fake mode

def test_read_script_fake(cal, tmp_path, monkeypatch, capsys):
    from scripts import read

    path = tmp_path / "cal.json"
    save_calibration(cal, path)
    monkeypatch.setattr(config, "CALIBRATION_FILE", path)
    monkeypatch.setattr(config, "VISION_DEBUG_DIR", tmp_path / "debug")
    assert read.main(["--pattern", "checker_0", "--repeat", "3", "--settle-ms", "0"]) == 0
    out = capsys.readouterr().out
    assert "identical to read 1: 3/3" in out and "equal to expected:   3/3" in out
    assert read.main(["--hex", "0100026603FF04FF057E063C07180800", "--json", "--settle-ms", "0"]) == 0
    m = json.loads(capsys.readouterr().out)
    assert m["bytes"] == "0100026603FF04FF057E063C07180800" and m["vision"]["status"] == "ok"
    assert not (tmp_path / "debug").exists()


def test_read_script_dark(cal, tmp_path, monkeypatch, capsys):
    from scripts import read

    path = tmp_path / "cal.json"
    save_calibration(cal, path)
    monkeypatch.setattr(config, "CALIBRATION_FILE", path)
    monkeypatch.setattr(config, "VISION_DEBUG_DIR", tmp_path / "debug")
    assert read.main(["--pattern", "all_off", "--settle-ms", "0"]) == 0  # dark is the right answer here
    out = capsys.readouterr().out
    assert "status dark" in out and read.DARK_NOTE in out and read.NOT_VISIBLE not in out
    assert not (tmp_path / "debug").exists()


def test_read_good():
    from scripts.read import read_good

    m = lambda status, rows: {"vision": {"status": status}, "rows": rows}
    assert read_good(m("dark", P.all_off()), P.all_off())
    assert not read_good(m("dark", P.all_off()), P.single(0, 0))
    assert not read_good(m("dark", P.all_off()), None)
    assert read_good(m("ok", P.single(0, 0)), P.single(0, 0))
    assert not read_good(m("unreliable", P.single(0, 0)), P.single(0, 0))


def test_read_script_missing_calibration(tmp_path, monkeypatch, capsys):
    from scripts import read

    monkeypatch.setattr(config, "CALIBRATION_FILE", tmp_path / "none.json")
    assert read.main(["--pattern", "all_on"]) == 1
    assert "no calibration" in capsys.readouterr().err


def test_parse_pattern():
    from scripts.read import parse_pattern

    assert parse_pattern("row_3") == P.row_only(3)
    assert parse_pattern("random:1:0.5") == P.random_frame(random.Random(1), 0.5)
    with pytest.raises(ValueError):
        parse_pattern("glyph_A")
