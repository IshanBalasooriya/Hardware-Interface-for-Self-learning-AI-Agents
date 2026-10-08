"""Stage 1 verification: common checks C1..C6 plus the stage 1 static checks (C7). Offline, standard library only."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_common import ROOT, Report, added_lines_only, rel, run_common  # noqa: E402

STAGE = 1
TEST_FILES = ["tests/test_grid_store_since.py", "tests/test_pending.py", "tests/test_evidence.py"]
HELPER_NAMES = ["ScriptedClient", "text_reply", "tool_reply", "shift_reply", "plan_reply", "verdict_reply",
                "make_bridge", "make_ctx", "frame_hex"]


def _import(name: str):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(name)


def check_gitignore(report: Report) -> None:
    lines = rel(".gitignore").read_text(encoding="utf-8").splitlines()
    if "!*.md" in (line.strip() for line in lines):
        report.row("PASS", "C7", ".gitignore", "contains !*.md")
    else:
        report.row("FAIL", "C7", ".gitignore", "no line !*.md")


def check_config(report: Report) -> None:
    try:
        config = _import("config")
    except Exception as e:
        report.row("FAIL", "C7", "config constants", f"import failed: {type(e).__name__}: {e}")
        return
    names = ["AGENT_MODE", "MAX_ROUNDS", "RUN_TIME_CAP_S", "MAX_REPLAY_SKILLS", "MAX_REPLAY_HOLD_MS",
             "MAX_PLAN_STAGES", "MAX_EVIDENCE_FRAMES"]
    problems = [f"{name} missing" for name in names if not hasattr(config, name)]
    if getattr(config, "MAX_ROUNDS", None) != 3:
        problems.append(f"MAX_ROUNDS is {getattr(config, 'MAX_ROUNDS', None)!r}, expected 3")
    if getattr(config, "MAX_REPLAY_SKILLS", None) != 8:
        problems.append(f"MAX_REPLAY_SKILLS is {getattr(config, 'MAX_REPLAY_SKILLS', None)!r}, expected 8")
    if problems:
        report.row("FAIL", "C7", "config constants", "; ".join(problems))
    else:
        report.row("PASS", "C7", "config constants", f"all {len(names)} present, MAX_ROUNDS 3, MAX_REPLAY_SKILLS 8")


def check_imports(report: Report) -> None:
    problems = []
    try:
        if not callable(getattr(_import("bridge.grid_store").GridStore, "since", None)):
            problems.append("GridStore.since is not callable")
    except Exception as e:
        problems.append(f"bridge.grid_store: {type(e).__name__}: {e}")
    try:
        _import("skills.pending").PendingSkillStore
    except Exception as e:
        problems.append(f"skills.pending.PendingSkillStore: {type(e).__name__}: {e}")
    try:
        evidence = _import("agent.evidence")
        for name in ("skill_outline", "build_catalogue", "frame_digest"):
            if not callable(getattr(evidence, name, None)):
                problems.append(f"agent.evidence.{name} missing")
    except Exception as e:
        problems.append(f"agent.evidence: {type(e).__name__}: {e}")
    if problems:
        report.row("FAIL", "C7", "New modules import", "; ".join(problems))
    else:
        report.row("PASS", "C7", "New modules import", "GridStore.since, PendingSkillStore, agent.evidence x3")


def check_grid_store_diff(report: Report) -> None:
    status, detail = added_lines_only("bridge/grid_store.py")
    report.row(status, "C7", "bridge/grid_store.py only added lines", detail)


def check_helpers(report: Report) -> None:
    if not rel("tests/helpers.py").is_file():
        report.row("FAIL", "C7", "tests/helpers.py exports", "file missing")
        return
    try:
        helpers = _import("tests.helpers")
    except Exception as e:
        report.row("FAIL", "C7", "tests/helpers.py exports", f"import failed: {type(e).__name__}: {e}")
        return
    missing = [name for name in HELPER_NAMES if not callable(getattr(helpers, name, None))]
    if missing:
        report.row("FAIL", "C7", "tests/helpers.py exports", f"missing: {', '.join(missing)}")
    else:
        report.row("PASS", "C7", "tests/helpers.py exports", f"all {len(HELPER_NAMES)} names")


def stage_checks(report: Report) -> None:
    check_gitignore(report)
    check_config(report)
    check_imports(report)
    check_grid_store_diff(report)
    check_helpers(report)


def main() -> int:
    report = Report(STAGE)
    report.out(f"Stage {STAGE} verification in {ROOT}")
    run_common(report, TEST_FILES)
    stage_checks(report)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
