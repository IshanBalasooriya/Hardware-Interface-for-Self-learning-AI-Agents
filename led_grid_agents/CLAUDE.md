# CLAUDE.md — `led_grid_agents/`

## What this folder is

A copy of `led_grid/` (a working single-agent system: an LLM drives an 8x8 LED matrix on an ESP32 through generic primitives and saves working results as JSON skills). Here it is extended to a **two-agent pipeline**: an Orchestrator LLM that plans and audits, the existing Drawer agent, and a plain-Python Controller that enforces the procedure.

The project is a general LLM-to-hardware interface. The LED grid is only the demonstration vehicle. The final evaluation is on 2026-10-09, so the smallest change that works wins.

## Where the plan is

| File | Use |
|---|---|
| `docs/AGENTS_MASTER.md` | The whole design and all interfaces. Read it fully at the start of every session |
| `docs/ASTAGE_<N>_*.md` | One stage. Build only the stage the owner names |
| `docs/CODE_SURVEY.md` | Description of the existing code with line references. Use it before opening files |
| `docs/PROGRESS.md`, `docs/DEVIATIONS.md` | Append an `Agents stage N` section to each when a stage is done |

## Session protocol

1. The owner names one stage. Read the master plan, then that stage file, then the earlier `Agents stage` entries in `PROGRESS.md` and `DEVIATIONS.md`.
2. Run the previous stage's verify script first (for stage 1: the plain test suite, expect 158 passed). If it does not pass, stop and report.
3. Build the stage in the order its file gives.
4. Write the stage's tests and its `scripts/verify_stageN.py`.
5. Run `scripts/verify_stageN.py` yourself and fix until it prints `STAGE N VERIFY: PASS`. Never weaken a check or a test to get there; if a check cannot pass as specified, stop and report why.
6. Update `PROGRESS.md` and `DEVIATIONS.md`, then report as the stage file says.

If the plan is unclear, contradicts the code, or cannot be followed as written: stop and ask. Do not guess, and do not redesign.

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest                      # full suite
.\.venv\Scripts\python.exe -m pytest tests\test_x.py -q   # one file
.\.venv\Scripts\python.exe scripts\verify_stageN.py       # stage verification
.\.venv\Scripts\python.exe scripts\verify_all.py          # all stages built so far
```

The `python` on PATH is MSYS2 3.12 and unusable. Always use `.\.venv\Scripts\python.exe` (Python 3.11.9). Shell is PowerShell on Windows.

## Never

- Never open the real serial port, start the server against the board, or call the real LLM or its proxy. Fake device (`FakeTransport`) and scripted LLM clients only. The owner runs every hardware step.
- Never run `scripts/live_check.py` against anything. Test its logic with recorded events only.
- Never modify anything outside this folder. `../led_grid/` is tomorrow's demo; read it only for the byte comparisons the verify scripts make.
- Never change these files: `agent/loop.py`, `agent/tools.py`, `agent/prompts.py`, `skills/store.py`, `skills/runner.py`, `bridge/bridge.py`, `bridge/transport.py`, `bridge/fake_device.py`, `bridge/grid_model.py`, anything in `firmware/`.
- Never edit an existing test file (one named line in stage 4 is the only exception). New tests go in new files.
- Never write or edit skill JSON by hand, and never let a test write into `skills/library/` (tests use `tmp_path`).
- Never add display-named tools, fonts or glyph tables to code or prompts. Primitives stay generic (`shift_out`, `wait`).
- Never add a dependency. Never refactor existing code.
- Never claim a hardware check passed, and never describe the audit as verifying the physical display: it judges device-confirmed **commanded** state.

## Code conventions (match the existing code)

- Python 3.11, type hints, short module docstring, no classes where a function will do.
- Import direction `server -> agent -> skills -> bridge`.
- Tool results and errors are dicts with `success`; error text is cut to 200 characters.
- Events are plain dicts with a `type`; emit them through the given `on_event` callback and never let a listener exception escape.
- Constants live in `config.py`.

## Report format at the end of a stage

1. The last line printed by `scripts/verify_stageN.py`.
2. The pytest result line (total passed).
3. Files created and files changed.
4. Every deviation from the plan, each with its reason.
5. Open issues, and anything in the plan that was unclear.
