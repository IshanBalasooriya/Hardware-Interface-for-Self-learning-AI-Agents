import { EVENTS } from "./api.js";

export function createStore(client, config) {
  const listeners = new Set(), calls = new Map();
  let disposed = false, socketOpened = false, refreshing = false, buffered = [], renderQueued = false, traceId = 0;
  const state = {
    status: { connected: false, busy: false, runId: null }, map: null,
    history: [], trace: [], traceVersion: 0, line: "Ready. Type a prompt or pick one below.",
    danger: false, socketOpen: false, error: "", sending: false, loading: true
  };
  function notify() {
    // The grid owns requestAnimationFrame; batch state first so JSON and LEDs paint together.
    if (!renderQueued && !disposed) {
      renderQueued = true;
      queueMicrotask(() => {
        renderQueued = false; if (!disposed) listeners.forEach(fn => fn(state));
      });
    }
  }
  function addTrace(entry) {
    state.trace.push({ ...entry, id: ++traceId });
    if (state.trace.length > config.maxTraceEntries) {
      const removed = state.trace.splice(0, state.trace.length - config.maxTraceEntries);
      removed.forEach(item => { if (item.callId) calls.delete(`${item.runId}:${item.callId}`); });
    }
    state.traceVersion++;
    return state.trace[state.trace.length - 1];
  }
  function apply(event) {
    if (!event || disposed) return;
    if (refreshing) { buffered.push(event); return; }
    switch (event.kind) {
      case EVENTS.STATUS: state.status = event.status; break;
      case EVENTS.MAP:
        if (!event.map || (state.map && event.map.seq <= state.map.seq)) return;
        state.map = event.map;
        state.history = [event.map, ...state.history].slice(0, config.maxHistoryFrames);
        break;
      case EVENTS.START:
        state.line = "Thinking..."; state.danger = false; state.error = "";
        state.status = { ...state.status, busy: true, runId: event.runId };
        addTrace({ ...event, view: "start" }); break;
      case EVENTS.MESSAGE: addTrace({ ...event, view: "message" }); break;
      case EVENTS.CALL: {
        if (event.activity) state.line = event.activity;
        const entry = addTrace({ ...event, view: "tool", pending: true, revision: 0 });
        calls.set(`${event.runId}:${event.callId}`, entry); break;
      }
      case EVENTS.RESULT: {
        const entry = calls.get(`${event.runId}:${event.callId}`);
        if (entry) {
          Object.assign(entry, { result: event.result, duration: event.duration, success: event.success, preview: event.preview, pending: false, revision: (entry.revision ?? 0) + 1 });
          state.traceVersion++;
        }
        else addTrace({ ...event, view: "tool", args: {}, summary: "", pending: false, revision: 1 });
        break;
      }
      case EVENTS.SAVED:
        state.line = `Saved as ${event.name}`; addTrace({ ...event, view: "saved" }); break;
      case EVENTS.FINISH:
        state.status = { ...state.status, busy: false, runId: null };
        state.error = "";
        state.danger = event.outcome === "error";
        state.line = event.outcome === "stopped" ? "Stopped." : event.outcome === "max_turns" ? "The agent ran out of steps." :
          event.outcome === "error" ? `Something went wrong: ${event.error || event.summary || "Unknown error"}` : event.summary || "Done.";
        addTrace({ ...event, view: "finish" }); break;
    }
    notify();
  }
  async function refresh() {
    refreshing = true;
    const results = await Promise.allSettled([client.getStatus(), client.getShiftState(), client.getFrames(config.maxHistoryFrames)]);
    if (disposed) return;
    if (results[0].status === "fulfilled") state.status = results[0].value;
    if (results[1].status === "fulfilled") state.map = results[1].value;
    if (results[2].status === "fulfilled") {
      state.history = results[2].value.frames.slice(-config.maxHistoryFrames).reverse();
    }
    state.loading = false; refreshing = false;
    const pending = buffered; buffered = []; pending.forEach(apply); notify();
  }
  const unsubscribers = [];
  async function init() {
    await refresh();
    if (disposed) return;
    unsubscribers.push(client.onEvent(apply));
    unsubscribers.push(client.onConnection(connection => {
      state.socketOpen = connection === "open";
      if (state.socketOpen && (socketOpened || !state.map)) void refresh();
      if (state.socketOpen) socketOpened = true;
      notify();
    }));
  }
  return {
    state, init,
    subscribe(fn) { listeners.add(fn); fn(state); return () => listeners.delete(fn); },
    async submit(prompt) {
      state.error = "";
      if (!prompt.trim()) { state.error = "Type a prompt first"; notify(); return; }
      state.sending = true; notify();
      try { await client.sendPrompt(prompt.trim()); }
      catch (error) {
        state.error = error.code === "busy" ? "The agent is busy. Wait for it to finish or press Stop." :
          error.code === "offline" ? "Device offline" : error.code === "empty" ? "Type a prompt first" : "Could not reach the agent.";
      } finally { state.sending = false; notify(); }
    },
    async stop() {
      try { await client.stop(); } catch { state.error = "Could not reach the agent."; notify(); }
    },
    dispose() { disposed = true; unsubscribers.forEach(fn => fn()); listeners.clear(); client.dispose(); }
  };
}