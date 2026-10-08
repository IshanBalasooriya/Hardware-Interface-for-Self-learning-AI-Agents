# LED Grid Agent Dashboard

A light-mode, read-only dashboard with a warm porcelain, aubergine, and soft matcha palette. Red is reserved for the physical LED visualisation and error text. The light theme and simulation lab deliberately follow the user's requested direction instead of the original spec's dark-only theme.

## Run without a build

This entire folder is a standalone deliverable. It needs no React, TypeScript, npm, bundler, fonts, or CDN resources.

From inside `static/`, serve the files over HTTP:

```sh
python -m http.server 8000
```

Open `http://localhost:8000/?mock=1`. Do not open `index.html` using `file://`.

The repository's `src/App.tsx` is a thin React host for these same modules. Its default hosted preview enables mock mode and performs one introductory heart run through the mock backend. Focusing the prompt or using a simulation control cancels a pending introductory run. An explicit `?mock=1` starts clean with all LEDs off, as specified.

## Mock and live modes

- `?mock=1`: blank initial matrix, online mock device, no backend requests.
- `?mock=1&offline=1`: start with the mock device offline.
- `?mock=0`: real backend, including in the hosted preview.
- With the standalone files and no query, mock mode is off.
- A boolean `CONFIG.mock` in `config.js` overrides the URL. Otherwise the URL chooses mock mode.

The non-removable **Mock data** badge is always visible in mock mode. To connect the backend, set `apiBase` and `wsPath` in `config.js`, if necessary. An empty `apiBase` uses the same origin; a nonempty value should be an absolute HTTP(S) origin.

## Simulation lab

Choose a scenario and press **Run simulation**. The lab sends a prompt to the mock client; it never writes to the map or toggles individual LEDs.

- Draw + correct: deliberately omit one pixel, explain the issue, correct the map, verify it, and save a mock skill.
- Heartbeat: 40 alternating frames at 80 ms intervals at 1x speed.
- Clear: receive a confirmed all-off map.
- Tool failure: show a failed call and a run error; subsequent runs still work.
- Test, unknown, and shutdown: exercise every grid display mode.
- Playback speed: 0.5x to 2x, applied to future runs.
- Device online switch: emit a backend status update to test offline behaviour. Disconnecting during a run also stops it.
- Stop: cancel all pending mock steps. It is available in both views during a run.

Suggested prompts for A, 7, and a smiley use additional backend fixtures so the suggested examples produce the expected shapes. All other drawing prompts use the heart correction fixture. Mock skill versions increment within the current page session only.

## Recording replay

Place an NDJSON recording in this folder, one raw backend event JSON object per line, each with a Unix-seconds `ts`.

Open `?mock=1&replay=demo-recording.ndjson` for the included example, or substitute another filename. Playback uses the original time gaps and the same `normalizeEvent` function as the live connection. Recordings must be same-origin. A failed file load appears as a readable run error.

For the hosted React preview, the included recording is also available at `/demo-recording.ndjson`.

## Architecture

- `api.js`: the only live protocol adapter. Owns REST paths, HTTP error classification, raw event names, tool summaries, and normalisation. Retries WebSocket connections with 1, 2, 4, 8, then 10-second delays.
- `mock.js`: in-browser backend and replay fixtures, exposing the same client interface. Command encoding exists only here as mock backend behaviour.
- `store.js`: one session-only state store. Discards older/equal map sequences, retains at most 30 frames and 500 trace entries, and rehydrates on reconnect regardless of sequence.
- `grid.js`: one renderer for audience, mini, and thumbnail grids. Creates 64 elements once, coalesces with requestAnimationFrame, and renders only map rows and display mode.
- `app.js`: wiring, tabs, simulation controls, JSON lines, and frame history. Both tabs stay mounted and live.
- `trace.js`: matched, expandable tool calls, decoded-map previews, and scroll-aware follow mode.
- `icons.js`: local inline SVG icons.

The normalised map preserves all incoming fields unchanged. Events retain the contract's original fields for client-interface compatibility and gain semantic internal names (`EVENTS`) so raw WebSocket type strings never appear in views. Status retains `run_id` and gains the internal `runId` alias. The adapter supplies display summaries and decoded-map previews; views treat tool arguments and results as opaque JSON. Unrecognised events are ignored; unrecognised tools render generically.

Backend strings are inserted using textContent, text nodes, or form values. Only hard-coded interface markup and local SVG icons use innerHTML. There is no storage, authentication, skill editing, direct LED control, or outgoing WebSocket traffic.

## Interface notes for backend

- The provisional contract remains isolated to `api.js` and mock fixtures. Update these after verification on hardware; no frontend protocol assumptions need to be copied into the backend.
- Initial snapshots are requested concurrently. Reconnection buffers incoming events until refreshed snapshots have been applied, then resumes sequence-ordered updates.
- A tool preview uses only `result.decoded_state`, not `args.data_hex`. Results without that optional field simply omit the preview.
- Tool calls are matched by both normalised run ID and call ID, allowing call IDs to be reused between runs.
- `bytes` is displayed verbatim. The real frontend never parses it. Unknown LED map fields are preserved in JSON.
- Normalised client errors have semantic codes; UI modules do not inspect HTTP status numbers.

## Verification

The React production build is checked with the project's build task. The standalone handoff is source HTML, CSS, and ES modules and requires no build. Interactive browser checks and a real hardware connection must still be verified in the target Chrome/Edge environment; no physical backend is included.