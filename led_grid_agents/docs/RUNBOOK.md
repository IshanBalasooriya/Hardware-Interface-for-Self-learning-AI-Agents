# Demo runbook

All commands are for Windows PowerShell and run from `led_grid\` unless a step says otherwise. Every Python command uses the project's virtual environment directly (`.\.venv\Scripts\python.exe`), so no activation is needed. Do not use the `python` on PATH: it is MSYS2 Python 3.12 and has none of the dependencies.

## 1. Before the demo (checklist)

| # | Check | How |
|---|---|---|
| 1 | Board wired | VCC → 5V/VIN, GND → GND, DIN → GPIO 25, CLK → GPIO 26, CS → GPIO 27 (`docs\WIRING.md`). |
| 2 | Module orientation | Keep the module's marked edge at the top: the row driven by register `0x01` is the top row (calibrated in stage 3). If the module is rotated, the picture on the grid will not match the dashboard. |
| 3 | Firmware flashed | Already flashed in stage 3. Re-flash only if the board was reused; see "Re-flash the firmware" below. |
| 4 | `.env` has the right port | `Get-Content .env` shows `SERIAL_PORT=COM6`. |
| 5 | No other program holds the serial port | Close any serial monitor (`pio device monitor`, Arduino IDE, PuTTY) and any `scripts.run_skill` or `agent.cli` window. Only one program can hold the serial port at a time. While the server runs, it is that program. |
| 6 | Tests green | `.\.venv\Scripts\python.exe -m pytest` → 158 passed. The tests use the fake device and never open the port. |
| 7 | Library present | `(Get-ChildItem skills\library\*.json).Count` → `32`. If not, restore it (section 6). |
| 8 | LLM proxy running | Terminal 1 in section 2. `.env` has `OPENAI_BASE_URL=http://127.0.0.1:18080/v1`. |
| 9 | Server starts and the device is connected | Terminal 2 in section 2, then `curl.exe http://127.0.0.1:8000/api/status` returns `"connected":true`. |
| 10 | Dashboard live | `http://127.0.0.1:8000/` shows the grid and no "Mock data" badge. The on-screen grid equals the physical grid. |

### Re-flash the firmware (only if needed)

Close the server and any serial monitor first.

```powershell
cd firmware
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -t upload
cd ..
```

## 2. Start commands (in order)

Terminal 1, the LLM proxy (leave it running):

```powershell
uvx openai-api-server-via-codex
```

Terminal 2, the server (from `led_grid\`; leave it running). It opens COM6 and re-syncs the saved picture to the board on start:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Never add `--reload` or `--workers`: a second process would try to open the same serial port.

To also record the events to `logs\sample_events.jsonl`, start the server this way instead:

```powershell
$env:RECORD_EVENTS = "1"; .\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Terminal 3, the status check:

```powershell
curl.exe http://127.0.0.1:8000/api/status
```

Expected: `{"connected":true,"busy":false,"run_id":null}`.

Then open the dashboard in the browser:

```
http://127.0.0.1:8000/
```

During the demo:

- Do not reload the dashboard mid-demo. The server keeps no event history, so a reload loses the agent trace; after a reload only new events appear. The map, status and frame history do come back.
- The **Audience** tab shows the big grid and the prompt box. The **Technical** tab shows the live LED map JSON and the tool trace with thumbnails.
- The **Stop** button ends a running agent run or animation.

## 3. Demo script

Type each prompt into the dashboard's prompt box. Wait for the run to finish (status back to idle) before the next one. Prompts 1 to 4 replay skills that the agent built in Part B. Prompts 5 and 7 are composed live, so the exact frames and number of turns change from run to run.

| # | Prompt | What the audience sees | Idea it demonstrates |
|---|---|---|---|
| 1 | `Show a heart.` | The heart appears on the board and on screen at the same moment. The trace shows `list_skills`, then `reuse_skill symbol_heart`, with no drawing. | Reuse: the agent checks its library first and runs a saved skill in one call instead of redrawing. |
| 2 | `Play the heartbeat animation.` | The heart alternates with a smaller heart, 6 beats; the screen grid stays in step with the LEDs. | An animation is a saved JSON skill (`anim_heartbeat`) run by a fixed runner. The LLM makes one call; it does not send each frame. |
| 3 | `Say HI.` | H, then I, 600 ms apart. | A multi-frame word skill (`word_hi`) from the library. |
| 4 | `Show the bouncing dot.` | One dot runs left and right along row 4, 3 passes. | Another library animation (`anim_bounce`), all agent-made in Part B. |
| 5 | `Draw a star and save it as a skill named symbol_star.` | The agent states its intent, sends a frame with `shift_out`, and the star appears. Switch to the **Technical** tab: each `shift_out` result carries the decoded LED map, which the agent compares with what it meant to draw. If a row is wrong, it resends that row (or the full frame if several rows are wrong). Then `save_skill` and a "skill saved" entry. | Composing something new from generic primitives, checking it against the device-confirmed map, correcting it, and saving it. |
| 6 | `Show the star again.` | `list_skills`, then `reuse_skill symbol_star`. The star comes back with no `shift_out` from the LLM. | Replay with zero LLM drawing steps: the new skill is data, replayed by the runner. |
| 7 | `Count down 3, 2, 1.` | 3, 2 and 1 appear in turn. The trace shows how the agent built it, usually `reuse_skill` for `digit_3`, `digit_2`, `digit_1`. | Composing a new behaviour from existing skills. |
| 8 | `What have you displayed in the last minute?` | The agent calls `read_recent_frames` and answers in text with what was shown. The board does not change. | Reading history: the frame log is readable by the agent at any time, not only the current picture. |

Rehearsal timings are recorded in `docs\PROGRESS.md`.

## 4. What to say about limits

- The LED map (the on-screen grid and the JSON in the Technical tab) is the commanded state that the device confirmed. The bridge updates it only after the ESP32 replies `OK` to a command. It is not a light measurement: no sensor looks at the LEDs.
- So a loose wire, a dead LED or a module rotated the wrong way are invisible to the map. The map would still show the picture the chip was told to show. That is why we check the board with our eyes and keep the marked edge at the top.
- The map never changes from intent, from the LLM, or from the dashboard. A failed or timed-out command leaves it unchanged.
- On start-up and after a replug, the server re-sends the saved picture to the chip (re-sync), so the board and the map agree again.

## 5. Failure playbook

| Symptom | First action |
|---|---|
| Status shows device offline | Check the USB cable. Close any other serial program (only one program can hold COM6). Restart the server: `Ctrl+C` in terminal 2, then run the server command from section 2 again. |
| Grid shows garbage | Power-cycle the module (unplug and replug the board's USB). Restart the server if it does not recover; the start-up re-sync restores the saved picture. |
| Agent run hangs | Press **Stop** in the dashboard. Check terminal 1 (proxy) for errors. |
| Proxy down | Stop the server (`Ctrl+C` in terminal 2; it holds the serial port). Then demo saved skills with no LLM: `.\.venv\Scripts\python.exe -m scripts.run_skill symbol_heart` (any skill name from `skills\library\`, for example `anim_heartbeat` or `word_hi`). |
| Dashboard broken | Stop the server (`Ctrl+C` in terminal 2). Then run prompts from the terminal: `.\.venv\Scripts\python.exe -m agent.cli "Show a heart."` |
| A skill looks wrong | Stop the server and restore the library backup (section 6, step 3), then start the server again. |

## 6. Reset to clean state

1. Stop the server (`Ctrl+C` in terminal 2). Nothing else may hold the port or the files.
2. Clear the LED map and the frame log:

   ```powershell
   Remove-Item logs\shift_state.json, logs\shift_frames.jsonl -ErrorAction SilentlyContinue
   ```

   `logs\sample_events.jsonl` is the recorded event file for the dashboard developer. Keep it unless you mean to delete it: `Remove-Item logs\sample_events.jsonl`.
   After this, the next start begins at `seq` 1 and the re-sync blanks the board.
3. Restore the library from the backup. This also removes skills saved during a rehearsal, such as `symbol_star`:

   ```powershell
   Remove-Item skills\library\*.json
   Copy-Item skills\library_backup\*.json skills\library\
   (Get-ChildItem skills\library\*.json).Count
   ```

   The count must be `32`.
4. Start the server again (section 2).
