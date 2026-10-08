# Deviations

## Stage 1

- What: added `.venv/` to `.gitignore` (not in the stage 1 list).
  Why: the user chose a local virtual environment in `led_grid/.venv` (Python 3.11.9 via `py -3.11`; the `python` on PATH is MSYS2 3.12, which cannot install the wheels in `requirements.txt`).
  Files: `.gitignore`.

## Stage 3

- What: the firmware was written from scratch, without reading `POC/`. Board settings come from the stage file.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `firmware/platformio.ini`, `firmware/src/main.cpp`.
- What: H1 (flash) is done by the user. Claude Code only builds (`pio run`) and does not upload or open the serial port.
  Why: user instruction.
  Files: none.
- What: H3 adds `SHIFT_OUT 25 26 27 2 0100` (clear the top row) before `SHIFT_OUT 25 26 27 2 0880`.
  Why: as written, step 2's top-row LED stays lit during step 3, so two LEDs would show instead of "the lit LED is now in the bottom row". The user chose this fix.
  Files: `docs/WIRING.md`.
- What: interpretation choices not spelled out in the spec, the same in firmware and fake:
  - The line buffer holds at most 255 characters (256 bytes with the terminator). A trailing `\r` counts toward the limit. The longest valid command is about 155 characters.
  - `PING` followed by extra tokens replies `ERR BAD_ARGS`.
  - "Decimal integer" means 1 to 9 ASCII digits. Signs and longer numbers reply `ERR BAD_ARGS`.
  Files: `firmware/src/main.cpp`, `bridge/fake_device.py`.

## Stage 4

- What: the transport was written from the stage spec alone, without reading `POC/`.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `bridge/transport.py`.
- What: the user runs `scripts/manual_patterns.py` against the real port. Claude Code does not open the serial port.
  Why: user instruction.
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `SerialTransport` reads with a 0.1 s slice and buffers partial lines, so a read timeout in the middle of a line never produces a broken reply. `send` waits up to `SERIAL_TIMEOUT_S` in total.
  - `FakeTransport.send` raises `TransportError` when the fake device gives no reply (empty line), the same as a timeout.
  - `Bridge.shift_out` treats any reply other than exactly `OK` as a failure (`error` = reply text), not only replies that start with `ERR`.
  - Python-side validation rejects `bool` as an int.
  Files: `bridge/transport.py`, `bridge/bridge.py`.

## Stage 5

- What: the user runs `scripts/run_skill.py` against the real port. Claude Code does not open the serial port. `anim_test_blink` was written by calling `SkillStore.save`.
  Why: user instruction.
  Files: `skills/library/anim_test_blink.json`.
- What: interpretation choices not spelled out in the spec:
  - The name check (`^[a-z0-9_]{1,40}$`) is done in `SkillStore.get`/`save`, because `validate(definition)` has no name. This also blocks path traversal.
  - Each action must be exactly `{tool, args}`, and `args` must hold exactly the keys its tool needs (no missing or extra keys). Ints exclude `bool`. A skill's `data_hex` must be non-empty. Size limits are left to the bridge.
  - "Nesting depth at most 3" means at most 3 nested `repeat` levels.
  - `run_skill` validates the definition first and returns `{"success": False, "error": <message>}` if it is invalid. After `$name` substitution it validates again, and a missing, wrong-typed or out-of-range value returns `bad_params`. Nothing is sent in either case.
  - To apply the time cap during a wait, the runner passes `bridge.wait` a stop callable that combines the caller's `should_stop` with the deadline.
  - `steps_run` counts only actions that succeeded. A failed `shift_out` is not counted.
  - `scripts/run_skill.py` converts a `key=value` value to int only when the skill's default for that key is an int, so hex strings stay strings.
  - The HUMAN log check shows 11 new lines: 1 start-up re-sync from `Bridge.start`, then the skill's 10 frames.
  Files: `skills/store.py`, `skills/runner.py`, `scripts/run_skill.py`.

## Stage 6

- What: `POC/` was not read. The LLM client is built from config alone: `OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)`, Chat Completions with `tools=TOOL_SCHEMAS`, model `LLM_MODEL`. Findings (b) and (c) come from the real HUMAN runs, not from the POC.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `agent/loop.py`.
- What: the loop logs one INFO line per model reply (`reply N: text=yes/no tool_calls=K`) through stdlib `logging`, and `agent/cli.py` turns INFO logging on (`httpx` request lines are silenced).
  Why: the loop's events cannot show turn boundaries, so this line is how the real runs answer (b) and (c).
  Files: `agent/loop.py`, `agent/cli.py`.
- What: the user runs `python -m agent.cli` against the real port and the LLM proxy. Claude Code does not open the serial port.
  Why: user instruction (same as stages 4 and 5).
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `call_tool` rejects unexpected argument keys as well as missing or wrong-typed ones (`bad_args: <detail>`). Ints exclude `bool`. `read_recent_frames` also rejects `count` outside 1..`MAX_HISTORY`.
  - `SkillError` becomes `error` with its message. Any other exception becomes `"<Type>: <message>"`, cut to 200 characters.
  - Tool-call arguments that are not a JSON object (invalid JSON, or valid JSON that is not an object) are not executed. `tool_call` is still emitted, with `args` set to the raw string, followed by a `tool_result` holding the `bad_args` result. That result is returned to the model.
  - `summary` for `stopped` and `max_turns` is the last text the model sent. For `completed` it is the final reply's text.
  - Creating the default client is inside the error handling, so a bad config returns `error` instead of raising.
  - The `shift_out` schema says that the byte count must be a multiple of `group_size`, because the firmware rejects other counts.
  Files: `agent/tools.py`, `agent/loop.py`.

## Stage 6 follow-up

- What: `shift_out`, `save_skill` and `reuse_skill` take an optional string argument `intent` (one short sentence: what the call is for). This adds an optional argument to three rows of the master 6.5 tool table.
  - The loop removes it (`agent/tools.py::split_intent`) before calling `call_tool`, so bridge, store and runner never see it. `call_tool` itself is unchanged, and an `intent` passed to any other tool still gets `bad_args`.
  - A non-empty `intent` is emitted as an `agent_message` event just before that call's `tool_call` event. The `tool_call` args do not include it. It does not change `summary`. A non-string or blank `intent` is dropped without an event.
  - Saved skills never contain `intent`: it is a top-level tool argument, not part of `definition` (and the store rejects unknown keys in `definition`).
  - The assistant message sent back to the model keeps the raw arguments, including `intent`.
  Why: the model sends text only in its final reply (stage 6 finding b), so the user could not see what each call was for.
  Files: `agent/tools.py`, `agent/loop.py`, `agent/prompts.py`.
- What: the system prompt gains two sections beyond the spec's ten: `Intent` and `Skill JSON` (after "Procedure for drawing"). `Skill JSON` lists the exact keys that `skills/store.py` accepts and shows one complete, valid example (`SKILL_EXAMPLE`, built from config: the top-left LED blinks 3 times using `repeat`, `shift_out` and `wait`). The `save_skill` description contains the same rules and example. The Animations section now tells the agent to use `repeat` instead of writing out repeated frames.
  Why: fixes for the stage 6 open issues (first `save_skill` rejected on shape, unrolled animation).
  Files: `agent/prompts.py`, `agent/tools.py`.
- What: "Picture to bytes" now says that a full frame is registers 01 to 08 in order, each exactly once. It shows the template `01[r0]02[r1]...08[r7]`, generated in code. The compare step in "Procedure for drawing" now says: if exactly one row differs, resend only that row; if several rows differ, rebuild and resend the full frame; do not call `save_skill` until all 8 rows match. Spec item 4 asks only for single-row resends.
  Why: in two HUMAN runs the agent sent register 04 twice (and 08 never, or an extra `0800`), which shifted the smiley. Its single-row "correction" fixed the wrong row, and it saved the malformed frame (`symbol_smiley` v1 and v2).
  Files: `agent/prompts.py`.

## Stage 7

- What: `POC/` was not read. The server, including how worker-thread events reach WebSocket clients, was written from the stage spec alone.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `server/app.py`, `server/runs.py`.
- What: the user runs the server against the real board and the LLM proxy. Claude Code does not open the serial port.
  Why: user instruction (same as stages 4 to 6).
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `server/app.py` has a factory `create_app(port, state_file, frames_log, skills_dir, events_log, client, transport)`. `app = create_app()` uses the config values. Tests pass `port="fake"`, tmp paths and a scripted client, so they never open the real port or call the real LLM. All I/O happens in the lifespan, so importing the module opens nothing.
  - The lifespan calls `GridStore.load()` before `bridge.start()`. If the board is absent at startup, `start()` fails before its own `load()`, and a later `reconnect()` would re-sync a blank map. Loading first keeps the saved picture. Bridge code is unchanged.
  - `RunManager` does not know the bridge. Its `status` events carry `busy` and `run_id`, and the app adds `connected` before broadcasting.
  - Order: `emit` stamps `ts`, appends to the events file and queues the event under one lock. One sender task sends everything, so the file and every client see the same order.
  - The `status` sent to a new WebSocket client on connect goes only to that client. It is not an emitted event, so it is not in `EVENTS_LOG`.
  - `RECORD_EVENTS` is read at startup (lifespan). The events file is appended to, never truncated.
  - The 5 s health check skips while a run is busy. It broadcasts `status` when `connected` differs from the last value it broadcast.
  - The prompt is trimmed before the 500-character check. Check order: empty, too long, offline, busy.
  - A body without `prompt` counts as empty (400). Invalid JSON, or a `count` that is not an integer, gets FastAPI's 422.
  Files: `server/app.py`, `server/runs.py`, `tests/test_server.py`.

## Stage 8 Part A

- What: `POC/` was not read. The dashboard files were copied unchanged from the frontend developer's `static/` folder (not its `src/`, `public/`, `package.json` or Vite files), apart from the three edits below.
  Why: user instruction for this session.
  Files: `server/static/*`.
- What: `config.js` keeps `mock: null` and `apiBase: ""`, instead of the spec's `mock: false`. With `null`, `app.js` runs live unless the URL has `?mock=1`, so a plain `http://127.0.0.1:8000/` is live.
  Why: user instruction.
  Files: `server/static/config.js`.
- What: three frontend edits, made at the user's request. Two of them touch view code (`app.js`, `style.css`), which the stage says not to edit.
  - `api.js`: the `tool_result` preview uses `result.decoded_state`, falling back to `result.final_state` (`reuse_skill`) and then `result.state` (`read_shift_state`). Before this, only `shift_out` results had a thumbnail.
  - `app.js` + `style.css`: in live mode the whole simulation side panel (including the "Open mock mode" link) is hidden, and the root gets the class `is-live`. `.dashboard.is-live .workspace` uses a single column, so the main content takes the freed width. Mock mode is unchanged.
  - `style.css`: the agent trace is larger for a projector. `.agent-text` and `.tool-name` are 16px in `var(--ink)` (primary text colour), and `.tool-arguments` is 13px. The rules sit at the end of the file, so they override the media-query sizes too.
  Why: the demo is shown on a projector. The simulation panel does nothing in live mode, and `reuse_skill`/`read_shift_state` results had no thumbnail.
  Files: `server/static/api.js`, `server/static/app.js`, `server/static/style.css`.
- What: `api.js` needed no other change for the stage 7 interface. It passes the raw REST and event fields through, so nullable `intensity`, `warnings` and the skill `description` already work.
  Files: none.
- What: the stage 7 placeholder test became `test_dashboard_page`. It checks that `/` serves the dashboard's `index.html` (`id="led-dashboard"`, `./app.js`) and not the placeholder.
  Files: `tests/test_server.py`.

## Stage 8 Part B

- What: a failed reconnect now logs one WARNING line (`Reconnect failed: <error>`) instead of a full traceback. The traceback came from `Bridge.reconnect` (`logger.exception`), not from the server's health loop: `reconnect` catches the error itself. New test `test_reconnect_failure_logs_one_line`. The stage says not to change bridge code; the user allowed this one fix.
  Why: while the board was unplugged, the 5 s health check printed a full traceback every tick (stage 7 open issue).
  Files: `bridge/bridge.py`, `tests/test_bridge.py`.
- What: `scripts/library_prompts.txt` has one extra line beyond the spec list: `Create a skill named pattern_clear that turns every LED off.`
  Why: the dashboard suggests "Clear the display", and the agent-made `pattern_clear` from Part A was deleted with the other test skills, so it is made again by the agent.
  Files: `scripts/library_prompts.txt`.
- What: `scripts/build_library.py` waits for a run by polling `/api/status` (0.5 s), not over the WebSocket. It uses the standard library only. It prints the skills saved or changed (from `/api/skills`) and the final map, not the run's `summary`.
  Why: the spec allows polling, and a WebSocket client would need a package outside R8's list.
  Files: `scripts/build_library.py`.
- What: `scripts/demo_video_script.py` is a copy of `build_library.py` (only the docstring differs), for recording a demo video. Pass a prompts file as the argument.
  Why: user request.
  Files: `scripts/demo_video_script.py`.
- What: before the build, the user approved deleting `anim_test_blink`, `symbol_diamond` and the Part A `pattern_clear`. `anim_up_arrow_blink` was kept.
  Files: `skills/library/`.

## Agents stage 1

- What: `led_grid_agents/.venv` did not exist. Created with `py -3.11 -m venv .venv` (Python 3.11.9) and `pip install -r requirements.txt` (no new packages), then the baseline run (158 passed).
  Why: the stage file says to stop if `.venv` is missing; the owner chose to have Claude Code create it.
  Files: `.venv/` (ignored by git).
- What: `skills/pending.py` starts with `from __future__ import annotations`.
  Why: the required method name `list` shadows the builtin inside the class body, so the annotation `list[tuple[str, dict]]` on `held()` fails at import without it.
  Files: `skills/pending.py`.
- What: interpretation choices not spelled out in the plan:
  - `PendingSkillStore` holds the definition in the shape `SkillStore.save` would write (`type`, `version`, `description`/`params` if given, `actions`), so `get_skill` on a held skill shows its future version. `get` and `held` return deep copies. `list()` is sorted by name, like the real store.
  - The version a held save reports is computed from the real store at save time. Saving a held name again reports the same version.
  - `skill_outline`: frames are labelled `A`, `B`, ... (after `Z`: `F27`, ...) by the first appearance of each distinct `data_hex`; a `$name` `data_hex` is labelled `$name` and counts as one distinct frame. A `$param` count or duration with no usable integer default counts as 0. Waits show as `wait 250` or `wait $ms`.
  - `skill_outline` includes `params`, but a string param value that holds 8 or more hex digits in a row (a `$param` frame) is shown as `<frame data>`, because the outline must contain no hex.
  - `frame_digest`: `pictures` lists every picture seen in all frames (so every key of `counts` is in it), labelled `P1`, `P2`, ... by first appearance. `lit` counts `1` characters in `rows`. `gaps_ms` covers only the kept `sequence` (at most `max_frames - 1` gaps), rounded to 10 ms with Python `round`. A frame without `timestamp` counts as time 0.
  - `GridStore.since` treats a `bool` `seq` as not an integer.
  - Verify scripts: C3 compares the nine protected Python files and every file under `firmware/` (excluding `.pio/`) in both trees; a file that exists on only one side counts as changed. C4 compares every file in `../led_grid/tests/` (top level). C5 scans `import`/`from` lines in every `.py` under `bridge/`, `skills/`, `agent/`. `run_common` takes `allow_server_mode_lines` for stage 4. Each failing check prints its detail lines indented under the row.
  Files: `skills/pending.py`, `agent/evidence.py`, `bridge/grid_store.py`, `scripts/verify_common.py`, `scripts/verify_stage1.py`.
