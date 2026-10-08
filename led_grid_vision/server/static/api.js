export const EVENTS = Object.freeze({
  STATUS: "connection.status", MAP: "map.updated", START: "run.begin",
  MESSAGE: "agent.text", CALL: "tool.begin", RESULT: "tool.end",
  SAVED: "skill.stored", FINISH: "run.end",
  OBSERVED: "camera.observed", METRICS: "camera.metrics", VISION: "camera.status"
});

export function normalizeMap(raw) {
  return raw && typeof raw === "object" ? { ...raw } : null;
}

export function normalizeStatus(raw = {}) {
  raw ??= {};
  return { connected: Boolean(raw.connected), busy: Boolean(raw.busy), run_id: raw.run_id ?? null, runId: raw.run_id ?? null, vision: raw.vision ?? null };
}

const normalizeReply = raw => ({ ...raw });

function summarizeTool(tool, args = {}) {
  switch (tool) {
    case "shift_out": return String(args.data_hex ?? "").slice(0, 24);
    case "wait": return `${args.duration_ms ?? "?"} ms`;
    case "read_recent_frames": return `${args.count ?? "?"} frames`;
    case "get_skill": case "save_skill": return String(args.name ?? "");
    case "reuse_skill": return String(args.skill_name ?? "");
    case "read_shift_state": case "list_skills": case "read_observed_state": return "";
    default: return Object.keys(args).slice(0, 2).map(key => `${key}: ${String(args[key])}`).join(", ").slice(0, 60);
  }
}

export function normalizeEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const at = raw.ts ?? Date.now() / 1000;
  const base = { ...raw, ts: at, at, runId: raw.run_id ?? null };
  const args = raw.args ?? {};
  switch (raw.type) {
    case "status": return { ...base, kind: EVENTS.STATUS, status: normalizeStatus(raw) };
    case "shift_state": return { ...base, kind: EVENTS.MAP, map: normalizeMap(raw.state) };
    case "run_started": return { ...base, kind: EVENTS.START, prompt: raw.prompt ?? "" };
    case "agent_message": return { ...base, kind: EVENTS.MESSAGE, text: raw.text ?? "" };
    case "tool_call": return {
      ...base, kind: EVENTS.CALL, callId: raw.call_id, tool: raw.tool ?? "unknown",
      args, summary: summarizeTool(raw.tool, args), preview: null,
      activity: raw.tool === "shift_out" ? "Drawing..." :
        ["read_shift_state", "read_recent_frames"].includes(raw.tool) ? "Checking the grid..." :
        raw.tool === "read_observed_state" ? "Looking through the camera..." :
        raw.tool === "reuse_skill" ? `Playing ${raw.args?.skill_name ?? "a skill"}...` :
        ["get_skill", "list_skills"].includes(raw.tool) ? "Looking through saved skills..." : null
    };
    case "tool_result": return {
      ...base, kind: EVENTS.RESULT, callId: raw.call_id, tool: raw.tool ?? "unknown",
      result: raw.result ?? {}, duration: raw.duration_ms ?? null,
      success: raw.result?.success !== false, preview: normalizeMap(raw.result?.decoded_state ?? raw.result?.final_state ?? raw.result?.state)
    };
    case "skill_saved": return { ...base, kind: EVENTS.SAVED, name: raw.name ?? "unnamed", version: raw.version ?? 1 };
    case "observed_state": return { ...base, kind: EVENTS.OBSERVED, observed: normalizeMap(raw.state), check: raw.physical_check ?? null };
    case "vision_metrics": return { ...base, kind: EVENTS.METRICS, metrics: raw.metrics ?? null };
    case "vision_status": return {
      ...base, kind: EVENTS.VISION, operation: raw.operation ?? null, phase: raw.phase ?? null,
      vision: raw.status ?? null, result: raw.result ?? null
    };
    case "run_finished": return {
      ...base, kind: EVENTS.FINISH, outcome: raw.status ?? "completed",
      summary: raw.summary ?? "", error: raw.error ?? null
    };
    default: return null;
  }
}

export function clientError(status, error) {
  return { status, error, code: status === 409 ? "busy" : status === 400 ? "empty" : status === 503 ? "offline" : status === 404 ? "disabled" : "network" };
}

export function createApiClient(config) {
  const events = new Set(), connections = new Set(), abort = new AbortController();
  let socket, retryTimer, attempt = 0, disposed = false, started = false;
  const base = config.apiBase.replace(/\/$/, "");
  async function request(path, options = {}) {
    try {
      const response = await fetch(base + path, {
        ...options, signal: abort.signal,
        headers: { "Content-Type": "application/json", ...options.headers }
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw clientError(response.status, data?.error);
      if (data === null) throw clientError(0, "invalid_json");
      return data;
    } catch (error) {
      if (error?.code) throw error;
      throw clientError(0, "network_error");
    }
  }
  function connect() {
    if (disposed) return;
    try {
      const url = new URL(config.wsPath, config.apiBase || location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => { attempt = 0; connections.forEach(fn => fn("open")); };
      ws.onmessage = message => {
        try { const event = normalizeEvent(JSON.parse(message.data)); if (event) events.forEach(fn => fn(event)); }
        catch { /* Ignore a malformed message without interrupting the stream. */ }
      };
      ws.onerror = () => ws.close();
      ws.onclose = reconnect;
    } catch { reconnect(); }
  }
  function reconnect() {
    if (disposed) return;
    connections.forEach(fn => fn("closed"));
    clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 10000));
  }
  return {
    getStatus: async () => normalizeStatus(await request("/api/status")),
    getShiftState: async () => normalizeMap(await request("/api/shift_state")),
    getFrames: async count => {
      const raw = await request(`/api/frames?count=${Math.max(1, Math.min(30, count ?? 30))}`);
      return { frames: (raw.frames ?? []).map(normalizeMap).filter(Boolean) };
    },
    getSkills: async () => normalizeReply(await request("/api/skills")),
    sendPrompt: async prompt => normalizeReply(await request("/api/prompt", { method: "POST", body: JSON.stringify({ prompt }) })),
    stop: async () => normalizeReply(await request("/api/stop", { method: "POST" })),
    // Camera (stage 5). A 404 means the server runs without vision.
    getVisionStatus: async () => normalizeReply(await request("/api/vision/status")),
    getVisionMetrics: async () => normalizeReply(await request("/api/vision/metrics")),
    previewStreamUrl: () => `${base}/api/vision/preview.mjpg`,
    previewFrameUrl: () => `${base}/api/vision/preview.jpg?t=${Date.now()}`,
    lockPreview: async (action, x, y) => normalizeReply(await request("/api/vision/preview/lock", { method: "POST", body: JSON.stringify({ action, x, y }) })),
    setLight: async on => normalizeReply(await request("/api/vision/light", { method: "POST", body: JSON.stringify({ on: Boolean(on) }) })),
    getObservedState: async () => normalizeReply(await request("/api/observed_state")),
    calibrate: async () => normalizeReply(await request("/api/vision/calibrate", { method: "POST" })),
    checkPosition: async () => normalizeReply(await request("/api/vision/check_position", { method: "POST" })),
    onEvent(fn) { events.add(fn); return () => events.delete(fn); },
    onConnection(fn) {
      connections.add(fn);
      if (!started) { started = true; queueMicrotask(connect); }
      return () => connections.delete(fn);
    },
    dispose() { disposed = true; clearTimeout(retryTimer); abort.abort(); socket?.close(); events.clear(); connections.clear(); }
  };
}