"""Guided runner for the stage 4 live checks: the 17 steps in order, with pauses for physical actions.

.\\.venv\\Scripts\\python.exe -m scripts.stage4_run [--from N] [--fake]

A wrapper only. Each step runs the same command the owner would type (scripts.calibrate, scripts.soak,
scripts.read), as a child process with SERIAL_PORT, CAMERA_SOURCE, CALIBRATION_FILE and VISION_VIEW
removed from its environment. Output is streamed to the screen and written to
logs/vision/stage4_run_<timestamp>.md with a heading per step, the command, start and end time, exit
code, the full output and every answer given. A summary of the key result lines ends the run.

--from N  start at step N (a new log; it names the calibration file in use)
--fake    fake board and camera, a calibration file in a scratch folder under logs/vision/, every
          prompt answered automatically, the 10-minute wait cut to 3 s. Never touches the live
          calibration file (its SHA256 is checked before and after).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from scripts import stage4_audit as A
from scripts.stage4_audit import calibration_ok, grid_not_found

STRIP_ENV = ("SERIAL_PORT", "CAMERA_SOURCE", "CALIBRATION_FILE", "VISION_VIEW")
LOG_DIR = config.BASE_DIR / "logs" / "vision"
WAIT_S = 600
FAKE_WAIT_S = 3
MAX_PUSH_ATTEMPTS = 5
LINE_MAX = 160

CAL = ("scripts.calibrate",)
READ_ALL_ON = ("scripts.read", "--pattern", "all_on", "--repeat", "5")
READ_POS = ("scripts.read", "--pattern", "checker_0", "--check-position")

CHECKLIST = (
    "The led_grid server and every led_grid script are stopped",
    "No other app is using the webcam (Camera app, browser, Teams, ...)",
    "Screen at minimum brightness, a dark static window in front",
    "The lid is wedged so it cannot sag, and the grid and wires are taped",
)


@dataclass
class Step:
    n: int
    title: str
    commands: list
    before: tuple = ()  # physical actions, each a pause, before the commands
    after: tuple = ()  # physical actions after the commands
    ask: tuple | None = None  # (key, question) asked after `before`
    wait_s: int = 0  # countdown before the commands
    push_retry: bool = False  # step 16: rerun until the position check says grid_not_found


STEPS = [
    Step(1, "Calibrate (1 of 3)", [CAL]),
    Step(2, "Calibrate (2 of 3)", [CAL]),
    Step(3, "Calibrate (3 of 3)", [CAL]),
    Step(4, "Main soak", [("scripts.soak", "--frames", "200", "--seed", "1", "--label", "main")]),
    Step(5, "Second seed soak", [("scripts.soak", "--frames", "200", "--seed", "2", "--label", "seed2")]),
    Step(6, "Drift: 10 minutes untouched, then soak without recalibrating",
         [("scripts.soak", "--frames", "100", "--seed", "3", "--label", "after10min")], wait_s=WAIT_S),
    Step(7, "Intensity sweep",
         [("scripts.soak", "--frames", "40", "--seed", "4", "--intensity-sweep", "--label", "intensity")]),
    Step(8, "Fault a: baseline", [READ_ALL_ON]),
    Step(9, "Fault b: card over half the grid", [READ_ALL_ON],
         before=("Cover HALF of the LED grid with a card (the grid, not the camera). Keep it there.",),
         ask=("half", "Which physical half did you cover? (e.g. 'the half nearest the trackpad')"),
         after=("Remove the card from the grid.",)),
    Step(10, "Fault c: finger over the webcam", [READ_ALL_ON],
         before=("Put a finger over the webcam lens and keep it there.",),
         after=("Remove your finger from the webcam.",)),
    Step(11, "Fault d: phone torch on the grid", [READ_ALL_ON],
         before=("Shine a phone torch on the grid from about 30 cm and hold it there.",),
         after=("Turn the phone torch off.",)),
    Step(12, "Fault e: room light flipped", [READ_ALL_ON],
         before=("Switch the room light {flip}.",),
         after=("Switch the room light back {back}.",)),
    Step(13, "Recovery baseline (no recalibration)", [READ_ALL_ON]),
    Step(14, "Table tap", [READ_POS],
         before=("Tap the table firmly twice, then take your hands away.",)),
    Step(15, "Lid press (gentle)", [READ_POS],
         before=("Press the lid GENTLY and let go. Hands away.",)),
    Step(16, "Lid pushed until the grid has moved far (grid_not_found)", [READ_POS],
         before=("Push the lid further, until the grid has clearly moved in the image. Hands away.",),
         push_retry=True),
    Step(17, "Lid restored, recalibrate, read", [CAL, READ_POS],
         before=("Put the lid back in a steady position and wedge it again. Hands away.",)),
]


# ---------------------------------------------------------------- output parsing

KEY_PREFIXES = {
    "calibrate": ("Calibration OK", "Calibration FAILED", "min pitch", "separation", "verification",
                  "camera_error", "device_error"),
    "soak": ("position start", "ABORTED", "frames ", "wrong cells", "bad frames", "uncertain cells",
             "flagged frames", "dark frames", "warnings", "read_ms", "intensity ", "position end", "report",
             "debug images", "no calibration", "camera_error", "device_error"),
    "read": ("position check:", "read ", "identical to read", "equal to expected", "dark:", "GRID NOT VISIBLE",
             "read_ms median", "debug image:", "no calibration", "bad picture", "camera_error", "device_error"),
}


def kind_of(tail) -> str:
    return tail[0].rsplit(".", 1)[-1]


def _trim(line: str) -> str:
    """Drop the JSON detail of a position line, keeping its reason."""
    if line.startswith("position") and "{" in line:
        head = line[:line.index("{")].rstrip()
        m = re.search(r'"reason": "([^"]+)"', line)
        line = head + (f"   reason {m.group(1)}" if m else "")
    return line if len(line) <= LINE_MAX else line[:LINE_MAX - 3] + "..."


def extract_key_lines(kind: str, output: str) -> list[str]:
    """The result lines worth seeing in the summary. If none match, the last non-empty line."""
    prefixes = KEY_PREFIXES.get(kind, ())
    lines = [ln.strip() for ln in output.splitlines()]
    keep = [_trim(ln) for ln in lines if ln.startswith(prefixes)]
    if not keep:
        last = [ln for ln in lines if ln]
        keep = [_trim(last[-1])] if last else ["(no output)"]
    return keep


# ---------------------------------------------------------------- console

class Console:
    """Talks to the owner. AutoConsole answers by itself."""

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def beep(self) -> None:
        try:
            import winsound
            winsound.Beep(880, 250)
            winsound.Beep(1320, 350)
        except Exception:
            print("\a", end="", flush=True)

    def banner(self, title: str, lines) -> None:
        width = max(60, *(len(s) + 8 for s in [title, *lines]))
        bar = "#" * width
        self.say("\n" + bar + "\n" + bar)
        self.say(f"###   {title.upper()}")
        self.say("###")
        for s in lines:
            self.say(f"###   {s}")
        self.say(bar + "\n" + bar)

    def pause(self, title: str, lines) -> None:
        self.beep()
        self.banner(title, lines)
        input(">>> Press Enter when done ")

    def ask(self, key: str, question: str, choices=None) -> str:
        while True:
            answer = input(f">>> {question}{' [' + '/'.join(choices) + ']' if choices else ''}: ").strip()
            if choices and answer.lower() in choices:
                return answer.lower()
            if not choices and answer:
                return answer
            self.say("    please answer" + (f" one of {', '.join(choices)}" if choices else ""))

    def countdown(self, seconds: int, text: str) -> None:
        self.banner("Wait", [text, f"{seconds // 60} min {seconds % 60} s, continues by itself"])
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            s = int(left + 0.999)
            print(f"\r    {s // 60:02d}:{s % 60:02d}   DO NOT TOUCH ANYTHING   ", end="", flush=True)
            time.sleep(min(1.0, left))
        print("\r    00:00   done" + " " * 30, flush=True)
        self.beep()


class AutoConsole(Console):
    """Answers every prompt by itself (fake mode, tests). `answers[key]` may be a list, used in turn."""

    DEFAULTS = {"light": "on", "half": "fake mode: nothing covered", "calibration_failed": "abort"}

    def __init__(self, answers=None, wait_s=FAKE_WAIT_S, quiet=False) -> None:
        self.answers = {**self.DEFAULTS, **(answers or {})}
        self.wait_s = wait_s
        self.quiet = quiet
        self.pauses: list[str] = []
        self.beeps = 0

    def say(self, text: str = "") -> None:
        if not self.quiet:
            super().say(text)

    def beep(self) -> None:
        self.beeps += 1
        self.say("[beep]")

    def pause(self, title: str, lines) -> None:
        self.beep()
        self.banner(title, lines)
        self.pauses.append(" ".join(lines))
        self.say(">>> [auto] Enter")

    def ask(self, key: str, question: str, choices=None) -> str:
        a = self.answers[key]
        answer = a.pop(0) if isinstance(a, list) else a
        self.say(f">>> {question} [auto] {answer}")
        return answer

    def countdown(self, seconds: int, text: str) -> None:
        super().countdown(min(seconds, self.wait_s), text)


# ---------------------------------------------------------------- running

@dataclass
class CmdResult:
    command: str
    kind: str
    start: str
    end: str
    code: int
    output: str
    label: str = ""
    tail: tuple = ()
    started_at: float = 0.0


@dataclass
class StepResult:
    step: Step
    runs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Abort(Exception):
    pass


def child_env(fake: bool, fake_cal: Path | None = None, base=None) -> dict:
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in STRIP_ENV}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if fake:
        env.update(SERIAL_PORT="fake", CAMERA_SOURCE="fake", VISION_VIEW="0", CALIBRATION_FILE=str(fake_cal))
    return env


def run_until_not_found(run_once, console, max_attempts=MAX_PUSH_ATTEMPTS):
    """Step 16: run, and while the position check does not say grid_not_found, ask for a further push
    and run again. Returns (results, reached)."""
    results = []
    for attempt in range(1, max_attempts + 1):
        res = run_once(attempt)
        results.append(res)
        if grid_not_found(res.output):
            return results, True
        if attempt < max_attempts:
            console.pause("Push the lid a bit more", [
                f"Attempt {attempt} of {max_attempts}: the position check did not say grid_not_found.",
                "Push the lid a little further, until the grid has clearly moved. Hands away."])
    return results, False


def file_info(path: Path) -> str:
    if not path.exists():
        return f"`{path}` (missing)"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(json.loads(path.read_text("utf-8"))["created"]))
    except Exception:
        created = "unknown"
    return f"`{path}` (created {created}, SHA256 {sha[:16]}...)"


class Runner:
    def __init__(self, console, log_path: Path, env: dict, *, fake=False, soak_out: Path | None = None,
                 cal_file: Path = config.CALIBRATION_FILE, launcher=None, artifacts_dir: Path | None = None) -> None:
        self.console, self.log_path, self.env, self.fake = console, Path(log_path), env, fake
        self.soak_out, self.cal_file = soak_out, Path(cal_file)
        self.launcher = launcher or self._launch_argv
        self.env_text = None  # None: the appendix reads git and config itself
        self.context = {"flip": "", "back": ""}
        self.results: list[StepResult] = []
        self.artifacts_dir = None if artifacts_dir is None else Path(artifacts_dir)
        self.current_step: int | None = None
        self.answers: dict = {}
        self.soaks: dict = {}  # label -> (step n, report dict or None, saved file name)
        self.cals: dict = {}  # tag -> (saved file name, calibration dict or None)
        if self.artifacts_dir is not None:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "a", encoding="utf-8")

    def _launch_argv(self, tail) -> list[str]:
        argv = [sys.executable, "-m", *tail]
        if self.fake and tail[0] == "scripts.soak" and self.soak_out is not None:
            argv += ["--out", str(self.soak_out)]
        return argv

    def log(self, text: str = "") -> None:
        self._log.write(text + "\n")
        self._log.flush()

    def close(self) -> None:
        self._log.close()

    # one command
    def run_command(self, tail, label="") -> CmdResult:
        argv = self.launcher(tail)
        shown = ".\\.venv\\Scripts\\python.exe -m " + " ".join(tail)
        start, started_at = time.strftime("%H:%M:%S"), time.time()
        self.console.say(f"\n--- {label + ': ' if label else ''}{shown}   (started {start})")
        self.log(f"\n### {label + ' — ' if label else ''}`{shown}`\n\n- started {start}")
        if argv != [sys.executable, "-m", *tail]:
            self.log(f"- launched as `{' '.join(argv)}`")
        self.log("\n```text")
        lines = []
        proc = subprocess.Popen(argv, cwd=config.BASE_DIR, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                lines.append(line)
                self.console.say(line)
                self.log(line)
            code = proc.wait()
        except KeyboardInterrupt:
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            self.log("```\n\n- INTERRUPTED (Ctrl+C)")
            raise
        end = time.strftime("%H:%M:%S")
        self.log(f"```\n\n- ended {end}, exit code {code}")
        self.console.say(f"--- exit code {code}   (ended {end})")
        res = CmdResult(shown, kind_of(tail), start, end, code, "\n".join(lines), label, tuple(tail), started_at)
        self.collect(res)
        return res

    # audit artifacts
    def _save(self, src: Path, name: str) -> str:
        dst = self.artifacts_dir / name
        if Path(src).is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        self.log(f"- saved for audit: `{name}`")
        return name

    def save_start_calibration(self) -> None:
        if self.artifacts_dir is None or not self.cal_file.exists():
            return
        try:
            self._save(self.cal_file, "start_calibration.json")
            self.cals["start"] = ("start_calibration.json", json.loads(self.cal_file.read_text(encoding="utf-8")))
        except Exception as e:
            self.log(f"- artifact copy failed (start calibration): {type(e).__name__}: {e}")

    def collect(self, res: CmdResult) -> None:
        """Copy what this command produced into the run folder. Never raises."""
        if self.artifacts_dir is None:
            return
        tag = "setup" if self.current_step is None else f"step{self.current_step:02d}"
        if res.label.startswith("attempt"):
            tag += "_a" + res.label.split()[-1]
        since = res.started_at - 1.0
        try:
            if res.kind == "calibrate":
                cal = None
                if self.cal_file.exists() and self.cal_file.stat().st_mtime >= since:
                    name = self._save(self.cal_file, f"{tag}_calibration.json")
                    try:
                        cal = json.loads(self.cal_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                    self.cals[tag] = (name, cal)
                debug = Path(config.VISION_DEBUG_DIR)
                for png in sorted(debug.glob("calib_*.png")) + sorted(debug.glob("verify_*.png")):
                    if png.stat().st_mtime >= since:
                        self._save(png, f"{tag}_{png.name}")
            elif res.kind == "soak":
                tail = list(res.tail)
                label = tail[tail.index("--label") + 1] if "--label" in tail else tag
                path = A.report_path(res.output)
                report, name = None, "-"
                if path and Path(path).exists():
                    name = self._save(Path(path), f"{tag}_soak_{label}.json")
                    report = json.loads(Path(path).read_text(encoding="utf-8"))
                    dbg = report.get("debug_dir")
                    if dbg and Path(dbg).is_dir():
                        self._save(Path(dbg), f"{tag}_soak_{label}_debug")
                self.soaks[label] = (self.current_step, report, name)
            elif res.kind == "read":
                for i, img in enumerate(A.debug_images(res.output), 1):
                    if Path(img).exists():
                        self._save(Path(img), f"{tag}_read_{i}.png")
                    else:
                        self.log(f"- debug image not found: `{img}`")
        except Exception as e:
            self.log(f"- artifact copy failed ({tag}): {type(e).__name__}: {e}")

    def cal_before_soaks(self) -> dict | None:
        """The calibration the soaks used: the last copy from steps 1-3 (or fake setup), else the start file."""
        for tag in reversed(list(self.cals)):
            if tag.startswith(("step01", "step02", "step03", "setup")) and self.cals[tag][1]:
                return self.cals[tag][1]
        start = self.cals.get("start")
        return start[1] if start else None

    # physical actions and answers
    def act(self, sr: StepResult, text: str) -> None:
        text = text.format(**self.context)
        self.console.pause(f"Step {sr.step.n}: do this now", [text])
        self.log(f"- {time.strftime('%H:%M:%S')} owner confirmed: {text}")

    def answer(self, sr: StepResult, key: str, question: str, choices=None) -> str:
        a = self.console.ask(key, question, choices)
        self.answers[key] = a
        self.log(f"- answer: {question} -> **{a}**")
        sr.notes.append(f"answer: {a}")
        return a

    # calibration with retry / skip / abort
    def run_calibration(self, sr: StepResult, tail) -> None:
        attempt = 1
        while True:
            res = self.run_command(tail, f"attempt {attempt}" if attempt > 1 else "")
            sr.runs.append(res)
            if calibration_ok(res):
                return
            self.console.beep()
            choice = self.answer(sr, "calibration_failed", "Calibration failed. Retry, skip or abort?",
                                 ["retry", "skip", "abort"])
            if choice == "skip":
                sr.notes.append("calibration failed, skipped by the owner")
                return
            if choice == "abort":
                sr.notes.append("calibration failed, run aborted by the owner")
                raise Abort(f"calibration failed at step {sr.step.n}")
            attempt += 1

    def run_step(self, step: Step) -> StepResult:
        sr = StepResult(step)
        self.results.append(sr)
        self.current_step = step.n
        self.console.say(f"\n{'=' * 70}\nSTEP {step.n} of 17: {step.title}\n{'=' * 70}")
        self.log(f"\n## Step {step.n} — {step.title}\n\n- step started {time.strftime('%H:%M:%S')}")
        for text in step.before:
            self.act(sr, text)
        if step.ask:
            self.answer(sr, *step.ask)
        if step.wait_s:
            secs = min(step.wait_s, getattr(self.console, "wait_s", step.wait_s))
            self.log(f"- waiting {secs} s, nothing touched (started {time.strftime('%H:%M:%S')})")
            self.console.countdown(step.wait_s, "DO NOT TOUCH ANYTHING: lid, grid, table, keyboard")
            self.log(f"- wait ended {time.strftime('%H:%M:%S')}")
        for tail in step.commands:
            if kind_of(tail) == "calibrate":
                self.run_calibration(sr, tail)
            elif step.push_retry:
                runs, reached = run_until_not_found(
                    lambda k: self.run_command(tail, f"attempt {k}"), _LoggingConsole(self.console, self, sr))
                sr.runs += runs
                note = (f"grid_not_found reached on attempt {len(runs)}" if reached
                        else f"grid_not_found NOT reached after {len(runs)} attempts")
                sr.notes.append(note)
                self.log(f"- {note}")
            else:
                sr.runs.append(self.run_command(tail))
        for text in step.after:
            self.act(sr, text)
        return sr

    def run(self, steps) -> str:
        """Run the steps; returns 'done', 'aborted' or 'interrupted'. The summary is always written."""
        outcome = "done"
        current = None
        try:
            for step in steps:
                current = step.n
                self.run_step(step)
        except Abort as e:
            outcome = "aborted"
            self.log(f"\n**ABORTED: {e}**")
        except KeyboardInterrupt:
            outcome = "interrupted"
            self.log(f"\n**INTERRUPTED at step {current} (Ctrl+C)**")
        summary = format_summary(self.results, outcome)
        self.console.say("\n" + summary)
        self.log("\n" + summary)
        try:
            rows = A.gate_precheck(self.results, {k: v[1] for k, v in self.soaks.items()},
                                   self.cal_before_soaks(), self.answers)
            self.console.say("\n" + A.precheck_text(rows))
            self.log("\n" + A.appendix(rows, self.soaks, self.cals, self.artifacts_dir, self.env_text))
        except Exception as e:  # the audit must never cost the log
            self.log(f"\n**Audit appendix failed: {type(e).__name__}: {e}**")
        return outcome


class _LoggingConsole:
    """Passes step-16 'push more' pauses to the console and records them in the log."""

    def __init__(self, console, runner, sr) -> None:
        self.console, self.runner, self.sr = console, runner, sr

    def pause(self, title, lines) -> None:
        self.console.pause(title, lines)
        self.runner.log(f"- {time.strftime('%H:%M:%S')} owner confirmed: {lines[-1]}")


def format_summary(results, outcome="done") -> str:
    out = ["## Summary", "", f"Run {outcome}.", "", "```text"]
    for sr in results:
        codes = ", ".join(str(r.code) for r in sr.runs) or "-"
        out.append(f"Step {sr.step.n:2d}  {sr.step.title}   (exit {codes})")
        for note in sr.notes:
            out.append(f"      * {note}")
        for r in sr.runs:
            if len(sr.runs) > 1:
                out.append(f"      [{r.label or r.command.split(' -m ')[-1]}]")
            out += [f"      {ln}" for ln in extract_key_lines(r.kind, r.output)]
    out.append("```")
    return "\n".join(out)


# ---------------------------------------------------------------- main

def select_steps(start: int) -> list[Step]:
    return [s for s in STEPS if s.n >= start]


def light_context(state: str) -> dict:
    return {"flip": "OFF (it was ON at the start)" if state == "on" else "ON (it was OFF at the start)",
            "back": "ON, as at the start" if state == "on" else "OFF, as at the start"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--from", dest="start", type=int, default=1, choices=range(1, 18), metavar="N")
    p.add_argument("--fake", action="store_true", help="fake board and camera, automatic answers")
    args = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    stamp = time.strftime("%Y%m%d_%H%M%S")
    live_cal = Path(config.CALIBRATION_FILE)
    live_sha = hashlib.sha256(live_cal.read_bytes()).hexdigest() if live_cal.exists() else None
    scratch = None
    if args.fake:
        scratch = LOG_DIR / f"stage4_fake_{stamp}"
        scratch.mkdir(parents=True, exist_ok=True)
        cal_file = scratch / "fake_calibration.json"
        assert cal_file.resolve() != live_cal.resolve()
        console = AutoConsole()
        env = child_env(True, cal_file)
    else:
        cal_file = live_cal
        console = Console()
        env = child_env(False)
    name = f"stage4_run_{stamp}{'_fake' if args.fake else ''}"
    log_path, artifacts = LOG_DIR / f"{name}.md", LOG_DIR / name
    runner = Runner(console, log_path, env, fake=args.fake, soak_out=scratch, cal_file=cal_file,
                    artifacts_dir=artifacts)

    runner.log(f"# Stage 4 live checks — {'FAKE' if args.fake else 'LIVE'} run {stamp}\n")
    runner.log(f"- started {time.strftime('%Y-%m-%d %H:%M:%S')}, from step {args.start}")
    runner.log(f"- calibration file in use at the start: {file_info(cal_file)}")
    runner.log(f"- audit files (calibration copies, soak reports, debug images): `{artifacts}`")
    runner.save_start_calibration()
    runner.log(f"- child environment: {', '.join(STRIP_ENV)} removed"
               + (" then set to fake values" if args.fake else ""))
    console.say(f"Stage 4 guided run ({'FAKE' if args.fake else 'LIVE'}), log: {log_path}")

    outcome = "aborted"
    try:
        console.banner("Before you start", ["Confirm each item with Enter."])
        runner.log("\n## Checklist\n")
        for item in CHECKLIST:
            if args.fake:
                console.say(f">>> {item} [auto] Enter")
            else:
                input(f">>> {item}   - press Enter to confirm ")
            runner.log(f"- confirmed: {item}")
        light = console.ask("light", "Is the room light ON or OFF right now?", ["on", "off"])
        runner.context = light_context(light)
        runner.log(f"- room light at the start: **{light}**")

        steps = select_steps(args.start)
        if args.fake and args.start > 3 and not cal_file.exists():
            runner.log("\n## Fake setup — calibration for a run that starts after step 3")
            runner.run_command(CAL, "fake setup")
        outcome = runner.run(steps)
    except KeyboardInterrupt:
        outcome = "interrupted"
        runner.log("\n**INTERRUPTED before the steps (Ctrl+C)**")
    finally:
        runner.log(f"\n- calibration file in use at the end: {file_info(cal_file)}")
        if args.fake:
            now = hashlib.sha256(live_cal.read_bytes()).hexdigest() if live_cal.exists() else None
            same = now == live_sha
            runner.log(f"- live calibration file unchanged: {'yes' if same else 'NO'}")
            console.say(f"live calibration file unchanged: {'yes' if same else 'NO -- REPORT THIS'}")
            shutil.rmtree(scratch, ignore_errors=True)
            if not same:
                outcome = "live_calibration_changed"
        runner.log(f"- finished {time.strftime('%Y-%m-%d %H:%M:%S')}, outcome {outcome}")
        runner.close()
    console.say(f"\nAudit files in: {artifacts}")
    console.say(f"Log written to: {log_path}")
    return {"done": 0, "interrupted": 130}.get(outcome, 2)


if __name__ == "__main__":
    sys.exit(main())
