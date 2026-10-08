# Progress

## Stage 1 — Scaffold and chip model (2026-10-06)

Files created:
- `requirements.txt`, `pytest.ini`, `.gitignore`, `.env.example`, `config.py`, `README.md`
- `bridge/__init__.py`, `skills/__init__.py`, `agent/__init__.py`, `server/__init__.py` (empty)
- `bridge/grid_model.py`
- `tests/test_grid_model.py`
- `docs/PROGRESS.md`, `docs/DEVIATIONS.md`

Test results: `.venv/Scripts/python.exe -m pytest` with Python 3.11.9: 14 passed (all 13 spec cases; case 13 is split into a fresh-model test and a known-models test).

Notes:
- `resync_bytes` uses the default only when a register is unknown (`None`). A known 0 is kept, so `scan_limit=0` and `intensity=0` survive re-sync, as the round-trip property needs.
- `SERIAL_PORT` is `None` when the env var is unset (the spec gives no default).

Open issues:
- `MSB_IS_LEFT = True` is a placeholder until calibration in stage 3.
- Run tests with the venv's Python 3.11 (`.venv/Scripts/python.exe -m pytest`). The `python` on PATH is MSYS2 3.12 and does not have the dependencies.

## Stage 2 — LED map store (2026-10-06)

Files created:
- `bridge/grid_store.py`
- `tests/test_grid_store.py`

Files changed:
- `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 24 passed (14 stage 1 + 10 stage 2, one test per spec case).

Notes:
- `grid_store.py` imports only the standard library, `config`, and `bridge.grid_model`. `grid_model.py` and its tests are unchanged.
- The LED map keys follow the master 6.3 order: `seq`, `timestamp`, `display`, `intensity`, `rows`, `bytes`, `warnings`.
- `load()` treats a file as corrupt if it is invalid JSON or is missing a field / has a wrong type (`ValueError`, `KeyError`, `TypeError`). It renames the file to `<name>.corrupt` and resets to the initial state (`seq = 0`).
- `recent()` reads the last 64 KB of the frame log. If it starts partway through the file, it drops the first (possibly partial) line. The file is read under the store lock so it never sees a half-written line from a concurrent `record`.

Open issues:
- None new. `MSB_IS_LEFT` still waits for calibration in stage 3.

## Stage 3 — Firmware, fake device, calibration (2026-10-07)

Files created:
- `firmware/platformio.ini`, `firmware/src/main.cpp`
- `bridge/fake_device.py`
- `tests/test_fake_device.py`
- `docs/WIRING.md`

Files changed:
- `config.py` (`MSB_IS_LEFT = True` confirmed by calibration; comment added)
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 38 passed (24 from stages 1–2 + 14 stage 3). Firmware: `pio run` (PlatformIO at `~/.platformio/penv/Scripts/pio.exe`, not on PATH) built with no errors or warnings.

Hardware checks (reported by the user):
- H1: the user flashed the board and the monitor printed `ESP32_READY`.
- H2: all 7 rows matched the expected output. After the wake command, an old picture was still on the display (the spec allows this). The clear command blanked it.
- H3: with `0180` the LED was at the top-left. `0100` cleared it, and `0880` lit the bottom-left LED. `MSB_IS_LEFT = True`. The result is recorded in `docs/WIRING.md`.

Notes:
- The firmware was written without reading the POC, and the user did the flashing. Both are recorded in `docs/DEVIATIONS.md`, along with the extra `0100` step in H3 and the parsing interpretation choices.
- No flicker or garbage on the display, so there is no sign of the 3.3 V logic-level issue.

Open issues:
- None.

## Stage 4 — Transport and Bridge (2026-10-07)

Files created:
- `bridge/transport.py` (`TransportError`, `SerialTransport`, `FakeTransport`, `open_transport`)
- `bridge/bridge.py` (`Bridge`: `start`, `ping`, `shift_out`, `wait`, `resync`, `reconnect`, `close`)
- `tests/test_bridge.py`
- `scripts/__init__.py`, `scripts/manual_patterns.py`
- `.env` (gitignored, `SERIAL_PORT=COM6`; not tracked)

Files changed:
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 51 passed (38 from stages 1–3 + 13 stage 4). The 9 spec cases are covered; case 4 (bad hex) is parametrised over 5 inputs.

Hardware checks (reported by the user, board on COM6):
- All five patterns (top-left corner LED, full top row, left column, checkerboard, heart) printed `success=True` and the printed rows matched the LEDs, including left/right and top/bottom.
- After unplugging and replugging the board, `python -m scripts.manual_patterns --resync-only` restored the heart, matching the printed block.

Notes:
- The transport was written from the spec alone, without reading `POC/` (user instruction). The user ran the script against the real port; Claude Code did not open it. Both are recorded in `docs/DEVIATIONS.md` with the small interpretation choices.
- `store.record` is called only inside `Bridge.shift_out`. Failed commands are not retried.

Open issues:
- None.

## Stage 5 — Skill store and runner (2026-10-07)

Files created:
- `skills/store.py` (`SkillError`, `SkillStore`: `list`, `get`, `save`; `validate`, `count_steps`)
- `skills/runner.py` (`run_skill`)
- `skills/library/.gitkeep`, `skills/library/anim_test_blink.json` (written through `SkillStore.save`)
- `tests/test_skills.py`
- `scripts/run_skill.py`

Files changed:
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 94 passed (51 from stages 1–4 + 43 stage 5). All 10 spec cases are covered. Case 3 is parametrised over 26 bad definitions, plus 5 bad names. Case 4 is parametrised over 3 tools.

Hardware checks (reported by the user, board on COM6):
- `python -m scripts.run_skill anim_test_blink`: the heart blinked 5 times at a steady rate and the script printed `steps_run=20`.
- `logs/shift_frames.jsonl` gained the skill's 10 frames, seq 9–18 (after the start-up re-sync at seq 8).

Notes:
- The runner only calls `bridge.shift_out`, `bridge.wait` and `bridge.store.current()`, and it runs only `shift_out`, `wait` and `repeat`. Firmware, bridge and store code are unchanged.

Open issues:
- None.

## Stage 6 — Agent: tools, prompt, loop, CLI (2026-10-07)

Files created:
- `agent/tools.py` (`AgentContext`, `TOOL_SCHEMAS`, `call_tool`)
- `agent/prompts.py` (`build_system_prompt`)
- `agent/loop.py` (`run_agent`)
- `agent/cli.py`
- `tests/test_tools.py`, `tests/test_loop.py`
- `skills/library/symbol_heart.json`, `glyph_a.json`, `anim_heartbeat.json` (saved by the agent during the HUMAN runs)

Files changed:
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 128 passed (94 from stages 1–5 + 34 stage 6). All spec cases are covered for tools (one success and one failure per tool, unknown tool, a tool that raises, `reuse_skill` with a 2-frame skill) and for the loop (cases 1–8, plus `on_event` exceptions being ignored).

Proxy findings (`POC/` not read, see DEVIATIONS):
- (a) Client: `OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)` from `config`, then `client.chat.completions.create(model=LLM_MODEL, messages=..., tools=TOOL_SCHEMAS)`. This works with the proxy at `http://127.0.0.1:18080/v1`.
- (b) Assistant text never arrived together with tool calls. In all 29 replies across the 5 runs, a reply had either tool calls and no text, or text and no tool calls (only the final reply had text).
- (c) Several tool calls never arrived in one message. Every reply with tool calls had exactly one. The loop still handles several per message (test 3).

HUMAN check (real board on COM6 + LLM proxy; the user ran the CLI and pasted the output). The user confirmed that the grid matched in all 5 runs: the heart, a legible A, the heart again, three blinks, and the diagonal from top-left to bottom-right.

| # | Prompt | Turns | Row corrections | Result |
|---|---|---|---|---|
| 1 | Draw a heart and save it as a skill. | 6 | None | Heart in rows 0–5. The first `save_skill` used a wrong action shape (`{"type": "shift_out", ...}`), was rejected, and the agent fixed it. Saved `symbol_heart` v1. |
| 2 | Show the letter A. | 7 | Yes, a full frame resent, not a single row | The first frame was malformed (20 bytes, registers 02 and 04 repeated). The decoded rows were wrong and the agent saw that. The second frame is a 5x7 A in columns 1–5, rows 0–6. The first `save_skill` used the wrong action shape again; it was fixed and `glyph_a` v1 saved. |
| 3 | Show the heart again. | 3 | None | `list_skills` then `reuse_skill symbol_heart`, with no redraw, as expected. |
| 4 | Make the heart blink three times and save it as an animation. | 9 | None | Checked the heart and blank frames one by one. The first `save_skill` put a malformed `repeat` inside and was rejected. The second unrolled the 3 blinks (12 steps) instead of using `repeat`. Saved `anim_heartbeat` v1 and played it: `steps_run=12`, 2.25 s, ends blank. |
| 5 | Diagonal line, then report the rows. | 4 | None | Sent `0180 0240 ... 0801`. The reported rows match `decoded_state` exactly: `10000000` at the top through `00000001` at the bottom (top-left to bottom-right). |

Notes:
- Each CLI run starts with the start-up re-sync from `Bridge.start` (one extra `seq`).
- The agent never stated its plan in text before drawing (see finding b). The prompt asks for this, but in these runs the model sends text only in its final reply.
- The agent calls `read_shift_state` after `list_skills` in every drawing run. This is harmless.

Open issues:
- In 3 of 5 runs the first `save_skill` failed on the action shape (`{tool, args}`). The agent recovered each time from the validation error, but it costs a turn. The prompt and the `save_skill` schema do not show the skill JSON shape. That could be fixed later in a stage that allows prompt changes.
- In run 4 the agent unrolled the loop instead of using `repeat`, as the prompt asks. The saved skill is valid.
- In run 2 the agent corrected a wrong picture by resending the full frame, not single rows (the prompt asks for single rows).

## Stage 6 follow-up — skill example, repeat, intent (2026-10-07)

Not a new stage. Fixes three of the four stage 6 open issues. Firmware, bridge, skill store and runner are unchanged.

Files changed:
- `agent/prompts.py` (`SKILL_EXAMPLE`, `SKILL_KEYS_RULE`; new `Intent` and `Skill JSON` sections; repeat guidance under Animations; register-order rule (`01[r0]...08[r7]`, each register once) and a multi-row procedure: rebuild the full frame if several rows differ, no `save_skill` until all 8 rows match)
- `agent/tools.py` (optional `intent` on `shift_out`, `save_skill`, `reuse_skill`; `split_intent`; the `save_skill` description shows the rules and example)
- `agent/loop.py` (removes `intent` before dispatch and emits it as `agent_message` before `tool_call`)
- `tests/test_tools.py`, `tests/test_loop.py`, `tests/test_prompts.py` (new)
- `skills/library/anim_up_arrow_blink.json`, `skills/library/symbol_smiley.json` (saved by the agent during the HUMAN runs)
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 142 passed (the 128 existing tests + 14 new).

HUMAN check (real board on COM6 + LLM proxy; the user ran the CLI and pasted the output):

| # | Prompt | Turns | Result |
|---|---|---|---|
| 1 | Draw a smiley face and save it as a skill. | 6 | The frame wrote register 04 twice and never wrote 08, so rows 4–7 shifted up by one. The single-row "fix" `0881` changed the wrong row. The first `save_skill` passed and saved `symbol_smiley` v1, which was malformed. The user confirmed the board matched the printed map exactly. |
| 2 | Make an arrow pointing up blink four times and save it as an animation. | 7 | Both frames were correct first time. Saved as one `repeat` with count 4; the first `save_skill` passed (`anim_up_arrow_blink` v1). Played: `steps_run=16`, 3.0 s, ends blank. The user saw four blinks. |
| 3 | Show the smiley again. | 3 | `list_skills`, then `reuse_skill symbol_smiley`, no redraw. It replayed the flawed v1. |
| 4 | The saved smiley is wrong. Redraw ... save it again as symbol_smiley. (before the register rule) | 5 | The same register 04 mistake, plus a 9th message `0800`. The agent said it had checked every row and saved v2, still malformed. |
| 5 | Same prompt (after the register rule) | 5 | Frame `013C024203A5048105A506990742083C`: registers 01–08 once each, all 8 rows correct first time. Saved `symbol_smiley` v3. The user reports that the grid matches. |

Findings:
- In all 5 runs, an `[agent]` intent line appeared before every `shift_out`, `save_skill` and `reuse_skill` call, and never for the other tools.
- The first `save_skill` passed in every run (in stage 6 it failed in 3 of 5).
- The animation used `repeat` instead of unrolled frames.
- The LED map matched the board in every run the user checked.

Open issues:
- Still open: the agent may correct a wrong picture by resending the full frame instead of single rows. The new procedure allows a full resend when several rows differ.
- The register-order rule fixed the smiley in one run. More runs would show whether it holds for other pictures.
- `symbol_smiley` v1 and v2 are gone: the store keeps only the latest version in the file, now v3.

## Stage 7 — Server (2026-10-07)

Files created:
- `server/runs.py` (`RunBusy`, `RunManager`: `start`, `stop`, `busy`, `run_id`)
- `server/app.py` (`create_app`, module-level `app`; REST, `/ws`, 5 s health check, `RECORD_EVENTS` recording)
- `server/static/index.html` (placeholder: "Dashboard not installed" + link to `/api/status`)
- `tests/test_server.py`
- `logs/sample_events.jsonl` (recorded during the HUMAN check, 24 events; gitignored)

Files changed:
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 157 passed (142 from stages 1–6 + 15 stage 7). All 10 spec cases are covered. Case 3 is parametrised over `""` and `"   "`. Case 4 is parametrised with and without `intent`. Extra: `prompt_too_long`, stop when idle, and the placeholder page. The tests use `port="fake"`, tmp paths and a scripted LLM client. They never open COM6 or call the proxy.

HUMAN check (real board on COM6 + LLM proxy; the user ran the server with `RECORD_EVENTS=1` and the curl.exe commands):
1. The server started and the start-up re-sync restored the smiley. `/api/status` returned `{"connected":true,"busy":false,"run_id":null}`.
2. "Draw a heart" gave `r_0001`, and the heart appeared. The agent ran `list_skills` and then `reuse_skill symbol_heart` (intent `agent_message` before the call). `run_finished` status `completed`.
3. "Play the heart blink animation" gave `r_0002`. The agent called `reuse_skill anim_heartbeat`. A one-liner watched `/api/shift_state` and sent `/api/stop` as soon as the first frame (seq 59) arrived. The skill stopped after 219 ms (`steps_run=2`, `stopped: true`). `run_finished` status `stopped`. No blank frame was sent, and the heart stayed lit. The animation lasts only about 1.8 s, too short to stop by hand, hence the one-liner.
4. Board unplugged: `status connected:false` within the polling window. Board replugged: re-sync frame seq 60 (the heart), then `status connected:true`. The user saw the heart come back.
5. `logs/sample_events.jsonl` exists from this real run: 24 lines, all parseable (6 status, 2 run_started, 4 tool_call, 4 tool_result, 3 agent_message, 3 shift_state, 2 run_finished). Sending it to the dashboard developer is up to the user.

Notes:
- Agent, skills, bridge, store and firmware code are unchanged. Claude Code did not open the serial port or start the server.
- Interface differences from master 6.7 are listed in the stage report table (the same items as in DEVIATIONS "Stage 7").

Open issues:
- The status that a new WebSocket client receives on connect is not in the recording (it is not a broadcast event).
- The health check logs a full traceback every 5 s while the board is unplugged. This is noisy but harmless.

## Stage 8 Part A — Dashboard hookup (2026-10-07)

Part A only. Parts B (skill library) and C (runbook, README) are not started.

Files created:
- `server/static/`: `api.js`, `app.js`, `config.js`, `demo-recording.ndjson`, `favicon.svg`, `grid.js`, `icons.js`, `mock.js`, `README.md`, `store.js`, `style.css`, `trace.js`. All are copied from the frontend developer's `static/` folder. Its `src/`, `public/`, `package.json` and Vite files were not copied.
- `skills/library/pattern_clear.json`, `skills/library/symbol_diamond.json` (saved by the agent during the HUMAN checks)

Files changed:
- `server/static/index.html` (the placeholder is replaced by the dashboard's page)
- `server/static/api.js`, `app.js`, `style.css` (the three frontend edits, see DEVIATIONS "Stage 8 Part A")
- `tests/test_server.py` (`test_placeholder_page` → `test_dashboard_page`)
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

`config.js` is unchanged (`mock: null`, `apiBase: ""`), so the page runs live when the server serves it. Server, agent, skills, bridge, store and firmware code are unchanged.

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 157 passed (the placeholder test now checks the dashboard page).

HUMAN checks (real board on COM6 + LLM proxy; the user ran `uvicorn server.app:app --host 127.0.0.1 --port 8000` and checked in the browser):

| # | Check | Result |
|---|---|---|
| A1 | Page loads from the server, no console errors, no "Mock data" badge | Pass. Live, no simulation panel, full-width content. |
| A2 | On-screen grid equals the physical grid at load | Pass. Both showed the heart. |
| A3 | "Draw a heart": status line updates, both grids change together | Pass. The display was cleared first, so the change was visible. |
| A4 | Animation smooth and in step with the LEDs | Pass |
| A5 | Technical view: live map JSON, tool trace with thumbnails | Pass. The `reuse_skill` thumbnails show (edit a), and the trace text is larger (edit c). |
| A6 | Stop ends a running animation | Pass (`anim_test_blink`) |
| A7 | Unplug: offline shown and prompt disabled; replug: recovers | Pass |
| A8 | Reload mid-run: state restored from the server | Pass. The page was reloaded during "Draw a small diamond ... symbol_diamond"; busy state and map came back, and new trace entries kept arriving. |

Notes:
- Claude Code did not start the server or open the serial port. `POC/` was not read.
- After a reload, the trace shows only the events after the reload. The server keeps no event history, and the dashboard re-fetches status, map and frames only.

Open issues:
- `symbol_diamond` is small (rows 2–4 only: `00011000`, `00100100`, `00011000`). It was made only to test A8. Review it or delete it before the Part B library build.
- The health check still logs a traceback every 5 s while the board is unplugged (from stage 7).
- Parts B and C are still to do.

## Stage 8 Part B — Skill library (2026-10-07)

Part B only. Part C (runbook, README) is not started.

Files created:
- `scripts/build_library.py` (`python -m scripts.build_library [prompts_file] [--base-url URL]`)
- `scripts/library_prompts.txt` (the spec's 15 prompts + `pattern_clear`)
- `scripts/demo_video_script.py` (copy of `build_library.py` for the demo recording, user request)
- `skills/library/*.json` (saved by the agent during the build, see below)
- `skills/library_backup/` (copy of the 32 library files after the review)

Files changed:
- `bridge/bridge.py` (a failed reconnect logs one WARNING line instead of a traceback)
- `tests/test_bridge.py` (`test_reconnect_failure_logs_one_line`)
- `docs/DEVIATIONS.md`, `docs/PROGRESS.md`

Files deleted (user approved): `skills/library/anim_test_blink.json`, `symbol_diamond.json`, `pattern_clear.json` (the Part A one).

Test results: `.venv/Scripts/python.exe -m pytest -v` with Python 3.11.9: 158 passed (157 + the reconnect log test).

HUMAN check (real board on COM6 + LLM proxy; the user ran the server and `python -m scripts.build_library`): the user reported that all skills from the 16 prompts looked right on the grid. No retries or fixes were reported.

Final library (32 skills; steps = top-level actions as counted by the store):

| Skill | Version | Description |
|---|---|---|
| anim_bounce | 1 | Dot bounces left and right along row 4, 3 passes |
| anim_fill | 1 | Rows light bottom to top, then clear (17 steps) |
| anim_heartbeat | 2 | Heart alternating with a smaller heart, 6 beats |
| anim_up_arrow_blink | 1 | Up arrow blinks 4 times (stage 6 follow-up) |
| digit_0 … digit_9 | 1 | 5x7 digits |
| glyph_a … glyph_e, glyph_h, glyph_i, glyph_l, glyph_o | 1 | 5x7 uppercase letters |
| pattern_border, pattern_checker, pattern_clear | 1 | Border, checkerboard, all off |
| symbol_arrow_up, symbol_check, symbol_cross | 1 | Arrow up, check mark, X |
| symbol_heart | 1 | Heart (unchanged from stage 6) |
| symbol_smiley | 3 | Smiley (unchanged from the stage 6 follow-up) |
| word_hi | 1 | H then I, 600 ms apart (4 steps) |

All six dashboard suggestions are covered: `symbol_heart`, `glyph_a`, `digit_7`, `symbol_smiley`, `anim_heartbeat`, `pattern_clear`.

Notes:
- Claude Code did not start the server, open the serial port or call the LLM. `POC/` was not read. No skill JSON was written or edited by hand.
- `symbol_heart`, `glyph_a` and `symbol_smiley` kept their earlier versions; the agent did not save new versions for those prompts.
- `word_hi` holds its own copies of the H and I frames, because a skill cannot call another skill.

Open issues:
- Part C (runbook, README, rehearsal) is still to do.

## Stage 8 Part C — Runbook and README (2026-10-07)

Part C documents only. No code was changed.

Files created:
- `docs/RUNBOOK.md` (checklist, start commands, demo script, limits, failure playbook, reset)

Files changed:
- `README.md` (the stub is replaced: framing, architecture, setup, run, test, directory guide, "Deviations from the POC")
- `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest` with Python 3.11.9: 158 passed.

Demo script (8 prompts, chosen by the user):

| # | Prompt | Shows |
|---|---|---|
| 1 | Show a heart. | Reuse of `symbol_heart` |
| 2 | Play the heartbeat animation. | `anim_heartbeat` replayed by the runner |
| 3 | Say HI. | `word_hi` |
| 4 | Show the bouncing dot. | `anim_bounce` |
| 5 | Draw a star and save it as a skill named symbol_star. | Live composition, check against the map, correction, save |
| 6 | Show the star again. | Replay with zero LLM drawing steps |
| 7 | Count down 3, 2, 1. | Live composition from `digit_3`, `digit_2`, `digit_1` |
| 8 | What have you displayed in the last minute? | Reading history (`read_recent_frames`) |

The user asked for "checkerboard / bounce / fill" without picking one; the runbook uses `anim_bounce`.

Notes:
- Claude Code did not start the server, open the serial port or call the LLM. `POC/` was not read.
- All commands are PowerShell and call `.\.venv\Scripts\python.exe` directly. The server command is the one used in Part A (`uvicorn server.app:app --host 127.0.0.1 --port 8000`), run as `python -m uvicorn` through the venv. The proxy command (`uvx openai-api-server-via-codex`) comes from the stage 8 spec; earlier PROGRESS entries do not record it.
- The reset step restores the library by deleting `skills\library\*.json` and copying the backup, so a rehearsal's `symbol_star` is removed.

Open issues:
- The HUMAN rehearsal of the demo script, with timings, is not done yet (stage 8 "Done when").

## Agents stage 1 — Foundations and verification framework (2026-10-08)

No LLM calls. Nothing in `server/` changed; in `agent/` only the new `agent/evidence.py`.

Files created:
- `skills/pending.py` (`PendingSkillStore`: holds saves in memory, commits only on `commit()`)
- `agent/evidence.py` (`skill_outline`, `build_catalogue`, `frame_digest`)
- `tests/helpers.py` (`ScriptedClient`, `text_reply`, `tool_reply`, `shift_reply`, `plan_reply`, `verdict_reply`, `make_bridge`, `make_ctx`, `frame_hex`)
- `tests/test_grid_store_since.py`, `tests/test_pending.py`, `tests/test_evidence.py`
- `scripts/verify_common.py` (checks C1 to C6), `scripts/verify_stage1.py` (C1 to C7), `scripts/verify_all.py`
- `.venv/` (local virtual environment, not tracked)

Files changed:
- `.gitignore` (added `!*.md`)
- `config.py` (the seven constants of master 7.1, after `SKILL_TIME_CAP_S`)
- `bridge/grid_store.py` (added `GridStore.since`; only lines added)
- `docs/PROGRESS.md`, `docs/DEVIATIONS.md`

Test results (Python 3.11.9, `.\.venv\Scripts\python.exe`):
- Baseline before any change: 158 passed.
- After the stage: 187 passed (158 existing + 29 new).
- `scripts\verify_stage1.py`: `STAGE 1 VERIFY: PASS` (also run from a different current directory). `scripts\verify_all.py`: `VERIFY ALL: PASS`.

Notes:
- No serial port was opened, no server was started, the LLM was not called. Tests use `FakeTransport`, scripted clients and `tmp_path`.
- `GridStore.since` reads the whole log, so its cost grows with the log. The real `logs/shift_frames.jsonl` copied from `led_grid` is small (about 300 lines).
