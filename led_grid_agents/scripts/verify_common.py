"""Shared offline checks C1..C6 for the verify_stageN scripts (agents master section 10). Standard library only."""

import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT.parent / "led_grid"
MIN_PASSED = 158
LIBRARY_COUNT = 32
TAIL_LINES = 40

PROTECTED = ["agent/loop.py", "agent/tools.py", "agent/prompts.py", "skills/store.py", "skills/runner.py",
             "bridge/bridge.py", "bridge/transport.py", "bridge/fake_device.py", "bridge/grid_model.py"]
FORBIDDEN_IMPORTS = {"bridge": ("skills", "agent", "server"), "skills": ("agent", "server"), "agent": ("server",)}


class Report:
    """Collects check rows, prints them as they come, and writes the log at the end."""

    def __init__(self, stage: int) -> None:
        self.stage = stage
        self.lines: list[str] = []
        self.failed = 0

    def out(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def row(self, status: str, check_id: str, name: str, detail: str, output: str | None = None) -> None:
        if status == "FAIL":
            self.failed += 1
        self.out(f"{status:<4}  {check_id:<3} {name}: {detail}")
        if status == "FAIL" and output:
            for line in output.rstrip().splitlines():
                self.out(f"      | {line}")

    def finish(self) -> int:
        result = "PASS" if self.failed == 0 else f"FAIL ({self.failed} checks failed)"
        self.out(f"STAGE {self.stage} VERIFY: {result}")
        log = ROOT / "logs" / f"verify_stage{self.stage}.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return 0 if self.failed == 0 else 1


def rel(path: str) -> Path:
    return ROOT / Path(path)


def run_pytest(*args: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rfE", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def pytest_counts(output: str) -> dict[str, int]:
    """Counts from pytest's final summary line, e.g. '3 failed, 155 passed, 1 error in 2.1s'."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    return {word: int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?|skipped)", summary)}


def failure_detail(output: str) -> str:
    names = [line for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))]
    tail = output.rstrip().splitlines()[-TAIL_LINES:]
    return "\n".join(names + ["--- last lines ---"] + tail)


def check_full_suite(report: Report) -> None:
    code, output = run_pytest()
    counts = pytest_counts(output)
    passed = counts.get("passed", 0)
    bad = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    detail = f"{passed} passed, {bad} failed or errors"
    if code != 0 or bad or passed < MIN_PASSED:
        report.row("FAIL", "C1", "Full test suite", f"{detail} (need 0 failed and >= {MIN_PASSED} passed)",
                   failure_detail(output))
    else:
        report.row("PASS", "C1", "Full test suite", detail)


def check_stage_tests(report: Report, test_files: list[str]) -> None:
    for test_file in test_files:
        path = rel(test_file)
        name = f"Stage tests {test_file}"
        if not path.exists():
            report.row("FAIL", "C2", name, "file missing")
            continue
        if not re.search(r"^def test_", path.read_text(encoding="utf-8"), re.M):
            report.row("FAIL", "C2", name, "no tests in file")
            continue
        code, output = run_pytest(test_file)
        counts = pytest_counts(output)
        passed = counts.get("passed", 0)
        if code != 0 or passed == 0:
            report.row("FAIL", "C2", name, f"{passed} passed, exit code {code}", failure_detail(output))
        else:
            report.row("PASS", "C2", name, f"{passed} passed")


def _tree(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*")
            if p.is_file() and ".pio" not in p.relative_to(base).parts}


def check_protected(report: Report) -> None:
    if not ORIGINAL.is_dir():
        report.row("WARN", "C3", "Protected files unchanged", f"{ORIGINAL} not readable")
        return
    pairs = [(path, rel(path), ORIGINAL / path) for path in PROTECTED]
    firmware = _tree(ORIGINAL / "firmware") | _tree(rel("firmware"))
    pairs += [(f"firmware/{p}", rel("firmware") / p, ORIGINAL / "firmware" / p) for p in sorted(firmware)]
    changed = [name for name, here, there in pairs
               if not (here.is_file() and there.is_file() and here.read_bytes() == there.read_bytes())]
    if changed:
        report.row("FAIL", "C3", "Protected files unchanged", f"{len(changed)} differ", "\n".join(changed))
    else:
        report.row("PASS", "C3", "Protected files unchanged", f"{len(pairs)} files identical to ../led_grid")


def _only_mode_lines_differ(here: Path, there: Path) -> bool:
    a = there.read_text(encoding="utf-8").splitlines()
    b = here.read_text(encoding="utf-8").splitlines()
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if "mode=" not in line:
            return False
    return True


def check_existing_tests(report: Report, allow_server_mode_lines: bool = False) -> None:
    original_tests = ORIGINAL / "tests"
    if not original_tests.is_dir():
        report.row("WARN", "C4", "Existing tests unchanged", f"{original_tests} not readable")
        return
    changed = []
    files = sorted(p for p in original_tests.iterdir() if p.is_file())
    for there in files:
        here = rel("tests") / there.name
        if here.is_file() and here.read_bytes() == there.read_bytes():
            continue
        if (allow_server_mode_lines and there.name == "test_server.py" and here.is_file()
                and _only_mode_lines_differ(here, there)):
            continue
        changed.append(f"tests/{there.name}")
    if changed:
        report.row("FAIL", "C4", "Existing tests unchanged", f"{len(changed)} differ", "\n".join(changed))
    else:
        report.row("PASS", "C4", "Existing tests unchanged", f"{len(files)} files identical to ../led_grid/tests")


def check_import_direction(report: Report) -> None:
    problems = []
    for package, forbidden in FORBIDDEN_IMPORTS.items():
        pattern = re.compile(rf"^\s*(?:from|import)\s+({'|'.join(forbidden)})\b", re.M)
        for path in sorted(rel(package).rglob("*.py")):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(ROOT).as_posix()} imports {match.group(1)}")
    if problems:
        report.row("FAIL", "C5", "Import direction", f"{len(problems)} wrong imports", "\n".join(problems))
    else:
        report.row("PASS", "C5", "Import direction", "server -> agent -> skills -> bridge holds")


def check_library(report: Report) -> None:
    library, backup = rel("skills/library"), rel("skills/library_backup")
    here = sorted(p.name for p in library.glob("*.json"))
    there = sorted(p.name for p in backup.glob("*.json"))
    problems = []
    if len(here) != LIBRARY_COUNT:
        problems.append(f"skills/library holds {len(here)} .json files, expected {LIBRARY_COUNT}")
    problems += [f"{name} not in library_backup" for name in here if name not in there]
    problems += [f"{name} missing from library" for name in there if name not in here]
    problems += [f"{name} differs from library_backup" for name in here
                 if name in there and (library / name).read_bytes() != (backup / name).read_bytes()]
    if problems:
        report.row("FAIL", "C6", "Skill library intact", problems[0], "\n".join(problems))
    else:
        report.row("PASS", "C6", "Skill library intact", f"{len(here)} skills identical to library_backup")


def added_lines_only(path: str) -> tuple[str, str]:
    """(status, detail) for 'path differs from ../led_grid/path only by added lines'."""
    here, there = rel(path), ORIGINAL / path
    if not there.is_file():
        return "WARN", f"{there} not readable"
    a = there.read_text(encoding="utf-8").splitlines()
    b = here.read_text(encoding="utf-8").splitlines()
    opcodes = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    removed = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag in ("replace", "delete"))
    added = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("replace", "insert"))
    if removed:
        return "FAIL", f"{removed} original lines removed or changed"
    return "PASS", f"{added} lines added, no original line changed"


def run_common(report: Report, test_files: list[str], allow_server_mode_lines: bool = False) -> None:
    check_full_suite(report)
    check_stage_tests(report, test_files)
    check_protected(report)
    check_existing_tests(report, allow_server_mode_lines)
    check_import_direction(report)
    check_library(report)
