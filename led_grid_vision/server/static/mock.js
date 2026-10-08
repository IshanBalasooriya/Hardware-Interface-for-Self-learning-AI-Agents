import { normalizeEvent, normalizeStatus, clientError, EVENTS } from "./api.js";

const BLANK = Array(8).fill("00000000");
const HEART = ["00000000", "01100110", "11111111", "11111111", "01111110", "00111100", "00011000", "00000000"];
const SMALL = ["00000000", "00000000", "00100100", "01111110", "00111100", "00011000", "00000000", "00000000"];
const LETTER = ["00011000", "00100100", "01000010", "01000010", "01111110", "01000010", "01000010", "00000000"];
const SEVEN = ["00000000", "01111110", "00000100", "00001000", "00010000", "00100000", "00100000", "00000000"];
const SMILE = ["00000000", "01100110", "01100110", "00000000", "01000010", "00111100", "00000000", "00000000"];

export function createMockClient(config) {
  const params = new URLSearchParams(location.search);
  const events = new Set(), connections = new Set(), timers = new Set(), skills = new Map();
  let connected = params.get("offline") !== "1", busy = false, runId = null, runCount = 0;
  let speed = 1, started = false, disposed = false, callCount = 0, replay = null, replayError = null;
  let map = { seq: 0, timestamp: Date.now() / 1000, display: "on", intensity: 2, rows: [...BLANK], bytes: "01000200030004000500060007000800" };
  let frames = [map];
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
  function shift(rows, display = "on") {
    const id = call("shift_out", { data_pin: 25, clock_pin: 26, latch_pin: 27, group_size: 2, data_hex: encode(rows) });
    later(70, () => {
      const state = newFrame(rows, display);
      result(id, "shift_out", { success: true, decoded_state: state });
    });
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

  return {
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
}