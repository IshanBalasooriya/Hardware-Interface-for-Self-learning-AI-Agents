"""scripts.stage4_run: step list, output parsing, summary, step-16 retry, calibration failures, logging.

Commands are never launched against the board or camera here: the end-to-end tests replace the
launcher with a child Python that prints a captured sample output."""

import json
import sys
from pathlib import Path

import pytest

import config
from scripts import stage4_run as S

SAMPLES = json.loads((Path(__file__).parent / "data" / "stage4_samples.json").read_text(encoding="utf-8"))


def out(name):
    return SAMPLES[name]["output"]


def res(name, kind=None, label=""):
    s = SAMPLES[name]
    return S.CmdResult("cmd", kind or name.split("_")[0], "10:00:00", "10:00:01", s["code"], s["output"], label)


# ---------------------------------------------------------------- the step list

def test_seventeen_steps_as_given():
    assert [s.n for s in S.STEPS] == list(range(1, 18))
    cmds = {s.n: s.commands for s in S.STEPS}
    for n in (1, 2, 3):
        assert cmds[n] == [("scripts.calibrate",)]
    assert cmds[4] == [("scripts.soak", "--frames", "200", "--seed", "1", "--label", "main")]
    assert cmds[5] == [("scripts.soak", "--frames", "200", "--seed", "2", "--label", "seed2")]
    assert cmds[6] == [("scripts.soak", "--frames", "100", "--seed", "3", "--label", "after10min")]
    assert cmds[7] == [("scripts.soak", "--frames", "40", "--seed", "4", "--intensity-sweep", "--label", "intensity")]
    for n in range(8, 14):
        assert cmds[n] == [("scripts.read", "--pattern", "all_on", "--repeat", "5")]
    for n in (14, 15, 16):
        assert cmds[n] == [("scripts.read", "--pattern", "checker_0", "--check-position")]
    assert cmds[17] == [("scripts.calibrate",), ("scripts.read", "--pattern", "checker_0", "--check-position")]
    by = {s.n: s for s in S.STEPS}
    assert by[6].wait_s == 600 and by[16].push_retry and not any(s.push_retry for s in S.STEPS if s.n != 16)
    assert by[9].ask[0] == "half" and "card" in by[9].before[0] and "card" in by[9].after[0]
    assert "finger" in by[10].before[0] and "finger" in by[10].after[0]
    assert "torch" in by[11].before[0] and "torch" in by[11].after[0]
    assert "{flip}" in by[12].before[0] and "{back}" in by[12].after[0]
    assert "Tap the table" in by[14].before[0] and "GENTLY" in by[15].before[0]
    assert "Push the lid further" in by[16].before[0] and "lid back" in by[17].before[0]
    # no physical pause where none is needed
    assert all(not by[n].before and not by[n].after for n in (1, 2, 3, 4, 5, 6, 7, 8, 13))


def test_select_steps_from():
    assert [s.n for s in S.select_steps(1)] == list(range(1, 18))
    assert [s.n for s in S.select_steps(14)] == [14, 15, 16, 17]


def test_light_context():
    assert S.light_context("on")["flip"].startswith("OFF") and S.light_context("on")["back"].startswith("ON")
    assert S.light_context("off")["flip"].startswith("ON") and S.light_context("off")["back"].startswith("OFF")


# ---------------------------------------------------------------- environment

def test_child_env_live_strips_fake_vars():
    base = {"SERIAL_PORT": "fake", "CAMERA_SOURCE": "fake", "CALIBRATION_FILE": "x.json", "VISION_VIEW": "0",
            "PATH": "p"}
    env = S.child_env(False, base=base)
    assert not set(S.STRIP_ENV) & set(env) and env["PATH"] == "p" and env["PYTHONUNBUFFERED"] == "1"


def test_child_env_fake_points_at_scratch(tmp_path):
    cal = tmp_path / "fake_calibration.json"
    env = S.child_env(True, cal, base={"CALIBRATION_FILE": str(config.CALIBRATION_FILE)})
    assert env["SERIAL_PORT"] == "fake" and env["CAMERA_SOURCE"] == "fake" and env["VISION_VIEW"] == "0"
    assert env["CALIBRATION_FILE"] == str(cal) != str(config.CALIBRATION_FILE)
    # config resolves an absolute env path to itself, not under the project folder
    assert config.BASE_DIR / env["CALIBRATION_FILE"] == cal


# ---------------------------------------------------------------- parsing

def test_extract_calibrate():
    assert S.extract_key_lines("calibrate", out("calibrate_ok")) == [
        "Calibration OK",
        "min pitch        12.12 px   (minimum 8.0)",
        "separation       min 53.2   median 59.3   (minimum 25.0)",
        "verification     20 patterns, 1280 cells, 0 wrong, 0 uncertain"]
    assert S.extract_key_lines("calibrate", out("calibrate_failed")) == ["Calibration FAILED: grid_not_found"]
    assert S.calibration_ok(res("calibrate_ok")) and not S.calibration_ok(res("calibrate_failed"))
    bad_exit = res("calibrate_ok")
    bad_exit.code = 1
    assert not S.calibration_ok(bad_exit)


def test_extract_soak():
    lines = S.extract_key_lines("soak", out("soak_ok"))
    assert lines[0] == "position start   OK  max corner shift 0.0 px"
    for want in ("frames           30 (1920 cells)", "wrong cells      0   (in ok frames: 0)",
                 "bad frames       0   (wrong cell with status ok)", "uncertain cells  0", "flagged frames   0",
                 "dark frames      2   (all-off pictures read dark: correct)", "warnings         {}",
                 "read_ms          min 58  median 68  p95 82  max 93",
                 "position end     OK  max corner shift 0.14 px"):
        assert want in lines, want
    assert lines[-1].startswith("report")
    assert not any("serial port" in ln or "corners_px" in ln for ln in lines)
    sweep = S.extract_key_lines("soak", out("soak_sweep"))
    assert [ln[:12] for ln in sweep if ln.startswith("intensity")] == ["intensity 00", "intensity 08", "intensity 0F"]
    aborted = S.extract_key_lines("soak", out("soak_aborted"))
    assert aborted == ["position start   FAILED  max corner shift 6.89 px",
                       "ABORTED: position check failed at the start. Recalibrate, or run scripts.read --check-position to see why"]


def test_extract_read():
    ok = S.extract_key_lines("read", out("read_ok"))
    assert ok[0].startswith("read 1: status ok") and len([ln for ln in ok if ln.startswith("read ")]) == 5
    assert "identical to read 1: 5/5" in ok and "equal to expected:   5/5" in ok
    assert "read_ms median 79   max 400" in ok
    assert not any(ln.startswith(("expected", "11111111", "movement")) for ln in ok)
    dark = S.extract_key_lines("read", out("read_dark_blocked"))
    assert dark[0].startswith("debug image:")
    assert any(ln.startswith("read 1: status dark") for ln in dark)
    assert any(ln.startswith("dark: no lit LED") for ln in dark) and any(ln.startswith("GRID NOT VISIBLE") for ln in dark)
    nf = S.extract_key_lines("read", out("read_pos_not_found"))
    assert nf[0] == "position check: FAILED   max corner shift None px   reason grid_not_found"
    moved = S.extract_key_lines("read", out("read_pos_moved"))
    assert moved[0] == "position check: FAILED   max corner shift 6.89 px"
    assert any("status unreliable" in ln and "grid_moved" in ln for ln in moved)


def test_extract_fallback_and_trim():
    assert S.extract_key_lines("read", "Traceback (most recent call last):\n  boom\nValueError: x\n") == ["ValueError: x"]
    assert S.extract_key_lines("soak", "") == ["(no output)"]
    assert len(S._trim("read 1: " + "x" * 400)) == S.LINE_MAX


def test_grid_not_found_detection():
    assert S.grid_not_found(out("read_pos_not_found"))
    assert not S.grid_not_found(out("read_pos_moved"))
    assert not S.grid_not_found(out("calibrate_failed"))  # only the position check line counts


# ---------------------------------------------------------------- step 16 retry

class Seq:
    def __init__(self, names):
        self.names, self.calls = list(names), []

    def __call__(self, attempt):
        self.calls.append(attempt)
        return res(self.names[attempt - 1], "read")


@pytest.mark.parametrize("names, runs, reached, pauses", [
    (["read_pos_not_found"], 1, True, 0),
    (["read_pos_moved", "read_ok", "read_pos_not_found"], 3, True, 2),
    (["read_pos_moved"] * 5, 5, False, 4),
])
def test_run_until_not_found(names, runs, reached, pauses):
    con = S.AutoConsole(quiet=True)
    run_once = Seq(names)
    results, ok = S.run_until_not_found(run_once, con)
    assert (len(results), ok, len(con.pauses), run_once.calls) == (runs, reached, pauses, list(range(1, runs + 1)))
    assert all("push" in p.lower() for p in con.pauses)
    assert con.beeps == pauses


def test_run_until_not_found_never_more_than_five():
    results, ok = S.run_until_not_found(Seq(["read_ok"] * 9), S.AutoConsole(quiet=True))
    assert len(results) == S.MAX_PUSH_ATTEMPTS == 5 and not ok


# ---------------------------------------------------------------- runner end to end (sample-printing children)

def _launcher(tmp_path, plan):
    """plan: list of sample names, used in order, one per command launched."""
    queue = list(plan)

    def launch(tail):
        name = queue.pop(0)
        f = tmp_path / f"{len(queue)}_{name}.txt"
        f.write_text(SAMPLES[name]["output"], encoding="utf-8")
        code = SAMPLES[name]["code"]
        return [sys.executable, "-c",
                f"import sys; sys.stdout.write(open(r'{f}', encoding='utf-8').read()); sys.exit({code})"]

    return launch, queue


def _runner(tmp_path, plan, answers=None):
    launch, queue = _launcher(tmp_path, plan)
    con = S.AutoConsole(answers, wait_s=0, quiet=True)
    log = tmp_path / "run.md"
    return S.Runner(con, log, S.child_env(False), launcher=launch), con, log, queue


def test_runner_logs_everything(tmp_path):
    steps = [s for s in S.STEPS if s.n in (1, 6, 9, 16)]
    plan = ["calibrate_ok", "soak_ok", "read_ok", "read_pos_moved", "read_pos_not_found"]
    runner, con, log, queue = _runner(tmp_path, plan, {"half": "the half nearest the trackpad"})
    runner.context = S.light_context("on")
    assert runner.run(steps) == "done"
    runner.close()
    assert queue == []
    text = log.read_text(encoding="utf-8")
    for want in ("## Step 1 — Calibrate (1 of 3)", "`.\\.venv\\Scripts\\python.exe -m scripts.calibrate`",
                 "- started ", "exit code 0", "Calibration OK", "## Step 6", "waiting 0 s",
                 "## Step 9", "owner confirmed: Cover HALF", "-> **the half nearest the trackpad**",
                 "owner confirmed: Remove the card", "## Step 16", "attempt 1", "attempt 2",
                 "grid_not_found reached on attempt 2", "## Summary", "Run done."):
        assert want in text, want
    summary = text[text.index("## Summary"):]
    assert "Step  1  Calibrate (1 of 3)   (exit 0)" in summary
    assert "Step 16  Lid pushed until the grid has moved far (grid_not_found)   (exit 1, 1)" in summary
    assert "* answer: the half nearest the trackpad" in summary
    assert "reason grid_not_found" in summary and "wrong cells      0   (in ok frames: 0)" in summary
    assert len(con.pauses) == 4  # card on, card off, push lid, push a bit more


def test_runner_room_light_text(tmp_path):
    runner, con, log, _ = _runner(tmp_path, ["read_ok"])
    runner.context = S.light_context("off")
    runner.run([S.STEPS[11]])
    runner.close()
    assert con.pauses == ["Switch the room light ON (it was OFF at the start).",
                          "Switch the room light back OFF, as at the start."]


@pytest.mark.parametrize("answers, plan, outcome, codes, note", [
    (["retry"], ["calibrate_failed", "calibrate_ok", "soak_ok"], "done", "1, 0", None),
    (["skip"], ["calibrate_failed", "soak_ok"], "done", "1", "skipped"),
    (["abort"], ["calibrate_failed"], "aborted", "1", "aborted"),
])
def test_calibration_failure_choices(tmp_path, answers, plan, outcome, codes, note):
    steps = [S.STEPS[0], S.STEPS[3]]
    runner, con, log, queue = _runner(tmp_path, plan, {"calibration_failed": answers})
    assert runner.run(steps) == outcome
    runner.close()
    assert queue == []
    text = log.read_text(encoding="utf-8")
    assert f"Step  1  Calibrate (1 of 3)   (exit {codes})" in text
    assert ("## Step 4" in text) == (outcome == "done")
    if note:
        assert note in text
    assert "## Summary" in text and f"Run {outcome}." in text


def test_other_failures_continue(tmp_path):
    runner, _, log, queue = _runner(tmp_path, ["read_dark_blocked", "read_ok"])
    assert runner.run([S.STEPS[9], S.STEPS[12]]) == "done"
    runner.close()
    text = log.read_text(encoding="utf-8")
    assert "Step 10  Fault c: finger over the webcam   (exit 1)" in text and "## Step 13" in text


def test_fake_soak_gets_out_dir(tmp_path):
    r = S.Runner(S.AutoConsole(quiet=True), tmp_path / "l.md", {}, fake=True, soak_out=tmp_path / "s")
    assert r._launch_argv(("scripts.soak", "--label", "x"))[-2:] == ["--out", str(tmp_path / "s")]
    assert "--out" not in r._launch_argv(("scripts.read", "--pattern", "all_on"))
    live = S.Runner(S.AutoConsole(quiet=True), tmp_path / "m.md", {})
    assert live._launch_argv(("scripts.soak", "--label", "x")) == [sys.executable, "-m", "scripts.soak", "--label", "x"]
    r.close()
    live.close()


def test_stage4_run_imports_no_hardware():
    import ast

    src = (Path(__file__).resolve().parent.parent / "scripts" / "stage4_run.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert not mods & {"serial", "cv2", "vision", "link"}, mods
