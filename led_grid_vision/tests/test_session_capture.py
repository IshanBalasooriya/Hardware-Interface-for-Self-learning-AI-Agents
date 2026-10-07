import ast
import json
from pathlib import Path

import numpy as np
import cv2
import pytest

from vision import patterns as P
from vision.camera import CameraError, ReplayCamera
from vision.fake_camera import FakeCamera
from vision.feasibility import analyse
from vision.session import SessionWriter, latest_session, load_session

ROOT = Path(__file__).resolve().parent.parent


def _write_session(base, items):
    cam = FakeCamera()
    w = SessionWriter(base, cam.info(), 2, 150)
    frames_by_name = {}
    for name, rows in items:
        cam.set_rows(rows)
        frames = cam.grab(2)
        w.add(name, rows, frames)
        frames_by_name[name] = frames
    w.close()
    return w.dir, frames_by_name


def test_session_round_trip(tmp_path):
    items = [("all_off", P.all_off()), ("all_on", P.all_on())]
    sdir, frames = _write_session(tmp_path, items)
    m = load_session(sdir)
    assert m["version"] == 1 and m["intensity"] == 2 and m["settle_ms"] == 150
    assert m["camera"]["source"] == "fake"
    assert [i["dir"] for i in m["items"]] == ["00_all_off", "01_all_on"]
    for item in m["items"]:
        for path, orig in zip(item["paths"], frames[item["name"]]):
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            assert np.array_equal(img, orig)
    assert not list(Path(sdir).glob("*.tmp"))


def test_replay_camera(tmp_path):
    sdir, frames = _write_session(tmp_path, [("all_off", P.all_off()), ("all_on", P.all_on())])
    cam = ReplayCamera(sdir)
    with pytest.raises(CameraError):
        cam.grab(1)
    cam.select(P.all_on())
    got = cam.grab(3)
    assert np.array_equal(got[0], frames["all_on"][0])
    assert np.array_equal(got[2], frames["all_on"][0])  # cycles
    with pytest.raises(CameraError):
        cam.select(P.checker(0))
    assert cam.info()["source"] == "fake"


def test_latest_session(tmp_path):
    assert latest_session(tmp_path) is None
    assert latest_session(tmp_path / "missing") is None
    a, _ = _write_session(tmp_path, [("all_off", P.all_off())])
    b, _ = _write_session(tmp_path, [("all_off", P.all_off())])
    assert a != b
    assert latest_session(tmp_path) == max(a, b, key=lambda p: p.name)


def _feasibility(cam):
    cam.lock_exposure()
    cam.set_rows(P.all_off())
    off = cam.grab(6)
    cam.set_rows(P.all_on())
    on = cam.grab(6)
    report, _ = analyse(off, on, cam.info())
    json.dumps(report)  # serialisable
    return report


def test_feasibility_default_go():
    assert _feasibility(FakeCamera())["verdict"] == "GO"


def test_feasibility_tiny_quad_no_go():
    r = _feasibility(FakeCamera(quad=((600, 300), (640, 300), (600, 340), (640, 340))))
    assert r["verdict"] == "NO_GO"
    assert r["metrics"]["box_short_px"]["status"] == "fail"


def test_feasibility_blocked_no_go():
    r = _feasibility(FakeCamera(blocked=True))
    assert r["verdict"] == "NO_GO"
    assert r["metrics"]["grid_found"]["status"] == "fail"


def test_capture_end_to_end_fake(tmp_path, capsys):
    from scripts import capture

    sdir = capture.main(["--out", str(tmp_path), "--settle-ms", "0"])
    m = load_session(sdir)
    assert len(m["items"]) == 24
    assert [i["name"] for i in m["items"]] == [n for n, _ in P.standard_set()]
    assert all(len(i["frames"]) == 6 for i in m["items"])
    report = json.loads((sdir / "report.json").read_text())
    assert report["verdict"] == "GO"
    for name in ("mean_all_off", "mean_all_on", "diff", "roi", "band_check"):
        assert (sdir / f"{name}.png").is_file()
    assert "Feasibility verdict: GO" in capsys.readouterr().out


def test_vision_does_not_import_link_or_serial():
    for path in (ROOT / "vision").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top in {"config", "vision", "numpy", "cv2"} or top in _stdlib(), (path.name, name)


def _stdlib():
    import sys

    return set(sys.stdlib_module_names)
