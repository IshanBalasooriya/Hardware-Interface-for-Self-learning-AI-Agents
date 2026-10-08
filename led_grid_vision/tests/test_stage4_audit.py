"""scripts.stage4_audit (parsing, gate pre-check, appendix) and the runner's audit-file collection."""

import json
import sys
from pathlib import Path

import pytest

import config
from scripts import stage4_audit as A
from scripts import stage4_run as S

SAMPLES = json.loads((Path(__file__).parent / "data" / "stage4_samples.json").read_text(encoding="utf-8"))
STEP = {s.n: s for s in S.STEPS}


def read_line(n=1, status="ok", diff=0, warnings="[]", unc=0):
    return f"read {n}: status {status}   uncertain {unc}   warnings {warnings}   read_ms 500   differences {diff}"


def pos_line(ok=True, shift=0.4, reason=None):
    detail = json.dumps({"reason": reason} if reason else {"tolerance_px": 1.6})
    return f"position check: {'OK' if ok else 'FAILED'}   max corner shift {shift} px   {detail}"


def run(kind, output, code=0, label=""):
    return S.CmdResult("cmd", kind, "t0", "t1", code, output, label)


def step(n, *runs, notes=()):
    sr = S.StepResult(STEP[n])
    sr.runs = list(runs)
    sr.notes = list(notes)
    return sr


CAL_OK = SAMPLES["calibrate_ok"]["output"]
CAL_BAD = SAMPLES["calibrate_failed"]["output"]


def soak(wrong=0, unc=0, flagged=0, frames=200, med=526, p95=548, end=0.6):
    return {"frames": frames, "wrong_cells": wrong, "uncertain_cells": unc, "flagged_frames": flagged,
            "bad_frames": 0, "read_ms": {"min": 480, "median": med, "p95": p95, "max": 600},
            "position_start_px": 0.3, "position_end_px": end, "warnings": {}}


CAL = {"geometry": {"min_pitch_px": 10.5}}


def good_results():
    five = lambda status="ok", diff=0, w="[]": "\n".join(read_line(i, status, diff, w) for i in range(1, 6))
    return [
        step(1, run("calibrate", CAL_OK)), step(2, run("calibrate", CAL_OK)), step(3, run("calibrate", CAL_OK)),
        step(9, run("read", five())),
        step(10, run("read", five("dark", 64), code=1)),
        step(11, run("read", five())), step(12, run("read", five("unreliable", 3, "['lighting_changed']"), code=1)),
        step(13, run("read", five())),
        step(14, run("read", pos_line() + "\n" + read_line())),
        step(15, run("read", pos_line(False, 18.9) + "\n" + read_line(1, "unreliable", 20, "['grid_moved']"), code=1)),
        step(16, run("read", pos_line(False, 50.0) + "\n" + read_line(1, "unreliable", 30, "['grid_moved']"), 1, "attempt 1"),
             run("read", pos_line(False, "None", "grid_not_found") + "\n" + read_line(1, "dark", 32), 1, "attempt 2")),
        step(17, run("calibrate", CAL_OK), run("read", pos_line() + "\n" + read_line())),
    ]


def good_soaks():
    return {"main": soak(), "seed2": soak(), "after10min": soak(frames=100, end=1.5), "intensity": soak(frames=40)}


def by_id(rows):
    return {r["id"]: r for r in rows}


# ---------------------------------------------------------------- parsing

def test_parse_reads_and_position():
    reads = A.parse_reads(SAMPLES["read_pos_moved"]["output"])
    assert reads == [{"n": 1, "status": "unreliable", "uncertain": 0, "warnings": ["grid_moved"], "read_ms": 82,
                      "differences": 32}]
    assert len(A.parse_reads(SAMPLES["read_ok"]["output"])) == 5
    assert A.parse_position(SAMPLES["read_pos_moved"]["output"]) == {"ok": False, "shift_px": 6.89, "reason": None}
    assert A.parse_position(SAMPLES["read_pos_not_found"]["output"]) == {"ok": False, "shift_px": None,
                                                                         "reason": "grid_not_found"}
    assert A.parse_position(SAMPLES["read_ok"]["output"]) is None
    assert A.parse_position(SAMPLES["soak_ok"]["output"], "position end")["shift_px"] == 0.14


def test_report_path_and_debug_images():
    assert A.report_path(SAMPLES["soak_ok"]["output"]).endswith("soak_20261008_131916.json")
    assert A.report_path(SAMPLES["soak_aborted"]["output"]) is None
    imgs = A.debug_images(SAMPLES["read_dark_blocked"]["output"])
    assert len(imgs) == 1 and imgs[0].endswith(".png")
    assert A.debug_images(SAMPLES["read_ok"]["output"]) == []


# ---------------------------------------------------------------- gate pre-check

def test_precheck_all_good():
    rows = by_id(A.gate_precheck(good_results(), good_soaks(), CAL, {"half": "nearest the trackpad"}))
    assert [r for r in rows] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G7b", "G8", "G9"]
    assert {k: v["auto"] for k, v in rows.items()} == {
        "G1": "pass", "G2": "pass", "G3": "pass", "G4": "pass", "G5": "pass", "G6": "needs review",
        "G7": "pass", "G7b": "pass", "G8": "pass", "G9": "pass"}
    assert "nearest the trackpad" in rows["G6"]["found"]
    assert "0.143 pitch" in rows["G4"]["found"]  # 1.5 / 10.5


def test_precheck_failures():
    res = good_results()
    res[0] = step(1, run("calibrate", CAL_BAD, 1), run("calibrate", CAL_OK, 0, "attempt 2"))
    soaks = good_soaks()
    soaks["main"] = soak(wrong=1)
    soaks["seed2"] = soak(unc=27)
    soaks["after10min"] = soak(end=2.2)  # 2.2 / 10.5 = 0.21 pitch
    soaks["intensity"] = soak(med=900)  # not part of G5
    rows = by_id(A.gate_precheck(res, soaks, CAL))
    assert rows["G1"]["auto"] == "fail" and "2 attempts" in rows["G1"]["found"]
    assert rows["G2"]["auto"] == "fail" and rows["G3"]["auto"] == "fail" and rows["G4"]["auto"] == "fail"
    assert rows["G5"]["auto"] == "pass"
    soaks["seed2"] = soak(p95=1300)
    assert by_id(A.gate_precheck(res, soaks, CAL))["G5"]["auto"] == "fail"


def test_precheck_confident_wrong_faults():
    res = good_results()
    res[5] = step(11, run("read", read_line(1, "ok", 4)))  # torch: ok with wrong cells
    rows = by_id(A.gate_precheck(res, good_soaks(), CAL))
    assert rows["G7"]["auto"] == "fail"
    res = good_results()
    res[4] = step(10, run("read", read_line(1, "unreliable", 64, "['lighting_changed']"), 1))  # not dark, not ok
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G7"]["auto"] == "needs review"


def test_precheck_g7b():
    res = good_results()
    res[9] = step(16, run("read", pos_line(False, "None", "grid_not_found") + "\n" + read_line(1, "ok", 32), 1, "attempt 1"))
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G7b"]["auto"] == "fail"
    res[9] = step(16, *[run("read", pos_line(False, 9.0) + "\n" + read_line(1, "unreliable", 9, "['grid_moved']"), 1,
                            f"attempt {k}") for k in range(1, 6)])
    row = by_id(A.gate_precheck(res, good_soaks(), CAL))["G7b"]
    assert row["auto"] == "needs review" and "not reached in 5 attempts" in row["found"]


def test_precheck_g8_g9():
    res = good_results()
    res[7] = step(13, run("read", read_line(1, "ok", 2)))  # recovery wrong
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G8"]["auto"] == "fail"
    res = good_results()
    res[8] = step(14, run("read", pos_line(False, 3.0) + "\n" + read_line(1, "unreliable", 0, "['grid_moved']"), 1))
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G8"]["auto"] == "pass"  # grid_moved reported
    # press not detected and the read is right: did the press move the grid at all?
    res = [r for r in good_results() if r.step.n != 15] + [step(15, run("read", pos_line() + "\n" + read_line()))]
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G9"]["auto"] == "needs review"
    res = [r for r in good_results() if r.step.n != 17] + [step(17, run("calibrate", CAL_BAD, 1))]
    assert by_id(A.gate_precheck(res, good_soaks(), CAL))["G9"]["auto"] == "fail"


def test_precheck_not_run_and_missing_reports():
    rows = by_id(A.gate_precheck([], {}, None))
    assert all(r["auto"] == "not run" for r in rows.values())
    rows = by_id(A.gate_precheck(good_results(), {"main": None, "seed2": soak(), "after10min": None}, CAL))
    assert rows["G2"]["auto"] == rows["G3"]["auto"] == rows["G4"]["auto"] == "needs review"


# ---------------------------------------------------------------- appendix

def test_appendix_renders(tmp_path):
    rep = soak()
    rep["per_cell_wrong"] = [[0] * 8 for _ in range(8)]
    rep["per_cell_wrong"][3][4] = 2
    rep["intensity_sweep"] = {"00": {"frames": 20, "wrong_cells": 0, "bad_frames": 0, "uncertain_cells": 1,
                                     "flagged_frames": 0, "warnings": {}}}
    cal = {"created": 1.0, "geometry": {"min_pitch_px": 10.5, "orientation": "rot90", "mirrored": False},
           "verification": {"cells_checked": 1280, "cells_wrong": 0, "cells_uncertain": 0},
           "on_level": [[200.0] * 8] * 8, "off_level": [[1.0] * 8] * 8,
           "corners_px": {"tl": [0, 0], "tr": [10, 0], "bl": [0, 10], "br": [10, 10]}}
    moved = dict(cal, corners_px={"tl": [3, 4], "tr": [13, 4], "bl": [3, 14], "br": [13, 14]})
    (tmp_path / "x.json").write_text("{}")
    rows = A.gate_precheck(good_results(), good_soaks(), CAL)
    text = A.appendix(rows, {"main": (4, rep, "step04_soak_main.json"), "seed2": (5, None, "-")},
                      {"step03": ("step03_calibration.json", cal), "step17": ("step17_calibration.json", moved)},
                      tmp_path, env_text="### Environment\n\n(test)")
    for want in ("## Audit appendix", "### Gate pre-check", A.PRECHECK_NOTE, "| G2 |", "step04_soak_main.json",
                 "  0   0   0   0   2   0   0   0", "| 00 | 20 | 0 | 0 | 1 | 0 | {} |", "report JSON not available",
                 "199.0 / 199.0", "corner drift step03 -> step17: max 5.00 px", "`x.json` (2 bytes)"):
        assert want in text, want


# ---------------------------------------------------------------- runner collects the audit files

def _printer(tmp_path, outputs):
    queue = list(outputs)

    def launch(tail):
        text, code = queue.pop(0)
        f = tmp_path / f"out_{len(queue)}.txt"
        f.write_text(text, encoding="utf-8")
        return [sys.executable, "-c", f"import sys; sys.stdout.write(open(r'{f}', encoding='utf-8').read()); sys.exit({code})"]

    return launch


def test_runner_collects_audit_files(tmp_path, monkeypatch):
    debug = tmp_path / "debug"
    debug.mkdir()
    monkeypatch.setattr(config, "VISION_DEBUG_DIR", debug)
    cal_file = tmp_path / "cal.json"
    art = tmp_path / "run"
    # files the "commands" produce
    cal_file.write_text(json.dumps({"created": 5.0, "geometry": {"min_pitch_px": 10.0}}))
    (debug / "calib_overlay.png").write_bytes(b"png")
    (debug / "calib_levels.png").write_bytes(b"png")
    soak_dbg = tmp_path / "soak_x"
    soak_dbg.mkdir()
    (soak_dbg / "frame_003_flagged_dark_row_1.png").write_bytes(b"png")
    report = dict(soak(), label="main", debug_dir=str(soak_dbg), debug_images=["frame_003_flagged_dark_row_1.png"])
    rpath = tmp_path / "soak_1.json"
    rpath.write_text(json.dumps(report))
    img = tmp_path / "read_1.png"
    img.write_bytes(b"png")
    outputs = [(CAL_OK, 0),
               (f"Soak 'main'\n  wrong cells      0   (in ok frames: 0)\n  report           {rpath}\n", 0),
               (f"debug image: {img}\n{read_line(1, 'dark', 64)}\n", 1),
               (f"debug image: {tmp_path / 'missing.png'}\n{read_line()}\n", 1)]
    runner = S.Runner(S.AutoConsole(quiet=True), tmp_path / "run.md", {}, cal_file=cal_file,
                      launcher=_printer(tmp_path, outputs), artifacts_dir=art)
    runner.env_text = "### Environment\n\n(test)"
    runner.save_start_calibration()
    runner.context = S.light_context("on")
    assert runner.run([STEP[3], STEP[4], STEP[10], STEP[13]]) == "done"
    runner.close()
    names = sorted(p.relative_to(art).as_posix() for p in art.rglob("*") if p.is_file())
    assert names == ["start_calibration.json", "step03_calib_levels.png", "step03_calib_overlay.png",
                     "step03_calibration.json", "step04_soak_main.json",
                     "step04_soak_main_debug/frame_003_flagged_dark_row_1.png", "step10_read_1.png"]
    log = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "saved for audit: `step04_soak_main.json`" in log and "debug image not found" in log
    assert "## Audit appendix" in log and "#### Step 4 soak `main`" in log and "step10_read_1.png" in log
    assert "| G7 |" in log


def test_collect_failure_never_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISION_DEBUG_DIR", tmp_path / "no_such_dir")
    rpath = tmp_path / "bad.json"
    rpath.write_text("not json")
    outputs = [(f"  report           {rpath}\n", 0), (read_line(), 0)]
    runner = S.Runner(S.AutoConsole(quiet=True), tmp_path / "run.md", {}, cal_file=tmp_path / "none.json",
                      launcher=_printer(tmp_path, outputs), artifacts_dir=tmp_path / "run")
    runner.env_text = "(test)"
    assert runner.run([STEP[4], STEP[8]]) == "done"
    runner.close()
    log = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "artifact copy failed (step04)" in log and "## Step 8" in log and "## Audit appendix" in log
