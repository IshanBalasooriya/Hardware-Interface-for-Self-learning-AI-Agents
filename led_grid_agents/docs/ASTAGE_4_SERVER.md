# ASTAGE_4_SERVER.md — stage 4: server wiring, live check script, runbook

Read `docs/AGENTS_MASTER.md` first, then the `Agents stage 1` to `3` sections of `docs/PROGRESS.md` and `docs/DEVIATIONS.md`.

## Before you start

Run `.\.venv\Scripts\python.exe scripts\verify_stage3.py`. It must end with `STAGE 3 VERIFY: PASS`. If not, stop and report.

## Build

1. **`server/runs.py`**: `RunManager.__init__` gains `mode: str = AGENT_MODE`. In `_run`: `mode == "multi"` calls `run_pipeline(prompt, ctx, on_event, client=self._client)`; anything else calls `run_agent(...)` exactly as today. A value other than `multi` or `single` logs one warning when the manager is created. Nothing else changes: one run at a time, the stop event, and the `status`, `run_started`, `run_finished` events stay as they are. `run_finished.status` may now be `not_approved`.
2. **`server/app.py`**: `create_app` gains `mode: str = AGENT_MODE`, passed to `RunManager`. Add `mode` to the `/api/status` reply **only if** no existing test asserts that reply as a whole dict; if one does, add a separate `GET /api/mode` returning `{"mode": ...}` instead and record it in `DEVIATIONS.md`. The 500-character limit stays at the endpoint only.
3. **`tests/test_server.py`**: the one permitted edit to an existing test file. In the `make_server` fixture, pass `mode="single"` to `create_app`. No other line changes.
4. **Dashboard check** (read first, change only if needed): read `server/static/store.js` and `server/static/trace.js` and determine what is shown when `run_finished.status` is `not_approved`. If it is shown as not successful, or falls back to the summary text, change nothing. If it breaks or looks like a success, make the smallest fix in `server/static/`. No other dashboard work.
5. **`scripts/live_check.py`**: the automated hardware check (specification below).
6. **`docs/RUNBOOK_AGENTS.md`**: the owner's steps (specification below).

## `scripts/live_check.py`

Run by the owner against the real board, with the server already running. Standard library only (`urllib`, `json`, `time`, `argparse`).

```powershell
.\.venv\Scripts\python.exe scripts\live_check.py            # all checks
.\.venv\Scripts\python.exe scripts\live_check.py --only 3   # one check
.\.venv\Scripts\python.exe scripts\live_check.py --restore  # after the checks, restore the library from library_backup and confirm 32
```

Pre-flight (abort with a clear message if any fails): the server answers `GET /api/status`; `connected` is true; not busy; the mode is `multi`; the events log (`config.EVENTS_LOG`) exists and grows when a prompt is sent (the server must have been started with `RECORD_EVENTS=1`); the library holds 32 skills.

For each check: note the events-log size and the library file list; `POST /api/prompt`; poll `/api/status` every 0.5 s until not busy (timeout `--timeout`, default 240 s; on timeout send `POST /api/stop` and mark `FAIL`); read the new event lines; evaluate.

The evaluation is a **pure function** `evaluate(check, events, library_before, library_after, skill_files) -> {"result": "PASS"|"WARN"|"FAIL", "observed": str, "details": [...]}` so it can be unit-tested with recorded events.

| # | Prompt | PASS when | WARN when |
|---|---|---|---|
| 1 | `Show a heart.` | plan route `replay` naming `symbol_heart`; no event with role `drawer`; last verdict `approve`; status `completed`; library unchanged | approved through the draw route instead |
| 2 | `Count down 3, 2, 1.` | plan route `replay` with `digit_3`, `digit_2`, `digit_1` in that order; approved; library unchanged | approved through the draw route |
| 3 | `Blink the digit 0 six times.` | status `completed`; last verdict `approve`; between the last `skill_pending` and the following `audit_verdict` exactly 6 `shift_state` events have at least one lit LED; one new skill in the library; that skill has `params` and a `repeat` whose `count` is a `$name` with default 6 | all of that except the count is a literal 6 |
| 4 | `Blink the digit 0 four times.` | plan route `replay` of the skill created in check 3 with a param set to 4; exactly 4 lit `shift_state` events in the round; approved; library unchanged | approved with 4 lit frames through the draw route |
| 5 | `Draw a plus sign: one vertical line in columns 3 and 4 from row 1 to row 6, and one horizontal line in rows 3 and 4 from column 1 to column 6. Save it as symbol_plus.` | approved; `symbol_plus` is in the library; the last `shift_state` rows equal the plus computed from that description | approved and saved, but the rows differ from the computed plus (print both) |
| 6 | `What have you displayed in the last minute?` | plan route `answer`; no `audit_verdict`; library unchanged; non-empty summary | — |
| 7 | `Draw a star.` | informational: never `FAIL`. Records the plan's stages, the number of rounds and the final status | — |

Check 4 is skipped with `WARN` if check 3 created no skill.

Output: a table on the console (check, result, rounds, seconds, observed), and `docs/LIVE_CHECK_RESULTS.md` with the same table, the full plan and verdict text of every check, the list of skills added, and this sentence: "These results are device-confirmed commanded state, not camera-verified." The script ends with `LIVE CHECK: PASS`, `PASS WITH WARNINGS` or `FAIL (k)`.

`--restore` deletes `skills/library/*.json`, copies `skills/library_backup/*.json` back, and confirms the count is 32. Without `--restore` the script prints the names of the skills it left in the library.

## `docs/RUNBOOK_AGENTS.md`

Exact numbered PowerShell steps, nothing left to interpretation:

1. Stop any running `led_grid` server (one process owns `COM6`).
2. Start the proxy (`uvx openai-api-server-via-codex`) in its own window.
3. In `led_grid_agents\`: run `scripts\verify_stage4.py` and expect `PASS`.
4. Start the server with events recording: `$env:RECORD_EVENTS = "1"`, then `.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000`.
5. Open `http://127.0.0.1:8000/` and watch the Technical tab.
6. In a second window: `.\.venv\Scripts\python.exe scripts\live_check.py`.
7. Read the last line and `docs\LIVE_CHECK_RESULTS.md`; send that file back for review.
8. `.\.venv\Scripts\python.exe scripts\live_check.py --restore` is not a check run; describe it as the reset, and give the manual restore commands as well.
9. Fallback to the original behaviour in this folder: `$env:AGENT_MODE = "single"` before starting the server; `Remove-Item Env:AGENT_MODE` to undo.

## Tests

**`tests/test_server_agents.py`** (mode `multi`, `port="fake"`, scripted client, `tmp_path`)
- Replay run: event types arrive in the order `status`, `run_started`, `plan_ready`, `round_started`, `tool_call`, `shift_state`, `tool_result`, `audit_verdict`, `run_finished`, `status`; pipeline events carry `run_id`, `role`, `round`.
- Draw run with a save, approved: `GET /api/skills` lists the skill afterwards.
- Draw run revised three times: `run_finished.status == "not_approved"`; `GET /api/skills` unchanged.
- `POST /api/prompt` during a pipeline run returns `409`.
- `POST /api/stop` during a pipeline run ends it with `stopped`.
- A prompt over 500 characters is still rejected with `400`; a 500-character prompt is accepted.
- Mode `single` through `create_app` gives the original event order with no `plan_ready`.
- An unknown mode behaves as `single`.

**`tests/test_live_check.py`** (pure functions only, no network)
- For each of the seven checks: a hand-built event list that should `PASS`, one that should `WARN` where the table has a WARN column, and one that should `FAIL` (check 7: never `FAIL`).
- The plus rows computed by the script equal the rows written out in the test.
- The lit-frame counter counts only `shift_state` events between the last `skill_pending` and the next `audit_verdict`.

## `scripts/verify_stage4.py`

All earlier checks, with C4 relaxed for `tests/test_server.py` as master section 10 says, plus C2 = the two test files above, plus C7:
- `tests/test_server.py` differs from `../led_grid/tests/test_server.py` in at most one line, and that line contains `mode=`.
- `server/runs.py` references both `run_pipeline` and `run_agent`.
- `scripts/live_check.py --help` exits 0 without contacting anything.
- `scripts/live_check.py` imports nothing outside the standard library and `config`.
- `docs/RUNBOOK_AGENTS.md` exists and contains the strings `RECORD_EVENTS`, `live_check.py`, `AGENT_MODE` and `commanded`.

## Done when

- `.\.venv\Scripts\python.exe scripts\verify_stage4.py` ends with `STAGE 4 VERIFY: PASS`.
- `docs/PROGRESS.md` and `docs/DEVIATIONS.md` each have an `Agents stage 4` section. PROGRESS states what the dashboard does on `not_approved`, with the file and line that decides it.

Report as `CLAUDE.md` says. Do not run `scripts/live_check.py`; the owner does.
