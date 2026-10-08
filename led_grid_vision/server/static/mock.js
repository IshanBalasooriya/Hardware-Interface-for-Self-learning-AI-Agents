import { normalizeEvent, normalizeStatus, clientError, EVENTS } from "./api.js";

const BLANK = Array(8).fill("00000000");
const HEART = ["00000000", "01100110", "11111111", "11111111", "01111110", "00111100", "00011000", "00000000"];
const SMALL = ["00000000", "00000000", "00100100", "01111110", "00111100", "00011000", "00000000", "00000000"];
const LETTER = ["00011000", "00100100", "01000010", "01000010", "01111110", "01000010", "01000010", "00000000"];
const SEVEN = ["00000000", "01111110", "00000100", "00001000", "00010000", "00100000", "00100000", "00000000"];
const SMILE = ["00000000", "01100110", "01100110", "00000000", "01000010", "00111100", "00000000", "00000000"];
const ALL_ON = Array(8).fill("11111111");
const corner = (r, c) => BLANK.map((row, i) => i === r ? row.slice(0, c) + "1" + row.slice(c + 1) : row);
const CHECKER = Array.from({ length: 8 }, (_, r) => (r % 2 ? "01" : "10").repeat(4));
// Camera mock (stage 5): sample frames and readouts in the shape the server sends. The real values come
// from the server's viewfinder code; these are fixtures only.
const CALIBRATION_STEPS = [["calibrate 1 all_off", BLANK], ["calibrate 2 all_on", ALL_ON], ["calibrate 3 corner_tl", corner(0, 0)],
  ["calibrate 4 corner_tr", corner(0, 7)], ["calibrate 5 corner_bl", corner(7, 0)], ["calibrate 6 corner_br", corner(7, 7)],
  ["verify checker_0", CHECKER], ["verify checker_1", CHECKER.map(row => row.split("").reverse().join(""))]];
const POSITION_STEPS = [["check position", BLANK], ["check position", ALL_ON], ["check position", corner(0, 0)],
  ["check position", corner(0, 7)], ["check position", corner(7, 0)], ["check position", corner(7, 7)]];
const READOUTS = [
  ["GRID NOT FOUND", null, false, "SEARCHING", { ready: "red", px_per_led: "red", in_frame: "red", lock: "amber" }],
  ["MOVE CLOSER", 6.8, true, "SEARCHING", { ready: "red", px_per_led: "red", in_frame: "green", lock: "amber" }],
  ["READY", 9.6, true, "SEARCHING", { ready: "green", px_per_led: "amber", in_frame: "green", lock: "amber" }],
  ["READY", 14.7, true, "LOCKED", { ready: "green", px_per_led: "green", in_frame: "green", lock: "green" }]
];
const CALIBRATION_SUMMARY = ["Calibration OK", "  min pitch        14.66 px   (minimum 8.0)", "  sample radius    3.10 px   channel green",
  "  orientation      rot90   mirrored False", "  separation       min 191.4   median 203.8   (minimum 25.0)",
  "  verification     8 patterns, 512 cells, 0 wrong, 0 uncertain"].join("\n");

export function createMockClient(config) {
  const params = new URLSearchParams(location.search);
  const events = new Set(), connections = new Set(), timers = new Set(), skills = new Map();
  let connected = params.get("offline") !== "1", busy = false, runId = null, runCount = 0;
  let speed = 1, started = false, disposed = false, callCount = 0, replay = null, replayError = null;
  let map = { seq: 0, timestamp: Date.now() / 1000, display: "on", intensity: 2, rows: [...BLANK], bytes: "01000200030004000500060007000800" };
  let frames = [map];
  const vision = { enabled: true, camera: "ok", calibrated: false, calibration_created: null, last_status: null,
    position_ok: null, preview_active: false, operation: null, light: false };
  let metrics = readout(0), observedSeq = 0, lightRestore = null, relockTimer = 0;
  const clone = value => JSON.parse(JSON.stringify(value));
  function emit(raw) {
    if (disposed) return;
    const event = normalizeEvent({ ts: Date.now() / 1000, run_id: runId, ...raw });
    if (!event) return;
    if (event.kind === EVENTS.STATUS) {
      connected = event.status.connected; busy = event.status.busy; runId = event.status.runId;
    }
    if (event.kind === EVENTS.MAP && event.map) {
      map = event.map; frames = [...frames, map].slice(-config.maxHistoryFrames);
    }
    events.forEach(fn => fn(event));
  }
  function later(delay, fn) {
    const timer = setTimeout(() => { timers.delete(timer); if (!disposed) fn(); }, delay / speed);
    timers.add(timer);
  }
  function cancel() { timers.forEach(clearTimeout); timers.clear(); }
  function status() { emit({ type: "status", connected, busy, run_id: runId }); }
  function begin(prompt) {
    busy = true; runId = `r_${String(++runCount).padStart(4, "0")}`;
    emit({ type: "run_started", prompt }); status();
    return runId;
  }
  function finish(outcome = "completed", summary = "Done.", error = null) {
    emit({ type: "run_finished", status: outcome, summary, error });
    busy = false; runId = null; status();
  }
  function call(tool, args = {}) {
    const id = `c_${++callCount}`;
    emit({ type: "tool_call", call_id: id, tool, args });
    return id;
  }
  function result(id, tool, data, duration = 38) {
    emit({ type: "tool_result", call_id: id, tool, result: data, duration_ms: duration });
  }
  // Command encoding belongs to this mock backend, never to the grid or views.
  function encode(rows) {
    return rows.map((row, index) => `${String(index + 1).padStart(2, "0")}${parseInt(row.replaceAll("?", "0"), 2).toString(16).padStart(2, "0")}`).join("").toUpperCase();
  }
  function newFrame(rows, display = "on") {
    const state = { seq: map.seq + 1, timestamp: Date.now() / 1000, display, intensity: 2, rows: [...rows], bytes: encode(rows) };
    emit({ type: "shift_state", state });
    return state;
  }
  function readout(step, label = "live") {
    const [ready, px, inFrame, lock, colours] = READOUTS[step];
    const calibrated = vision?.calibrated;
    return { ready: calibrated ? "READY" : ready, px_per_led: calibrated ? 14.7 : px, in_frame: calibrated || inFrame,
      sharpness: [41, 96, 212, 319][step], lock: calibrated ? "CALIBRATED" : lock, label, width: 1280, height: 720, fps: 25.9,
      colours: calibrated ? { ready: "green", px_per_led: "green", in_frame: "green", sharpness: "neutral", lock: "green" } : { ...colours, sharpness: "neutral" } };
  }
  function setMetrics(value) { metrics = value; emit({ type: "vision_metrics", metrics: value }); }
  function soon(delay, fn) { const timer = setTimeout(() => { if (!disposed) fn(); }, delay); return timer; }
  const pause = ms => new Promise(resolve => soon(ms, resolve));
  function ranges(cols) {
    const parts = []; let start = cols[0];
    cols.forEach((col, i) => { if (cols[i + 1] !== col + 1) { parts.push(start === col ? `${col}` : `${start}-${col}`); start = cols[i + 1]; } });
    return parts.join(", ");
  }
  // Mock backend only: the server computes physical_check in Python (vision/ledmap.py).
  function observation(rows, display = "on", covered = []) {
    const want = display === "shutdown" ? BLANK : display === "test" ? ALL_ON : rows;
    if (vision.camera !== "ok" || !vision.calibrated) {
      return { state: { seq: ++observedSeq, timestamp: Date.now() / 1000, display: "unknown", intensity: null, rows: Array(8).fill("????????"), bytes: null,
        warnings: [], source: "camera", vision: { status: "uncalibrated", uncertain: 64, read_ms: 0 } },
        check: { result: "unavailable", mismatches: [], warnings: [], summary: "Camera unavailable (camera not calibrated); the picture is not camera-confirmed." } };
    }
    const seen = want.map((row, r) => row.replaceAll("?", "0").split("").map((bit, c) => covered.some(([cr, cc]) => cr === r && cc === c) ? "0" : bit).join(""));
    const mismatches = [];
    want.forEach((row, r) => row.split("").forEach((bit, c) => { if ("01".includes(bit) && bit !== seen[r][c]) mismatches.push({ row: r, col: c, commanded: bit, observed: seen[r][c] }); }));
    const lit = seen.join("").includes("1"), wanted = want.join("").split("1").length - 1;
    const byRow = new Map(); mismatches.forEach(m => byRow.set(m.row, [...(byRow.get(m.row) ?? []), m.col]));
    const where = [...byRow].map(([r, cols]) => `row ${r} col${cols.length > 1 ? "s" : ""} ${ranges(cols)}`).join(", ");
    const check = !lit ? (wanted ? { result: "not_visible", summary: `Camera sees no lit LEDs, but ${wanted} should be lit (covered, moved, disconnected or unpowered?).` } :
      { result: "consistent_dark", summary: "Camera sees the display dark, as commanded." }) :
      mismatches.length ? { result: "mismatch", summary: `Camera sees ${mismatches.length} LED${mismatches.length > 1 ? "s" : ""} differ from the command (${where}).` } :
      { result: "match", summary: "Camera confirms the picture (64/64 LEDs)." };
    vision.last_status = lit ? "ok" : "dark";
    return { state: { seq: ++observedSeq, timestamp: Date.now() / 1000, display: lit ? "on" : "unknown", intensity: null, rows: seen, bytes: null,
      warnings: [], source: "camera", vision: { status: vision.last_status, uncertain: 0, read_ms: 512 } },
      check: { ...check, mismatches, warnings: [] } };
  }
  function observe(rows, display, covered) {
    if (display === "unknown") return null;
    const seen = observation(rows, display, covered);
    emit({ type: "observed_state", state: seen.state, physical_check: seen.check });
    return seen;
  }
  function shift(rows, display = "on", covered = []) {
    const id = call("shift_out", { data_pin: 25, clock_pin: 26, latch_pin: 27, group_size: 2, data_hex: encode(rows) });
    later(70, () => {
      const state = newFrame(rows, display);
      const seen = observe(rows, display, covered);
      result(id, "shift_out", { success: true, decoded_state: state, ...(seen ? { observed_state: seen.state, physical_check: seen.check } : {}) });
    });
  }
  async function visionTask(operation, steps, finish) {
    if (busy) throw clientError(409, "run_in_progress");
    const before = clone(lightRestore ?? map);
    busy = true; vision.operation = operation; status();
    emit({ type: "vision_status", operation, phase: "started", status: clone(vision) });
    for (const [label, rows] of steps) { newFrame(rows); setMetrics(readout(3, label)); await pause(320); }
    newFrame(before.rows, before.display); lightRestore = null; vision.light = false;
    const outcome = finish();
    vision.operation = null; setMetrics(readout(3));
    emit({ type: "vision_status", operation, phase: "finished", status: clone(vision), result: outcome });
    busy = false; status();
    return clone(outcome);
  }
  const ready = (async () => {
    const file = params.get("replay");
    if (!file) return;
    try {
      const url = new URL(file, location.href);
      if (url.origin !== location.origin) throw new Error("Recordings must be same-origin files.");
      const response = await fetch(url);
      if (!response.ok) throw new Error("Recording not found.");
      replay = (await response.text()).split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
    } catch (error) { replayError = `Could not load recording: ${error.message}`; }
  })();

  const api = {
    getStatus: async () => { await ready; return normalizeStatus({ connected, busy, run_id: runId }); },
    getShiftState: async () => { await ready; return clone(map); },
    getFrames: async count => { await ready; return { frames: clone(frames.slice(-(count ?? 30))) }; },
    getSkills: async () => ({ skills: clone(Array.from(skills.values())) }),
    async sendPrompt(prompt) {
      if (busy) throw clientError(409, "run_in_progress");
      if (!prompt.trim()) throw clientError(400, "empty_prompt");
      if (!connected) throw clientError(503, "device_offline");
      const id = begin(prompt), lower = prompt.toLowerCase();
      if (lower.includes("error")) {
        later(450, () => emit({ type: "agent_message", text: "I will send a test command and check the hardware response." }));
        later(1050, () => {
          const callId = call("shift_out", { data_hex: "0100026603FF04FF057E063C07180800" });
          later(80, () => result(callId, "shift_out", { success: false, error: "Hardware acknowledgement timed out." }, 80));
        });
        later(1750, () => finish("error", "The command was not confirmed.", "Hardware acknowledgement timed out. Try another prompt."));
      } else if (lower.includes("play") || lower.includes("animat")) {
        later(400, () => emit({ type: "agent_message", text: "I found the heartbeat skill. I will replay its two frames." }));
        let callId;
        later(950, () => { callId = call("reuse_skill", { skill_name: "heartbeat" }); });
        for (let i = 0; i < 40; i++) later(1300 + i * 80, () => newFrame(i % 2 === 0 ? HEART : SMALL));
        later(4520, () => result(callId, "reuse_skill", { success: true, frames_played: 40, decoded_state: map }, 3200));
        later(5000, () => finish("completed", "Heartbeat played. A little life in 64 pixels."));
      } else if (lower.includes("clear")) {
        later(400, () => emit({ type: "agent_message", text: "I will clear all 64 LEDs." }));
        later(950, () => shift(BLANK));
        later(1550, () => finish("completed", "Display cleared. A blank canvas again."));
      } else if (lower.includes("covered")) {
        later(400, () => emit({ type: "agent_message", text: "I will draw a heart, centred in the 8 by 8 grid." }));
        later(950, () => shift(HEART, "on", [[2, 1], [2, 2]]));
        later(1700, () => emit({ type: "agent_message", text: "decoded_state is the heart I intended, but the camera sees 2 LEDs differ. That is physical, so I will not redraw." }));
        later(2300, () => {
          const callId = call("save_skill", { name: "symbol_heart" });
          later(40, () => result(callId, "save_skill", { success: false, error: "save_refused: the camera contradicts the picture (physical_check mismatch: Camera sees 2 LEDs differ from the command (row 2 cols 1-2).)" }, 40));
        });
        later(2900, () => finish("completed", "A heart was sent, but the camera sees 2 LEDs differ (row 2 cols 1-2). Not saved: check that nothing covers the grid."));
      } else if (lower.includes("test mode") || lower.includes("unknown state") || lower.includes("display off")) {
        const display = lower.includes("test mode") ? "test" : lower.includes("display off") ? "shutdown" : "unknown";
        later(450, () => shift(display === "unknown" ? Array(8).fill("????????") : BLANK, display));
        later(1100, () => finish("completed", display === "test" ? "Test mode. All 64 LEDs are lit." : display === "shutdown" ? "Display off." : "State unknown. Waiting for confirmed data."));
      } else {
        const [rows, name] = lower.includes("letter") ? [LETTER, "letter_a"] : lower.includes("number") ? [SEVEN, "number_7"] : lower.includes("smil") ? [SMILE, "smiley"] : [HEART, "heart"];
        const wrong = [...rows], missing = rows[4].lastIndexOf("1");
        wrong[4] = rows[4].slice(0, missing) + "0" + rows[4].slice(missing + 1);
        later(450, () => emit({ type: "agent_message", text: `I will draw ${name.replaceAll("_", " ")}, centred in the 8 by 8 grid.` }));
        later(1050, () => shift(wrong));
        later(1750, () => emit({ type: "agent_message", text: "One pixel in row 5 is missing. I will correct that row and verify the map." }));
        later(2350, () => shift(rows));
        later(2950, () => {
          const callId = call("read_shift_state");
          later(45, () => result(callId, "read_shift_state", { success: true, decoded_state: map }, 45));
        });
        later(3450, () => {
          const callId = call("save_skill", { name, type: "action_sequence", steps: 3 });
          later(65, () => {
            const version = (skills.get(name)?.version ?? 0) + 1;
            skills.set(name, { name, type: "action_sequence", version, steps: 3 });
            result(callId, "save_skill", { success: true, name, version }, 65);
            emit({ type: "skill_saved", name, version });
          });
        });
        later(4050, () => finish("completed", `Drew ${name.replaceAll("_", " ")}. Saved as ${name}.`));
      }
      return { run_id: id };
    },
    async stop() { cancel(); if (busy) finish("stopped", "Stopped."); return { stopped: true }; },
    getVisionStatus: async () => ({ ...clone(vision), preview_active: true }),
    getVisionMetrics: async () => clone(metrics),
    previewStreamUrl: () => "./mock-preview.jpg",
    previewFrameUrl: () => "./mock-preview.jpg",
    async lockPreview(action) {
      if (action === "release" && !vision.calibrated) {
        clearTimeout(relockTimer); setMetrics(readout(2)); relockTimer = soon(1200, () => setMetrics(readout(3)));
      }
      return { ok: true };
    },
    async setLight(on) {
      if (busy) throw clientError(409, "run_in_progress");
      if (on) { lightRestore ??= clone(map); newFrame(ALL_ON); }
      else if (lightRestore) { newFrame(lightRestore.rows, lightRestore.display); lightRestore = null; }
      vision.light = Boolean(on);
      return { success: true, light: vision.light };
    },
    async getObservedState() {
      if (busy) throw clientError(409, "run_in_progress");
      const seen = observe(map.rows, map.display) ?? observation(map.rows, map.display);
      return { observed_state: seen.state, physical_check: seen.check };
    },
    calibrate: () => visionTask("calibrate", CALIBRATION_STEPS, () => {
      vision.calibrated = true; vision.calibration_created = Date.now() / 1000; vision.position_ok = null;
      return { success: true, reason: null, detail: {}, summary: CALIBRATION_SUMMARY };
    }),
    checkPosition: () => visionTask("check_position", POSITION_STEPS, () => {
      vision.position_ok = true;
      return { ok: true, max_corner_shift_px: 0.42, detail: { tolerance_px: 1.86, shifts_px: { tl: 0.31, tr: 0.42, bl: 0.18, br: 0.35 } } };
    }),
    setSpeed(value) { speed = Math.max(0.5, Math.min(2, Number(value))); },
    setConnected(value) { if (!value && busy) { cancel(); finish("stopped", "Stopped."); } connected = Boolean(value); status(); },
    onEvent(fn) { events.add(fn); return () => events.delete(fn); },
    onConnection(fn) {
      connections.add(fn);
      if (!started) {
        started = true;
        ready.then(() => {
          if (disposed) return;
          connections.forEach(callback => callback("open")); status();
          // The camera readout settles from MOVE CLOSER to READY, as when aiming the real camera.
          READOUTS.forEach((_, step) => soon(400 + step * 1100, () => { if (!vision.calibrated && !vision.operation) setMetrics(readout(step)); }));
          if (params.get("camera") === "demo") void cameraDemo();
          if (replayError) emit({ type: "run_finished", status: "error", error: replayError });
          if (replay?.length) {
            const first = Number(replay[0].ts);
            replay.forEach(raw => later(Math.max(0, (Number(raw.ts) - first) * 1000), () => emit(raw)));
          }
        });
      }
      return () => connections.delete(fn);
    },
    dispose() { disposed = true; cancel(); events.clear(); connections.clear(); }
  };

  // ?camera=demo: walk the setup without clicks (calibrate, check position, then a camera fault run).
  async function cameraDemo() {
    await pause(4800);
    await api.calibrate(); await pause(400);
    await api.checkPosition(); await pause(400);
    await api.sendPrompt("Simulate covered LEDs");
  }
  return api;
}
