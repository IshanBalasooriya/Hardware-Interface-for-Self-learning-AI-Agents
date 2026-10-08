"""scripts.soak on the fake camera and fake link."""

import json

import pytest

import config
from link.serial_link import FakeLink
from scripts import soak
from vision import patterns as P
from vision.calibration import calibrate, save_calibration
from vision.fake_camera import FakeCamera


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(config, "VISION_FLUSH_FRAMES", 0)
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 2)


@pytest.fixture(scope="module")
def cal():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "VISION_FLUSH_FRAMES", 0)
        mp.setattr(config, "VISION_AVG_FRAMES", 2)
        cam = FakeCamera()
        res = calibrate(cam, cam.set_rows, intensity=2, settle_ms=0)
    assert res.ok, (res.reason, res.detail)
    return res.calibration


def _map(status, rows):
    return {"rows": rows, "warnings": [], "vision": {"status": status, "uncertain": sum(r.count("?") for r in rows),
                                                     "read_ms": 100}}


# ---------------------------------------------------------------- frame list

def test_frame_list_deterministic_and_ordered():
    a, _ = soak.frame_list(1, 200)
    b, _ = soak.frame_list(1, 200)
    c, _ = soak.frame_list(2, 200)
    assert a == b and a != c and len(a) == 200
    std = P.standard_set()
    assert a[:24] == std
    by_name = dict(std)
    assert [rows for _, rows in a[24:29]] == [by_name[n] for n in soak.TRANSITIONS]
    assert all(name.startswith("random_") for name, _ in a[29:])
    assert {name.split("_d")[1] for name, _ in a[29:39]} == {str(d) for d in soak.DENSITIES}


def test_frame_list_short():
    items, _ = soak.frame_list(1, 10)
    assert items == P.standard_set()[:10]


# ---------------------------------------------------------------- scoring

def test_tally_scoring():
    t = soak.Tally()
    one = P.single(2, 5)
    assert t.add(one, _map("ok", one)) is None
    assert t.add(one, _map("ok", P.all_on())) == "bad"  # 63 wrong, confident
    shaky = list(one)
    shaky[0] = "??000000"
    assert t.add(one, _map("ok", shaky)) is None  # '?' is not wrong
    assert t.add(one, _map("unreliable", P.single(4, 4))) == "flagged"  # wrong but flagged: not bad
    assert t.add(one, _map("dark", P.all_off())) == "flagged"  # lit cells shown, nothing seen
    assert t.add(P.all_off(), _map("dark", P.all_off())) == "dark"  # correct
    assert t.add(one, _map("camera_error", ["?" * 8] * 8)) == "flagged"
    s = t.summary()
    assert (s["frames"], s["bad_frames"], s["flagged_frames"], s["dark_frames"]) == (7, 1, 3, 1)
    assert s["wrong_cells"] == 63 + 2 + 1 and s["wrong_cells_ok"] == 63
    assert s["uncertain_cells"] == 2 + 64
    assert s["per_cell_wrong"][2][5] == 2 and s["per_cell_wrong"][4][4] == 2  # all_on read + the unreliable one
    assert s["per_cell_uncertain"][0][:2] == [2, 2]
    assert s["read_ms"] == {"min": 100, "median": 100, "p95": 100, "max": 100}


# ---------------------------------------------------------------- end to end

def test_fake_soak_clean(cal, tmp_path):
    cam = FakeCamera()
    report = soak.run_soak(FakeLink(), cam, cal, frames=40, seed=3, settle_ms=0, label="t", debug_dir=tmp_path)
    assert not report.get("aborted")
    assert report["position_start"]["ok"] and report["position_end"]["ok"]
    assert (report["frames"], report["cells"]) == (40, 2560)
    assert report["wrong_cells"] == 0 and report["uncertain_cells"] == 0
    assert report["bad_frames"] == 0 and report["flagged_frames"] == 0
    assert report["dark_frames"] == 2  # standard all_off and the transition all_off
    assert json.loads(json.dumps(report)) == report
    assert report["debug_images"] == []


def test_occluded_cell_shows_in_per_cell_wrong(cal, tmp_path):
    cam = FakeCamera(occluded={(3, 4)})
    report = soak.run_soak(FakeLink(), cam, cal, frames=40, seed=3, settle_ms=0, debug_dir=tmp_path)
    grid = report["per_cell_wrong"]
    assert grid[3][4] > 0
    assert sum(map(sum, grid)) == grid[3][4]
    assert report["debug_images"] and all(p.endswith(".png") for p in report["debug_images"])
    assert len(list(tmp_path.glob("*.png"))) == len(report["debug_images"])


def test_position_failure_aborts(cal):
    from vision.fake_camera import DEFAULT_QUAD

    pitch = (DEFAULT_QUAD[1][0] - DEFAULT_QUAD[0][0]) / 8
    cam = FakeCamera(quad=tuple((x + pitch, y) for x, y in DEFAULT_QUAD))
    report = soak.run_soak(FakeLink(), cam, cal, frames=40, settle_ms=0)
    assert report["aborted"] and "frames" not in report
    assert not report["position_start"]["ok"]


def test_intensity_sweep_restores_default(cal):
    link = FakeLink()
    report = soak.run_soak(link, FakeCamera(), cal, frames=5, settle_ms=0, intensity_sweep=True)
    assert set(report["intensity_sweep"]) == {"00", "08", "0F"}
    assert all(s["frames"] == soak.SWEEP_FRAMES for s in report["intensity_sweep"].values())
    sent = [h for h in link.sent if h.startswith("0A")]
    assert sent == ["0A00", "0A08", "0A0F", f"0A{config.DEFAULT_INTENSITY:02X}"]


def test_soak_script_fake(cal, tmp_path, monkeypatch, capsys):
    path = tmp_path / "cal.json"
    save_calibration(cal, path)
    monkeypatch.setattr(config, "CALIBRATION_FILE", path)
    assert soak.main(["--frames", "30", "--settle-ms", "0", "--label", "x", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "wrong cells      0" in out
    reports = list(tmp_path.glob("soak_*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text())["label"] == "x"


def test_soak_script_missing_calibration(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CALIBRATION_FILE", tmp_path / "none.json")
    assert soak.main(["--out", str(tmp_path)]) == 1
    assert "no calibration" in capsys.readouterr().err
