# LED Grid System

This is the final demo of a general LLM-to-hardware interface. An LLM agent acts on hardware only through a small set of generic primitives (`shift_out` to clock bytes out of three pins, `wait`), observes the result the device confirmed, and saves what works as reusable JSON **skills**. A fixed runner replays skills without the LLM. No generated code is ever executed: skills are data.

The demonstration vehicle is an 8x8 red LED matrix (MAX7219 module) on an ESP32. The agent shows letters, digits, symbols, patterns and animations on it, but nothing in the hardware layer is display-specific: the firmware knows only `PING` and `SHIFT_OUT` with a pin allowlist, and the agent learns the MAX7219's registers from the prompt and from the decoded LED map it gets back after every command. The full design is in `docs/00_MASTER.md`.

## Architecture

```
Dashboard (browser)  <-- REST + WebSocket -->  server/   owns serial port, one run at a time
                                                 |
                                               agent/    LLM loop + tool registry
                                                 |
                                               skills/   skill store + runner (no LLM)
                                                 |
                                               bridge/   Bridge: transport + LED map store
                                                 |  serial
                                               firmware  PING, SHIFT_OUT, pin allowlist
                                                 |
                                               MAX7219 module
```

Imports go one way: `server -> agent -> skills -> bridge`. The LED map is updated in the bridge only after the device replies `OK`. It is commanded state confirmed by the device, not a measurement of light.

## Setup

Windows PowerShell, from `led_grid\`. Python 3.11 is required; the `python` on PATH on the development machine is MSYS2 3.12, so the venv is created with the `py` launcher.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set `SERIAL_PORT` to the board's port (`COM6` on the development machine; `fake` runs the in-process fake device). `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `LLM_MODEL` point at the LLM proxy.

Hardware: wiring and orientation calibration are in `docs/WIRING.md`. The firmware is in `firmware/` (PlatformIO, board `esp32doit-devkit-v1`):

```powershell
cd firmware
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -t upload
cd ..
```

## Run

Terminal 1, the LLM proxy:

```powershell
uvx openai-api-server-via-codex
```

Terminal 2, the server (one process only, it holds the serial port; never use `--reload` or `--workers`):

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open the dashboard at `http://127.0.0.1:8000/`.

Without the server (each of these opens the serial port itself, so stop the server first):

```powershell
.\.venv\Scripts\python.exe -m agent.cli "Draw a heart"
.\.venv\Scripts\python.exe -m scripts.run_skill symbol_heart
```

The demo procedure, checklist and failure playbook are in `docs/RUNBOOK.md`.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests run on the fake device with a scripted LLM client. They never open the serial port or call the proxy.

## Directory guide

| Path | Contents |
|---|---|
| `config.py` | All constants; reads `.env` |
| `firmware/` | ESP32 firmware: `PING`, `SHIFT_OUT`, pin allowlist |
| `bridge/` | `grid_model.py` (software copy of the chip), `grid_store.py` (LED map, frame log), `transport.py` (serial), `fake_device.py`, `bridge.py` (`shift_out`, `wait`, re-sync, reconnect) |
| `skills/` | `store.py` (list, get, save, validation), `runner.py` (replays a skill through the bridge) |
| `skills/library/` | The skills, all saved by the agent through its tools |
| `skills/library_backup/` | Known-good copy of the library |
| `agent/` | `tools.py` (tool schemas and registry), `prompts.py`, `loop.py`, `cli.py` |
| `server/` | `app.py` (REST, WebSocket, static files), `runs.py` (one run at a time, stop flag), `static/` (dashboard) |
| `scripts/` | `manual_patterns.py`, `run_skill.py`, `build_library.py` (+ `library_prompts.txt`), `demo_video_script.py` |
| `logs/` | `shift_state.json`, `shift_frames.jsonl`, `sample_events.jsonl` (gitignored) |
| `tests/` | pytest suite |
| `docs/` | Master plan, stage files, `WIRING.md`, `RUNBOOK.md`, `PROGRESS.md`, `DEVIATIONS.md` |

## Deviations from the POC

The earlier proof of concept is in `POC/` (unchanged). This system differs from it in these ways:

- **Grid-only firmware.** The firmware has two commands, `PING` and `SHIFT_OUT`, and allows only the bound device's pins (25, 26, 27). It enforces that allowlist itself and does not trust the Python side.
- **Single agent loop.** One conversation loop (`agent/loop.py`) with one tool registry (`agent/tools.py::call_tool`). Every hardware or file action by the agent goes through that registry.
- **`reuse_skill`.** The agent can run a saved skill in one tool call. The skill runner then sends the frames, with no LLM step per frame.
- **LED map persisted and re-synced.** The map is saved to `logs/shift_state.json` after every confirmed command, with a frame log in `logs/shift_frames.jsonl`. On start-up and after a replug, the bridge re-sends the saved state, so the board and the map agree.

Firmware, transport, agent loop, server and dashboard hookup were written from the stage specs without reading `POC/`. Every change to the spec (for example the optional `intent` argument on `shift_out`, `save_skill` and `reuse_skill`, and the server's interface details) is listed in `docs/DEVIATIONS.md`.
