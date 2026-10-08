import { CONFIG } from "./config.js";
import { createApiClient } from "./api.js";
import { createMockClient } from "./mock.js";
import { createStore } from "./store.js";
import { createGrid } from "./grid.js";
import { createTrace } from "./trace.js";
import { cameraPill, createCameraPanel } from "./camera.js";
import { icon, brandMark } from "./icons.js";

const SCENARIOS = {
  draw: { prompt: "Draw a heart", steps: ["Draw a centred heart", "Notice an imperfect row", "Correct it & save a skill"] },
  animation: { prompt: "Play the heartbeat animation", steps: ["Find the heartbeat skill", "Play 40 alternating frames", "Confirm the final state"] },
  clear: { prompt: "Clear the display", steps: ["Accept the prompt", "Clear all 64 LEDs", "Confirm a blank canvas"] },
  error: { prompt: "Simulate a tool error", steps: ["Send a test command", "Receive a failed response", "Report the error safely"] },
  test: { prompt: "Simulate test mode", steps: ["Accept a test command", "Receive test-mode state", "Show all 64 LEDs lit"] },
  unknown: { prompt: "Simulate unknown state", steps: ["Accept the test scenario", "Receive unknown LED data", "Display uncertainty, not guesses"] },
  shutdown: { prompt: "Simulate display off", steps: ["Accept the shutdown scenario", "Receive display-off state", "Keep the last map visible"] },
  camera: { prompt: "Simulate covered LEDs", steps: ["Draw a heart", "Camera sees 2 LEDs differ", "Save refused; report it"] }
};
const CAMERA_WARNING = "Camera not calibrated: results will not be camera-confirmed.";
const shortPrompts = new Map([
  ["Draw a heart", "Draw a heart"], ["Show the letter A", "Letter A"], ["Show the number 7", "Number 7"],
  ["Draw a smiley face", "Smiley face"], ["Play the heartbeat animation", "Heartbeat"], ["Clear the display", "Clear display"]
]);
const setText = (node, value) => { if (node.textContent !== String(value)) node.textContent = String(value); };
const sequence = value => Number.isInteger(value) ? String(value).padStart(4, "0") : "----";
function device(size, reference) {
  return `<div class="matrix-device matrix-device--${size}">
    <i class="screw screw--tl"></i><i class="screw screw--tr"></i><i class="screw screw--bl"></i><i class="screw screw--br"></i>
    <div class="device-top"><span>LED ARRAY</span><span>8 x 8</span></div>
    <div class="matrix-bay"><div data-ref="${reference}"></div></div>
    <div class="device-bottom"><span>LGA-01</span><span ${size === "large" ? 'data-ref="frame-label"' : ""}>RED / SINGLE COLOUR</span></div>
  </div>`;
}

export function createDashboard(root, options = {}) {
  const params = new URLSearchParams(location.search);
  const mock = typeof CONFIG.mock === "boolean" ? CONFIG.mock : params.has("mock") ? params.get("mock") === "1" : Boolean(options.preview);
  const client = mock ? createMockClient(CONFIG) : createApiClient(CONFIG);
  const store = createStore(client, CONFIG), controller = new AbortController();
  let activeView = "audience", lastMap, lastHistory = null, lastTrace = -1, disposed = false, interacted = false, demoTimer, copyTimer;
  const historyGrids = new Map(), flashTimers = new Map();
  root.className = "dashboard";
  root.innerHTML = `
    <header class="topbar">
      <a class="brand" href="." aria-label="LED grid agent home">${brandMark()}<div><h1>LED grid agent</h1><span class="brand-caption">SMALL SYSTEM. BIG IDEAS.</span></div></a>
      <nav class="view-tabs" role="tablist" aria-label="Dashboard views">
        <button class="view-tab is-active" data-view="audience" id="audience-tab" role="tab" aria-selected="true" aria-controls="audience-panel">${icon("grid")}Audience</button>
        <button class="view-tab" data-view="technical" id="technical-tab" role="tab" aria-selected="false" aria-controls="technical-panel" tabindex="-1">${icon("code")}Technical</button>
      </nav>
      <div class="connection-group"><span class="connection-status" data-ref="connection"><i class="status-dot"></i><span data-ref="connection-label">Reconnecting</span></span><span class="mock-badge" data-ref="mock-badge">Mock data</span><button class="theme-toggle" data-ref="theme-toggle" type="button" role="switch" aria-checked="false" aria-label="Toggle dark theme" title="Toggle theme"><i class="toggle-track"><i class="toggle-thumb"></i></i><span class="toggle-label" data-ref="toggle-label">Light</span><svg class="toggle-icon toggle-icon--sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2m9-10h-2M5 12H3m15.5-6.5-1.4 1.4M6.9 17.1l-1.4 1.4m13.2 0-1.4-1.4M6.9 6.9 5.5 5.5"/></svg><svg class="toggle-icon toggle-icon--moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/></svg></button></div>
    </header>
    <main class="workspace">
      <div class="content-pane">
        <section class="view audience-view" id="audience-panel" role="tabpanel" aria-labelledby="audience-tab">
          <div class="section-intro"><div class="section-kicker"><span>01 / LIVE CANVAS</span><span class="output-note">${icon("link")} OUTPUT ONLY</span></div><h2>Ideas, in 64 pixels.</h2><p>Say what you have in mind. The agent takes it from here.</p></div>
          <div class="canvas-stage">${device("large", "large-grid")}</div>
          <div class="audience-status" data-ref="audience-status"><i class="activity-dot"></i><span data-ref="status-line" role="status" aria-live="polite">Ready. Type a prompt or pick one below.</span></div>
          <div class="camera-pill-row"><span class="camera-pill" data-ref="camera-pill" hidden></span></div>
          <div class="prompt-area"><form class="prompt-bar" data-ref="prompt-form"><label class="sr-only" for="agent-prompt">Your prompt for the LED agent</label>${icon("prompt", "prompt-icon")}<input id="agent-prompt" data-ref="prompt" type="text" placeholder="What would you like to see?" autocomplete="off" maxlength="2000"><span class="input-hint">${icon("enter")}</span><button class="send-button" data-ref="send" type="submit">Send ${icon("arrow")}</button><button class="stop-button" data-ref="stop" type="button" hidden>${icon("stop")} Stop</button></form>
            <p class="prompt-error" data-ref="prompt-error" role="alert" hidden></p><p class="prompt-warning" data-ref="prompt-warning" hidden></p><div class="suggestions"><span class="suggestions-label">TRY</span><div class="prompt-chips" data-ref="chips"></div></div>
          </div>
        </section>
        <section class="view technical-view" id="technical-panel" role="tabpanel" aria-labelledby="technical-tab" hidden>
          <div class="section-intro"><div class="section-kicker"><span>01 / SYSTEM VIEW</span><span class="output-note">${icon("code")} THE REAL DATA</span></div><h2>A look under the hood.</h2><p>Every frame, every command, and the agent's side of the story.</p></div>
          <div class="camera-root" data-ref="camera-root" hidden></div>
          <div class="technical-layout">
            <section class="map-column"><div class="panel-heading"><h3>Live map</h3><span class="live-map-label" data-ref="map-live">LIVE</span></div><div class="map-data-layout"><div class="map-overview">${device("mini", "mini-grid")}<dl class="map-metadata"><div><dt>SEQUENCE</dt><dd data-ref="map-seq">0000</dd></div><div><dt>DISPLAY</dt><dd data-ref="map-display">on</dd></div><div><dt>INTENSITY</dt><dd><span data-ref="map-intensity">2</span><span class="value-unit"> / 15</span></dd></div></dl></div>
              <div class="json-panel"><div class="json-heading"><span>LED MAP / JSON</span><button data-ref="copy" class="copy-button" type="button" aria-label="Copy LED map JSON">${icon("copy")}<span>Copy</span></button></div><pre class="map-json" data-ref="json" tabindex="0" aria-label="Current authoritative LED map JSON"></pre><div class="json-footer"><i class="tiny-square"></i><span>READ-ONLY / BACKEND CONFIRMED</span></div></div></div>
              <div class="history-heading"><h3>Frame history</h3><span data-ref="history-count">0 / 30</span></div><div class="history-strip" data-ref="history" tabindex="0" role="region" aria-label="Most recent frames, newest first"></div><p class="history-caption">Newest first. Time between confirmed frames.</p>
            </section>
            <section class="trace-column"><div class="panel-heading"><h3>Agent trace</h3><span class="trace-count" data-ref="trace-count">0 entries</span></div><div class="trace-container"><div class="trace-scroll" data-ref="trace-scroll" tabindex="0" role="region" aria-label="Scrollable agent trace"><div class="trace-empty" data-ref="trace-empty">${icon("prompt")}<h4>Nothing to trace. Yet.</h4><p>Send a prompt or run a simulation.<br>Every step will show up here.</p></div><div class="trace-list" data-ref="trace-list"></div></div><button class="jump-latest" data-ref="jump" type="button" hidden>Jump to latest ${icon("down")}</button></div></section>
          </div>
        </section>
      </div>
      <aside class="simulation-panel" aria-labelledby="simulation-title">
        <div class="simulation-kicker"><span>${icon("flask")} SIMULATION LAB</span><span>02</span></div><h2 id="simulation-title">A little test drive.</h2><p class="simulation-description">No hardware needed. Try a scenario<br class="desktop-break"> and watch the agent work.</p>
        <div data-ref="simulation-controls">
          <label class="control-label" for="scenario">SCENARIO</label><div class="select-wrap"><select id="scenario" data-ref="scenario"><option value="draw">Draw + correct</option><option value="animation">Heartbeat animation</option><option value="clear">Clear the display</option><option value="error">Tool failure</option><option value="test">Display test mode</option><option value="unknown">Unknown state</option><option value="shutdown">Display off</option><option value="camera">Camera fault</option></select>${icon("down")}</div>
          <div class="speed-heading"><label for="simulation-speed">Playback speed</label><output data-ref="speed-output" for="simulation-speed">1x</output></div><input class="speed-slider" id="simulation-speed" data-ref="speed" type="range" min="0.5" max="2" value="1" step="0.5"><div class="range-labels"><span>0.5x / SLOW</span><span>2x / FAST</span></div>
          <div class="simulation-actions"><button class="simulation-button" data-ref="run-simulation" type="button">${icon("play")}<span data-ref="simulation-button-label">Run simulation</span>${icon("arrow", "simulation-arrow")}</button><button class="simulation-stop" data-ref="simulation-stop" type="button" aria-label="Stop the simulation" hidden>${icon("stop")}</button></div>
          <p class="simulation-error" data-ref="simulation-error" role="alert" hidden></p>
          <div class="simulation-script"><span class="control-label">THE SEQUENCE</span><ol data-ref="scenario-steps"></ol></div>
          <div class="device-connection"><div class="control-label">DEVICE CONNECTION</div><div class="connection-control"><span>Device online</span><button class="connection-toggle" data-ref="device-toggle" type="button" role="switch" aria-checked="true" aria-label="Simulate device connection"><span></span></button></div><p>Disconnect to test the offline state.</p></div>
        </div>
        <div class="real-mode-note" data-ref="real-mode" hidden><p>Connected to the real backend. Simulation controls are available in mock mode.</p><a class="simulation-button" href="?mock=1">Open mock mode ${icon("arrow")}</a></div>
        <div class="simulation-footer"><span class="simulation-state"><i data-ref="simulation-state-dot"></i><span data-ref="simulation-state">READY TO EXPLORE</span></span><p>Simulated events.<br>Authoritative state.</p></div>
      </aside>
    </main>
    <footer class="app-footer"><span><i class="tiny-square"></i> THE BACKEND IS THE SOURCE OF TRUTH</span><span>8 x 8 <i class="footer-slash">/</i> ONE COLOUR. ENDLESS POSSIBILITY.</span></footer>`;
  const $ = name => root.querySelector(`[data-ref="${name}"]`);
  const listen = (node, event, fn) => node.addEventListener(event, fn, { signal: controller.signal });
  const themeToggle = $("theme-toggle");
  const storedTheme = location.search.includes("dark=1") ? "dark" : location.search.includes("dark=0") ? "light" : null;
  const initialTheme = storedTheme === "dark" || (!storedTheme && localStorage.getItem("led-grid-agent-theme") === "dark") ? "dark" : "light";
  const applyTheme = theme => {
    document.documentElement.dataset.theme = theme;
    root.classList.toggle("is-dark", theme === "dark");
    themeToggle.setAttribute("aria-checked", String(theme === "dark"));
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#101013" : "#f6f5f0");
    setText($("toggle-label"), theme === "dark" ? "Dark" : "Light");
  };
  applyTheme(initialTheme);
  listen(themeToggle, "click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("led-grid-agent-theme", next); } catch { /* Ignore unavailable storage. */ }
  });
  root.querySelector(".brand").href = location.pathname + location.search;
  $("mock-badge").hidden = !mock;
  $("simulation-controls").hidden = !mock; $("real-mode").hidden = mock;
  root.querySelector(".simulation-panel").hidden = !mock; root.classList.toggle("is-live", !mock);
  if (!mock) {
    setText(root.querySelector(".simulation-description"), "Listening to your backend. Open mock mode to explore the simulation lab.");
    setText(root.querySelector(".real-mode-note p"), "Simulation controls are disabled in live mode. The dashboard forwards prompts and displays confirmed state only.");
    setText(root.querySelector(".simulation-footer p"), "Live events. Authoritative state.");
  }
  const largeGrid = createGrid($("large-grid"), { size: "large" });
  const miniGrid = createGrid($("mini-grid"), { size: "mini" });
  const trace = createTrace($("trace-scroll"), $("trace-list"), $("trace-empty"), $("jump"), $("trace-count"));
  const camera = createCameraPanel($("camera-root"), { client, store, listen });

  function switchView(view) {
    activeView = view;
    root.querySelectorAll(".view-tab").forEach(tab => {
      const selected = tab.dataset.view === view;
      tab.classList.toggle("is-active", selected); tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    root.querySelector(".audience-view").hidden = view !== "audience";
    root.querySelector(".technical-view").hidden = view !== "technical";
    $("simulation-error").hidden = !store.state.error || activeView !== "technical";
    camera.setVisible(view === "technical");
    if (view === "technical") trace.onVisible();
  }
  root.querySelectorAll(".view-tab").forEach(tab => {
    listen(tab, "click", () => switchView(tab.dataset.view));
    listen(tab, "keydown", event => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault(); const view = event.key === "Home" ? "audience" : event.key === "End" ? "technical" : activeView === "audience" ? "technical" : "audience";
        switchView(view); root.querySelector(`[data-view="${view}"]`).focus();
      }
    });
  });
  function cancelDemo() { interacted = true; clearTimeout(demoTimer); }
  const cameraConfirmed = state => !state.vision || (state.vision.calibrated && state.vision.position_ok === true);
  async function submit(prompt, automatic = false) {
    if (!automatic) cancelDemo();
    // Non-blocking: the run still starts.
    $("prompt-warning").hidden = cameraConfirmed(store.state); setText($("prompt-warning"), CAMERA_WARNING);
    await store.submit(prompt);
    if (!disposed && !store.state.error) $("prompt").value = "";
  }
  CONFIG.suggestedPrompts.forEach(prompt => {
    const chip = document.createElement("button"); chip.type = "button"; chip.className = "prompt-chip";
    chip.textContent = shortPrompts.get(prompt) ?? prompt; chip.title = prompt; chip.setAttribute("aria-label", prompt);
    listen(chip, "click", () => { $("prompt").value = prompt; void submit(prompt); }); $("chips").append(chip);
  });
  listen($("prompt"), "focus", cancelDemo);
  listen($("prompt-form"), "submit", event => { event.preventDefault(); void submit($("prompt").value); });
  listen($("stop"), "click", () => void store.stop());
  listen($("simulation-stop"), "click", () => void store.stop());
  function updateScenario() {
    const steps = SCENARIOS[$("scenario").value].steps;
    $("scenario-steps").replaceChildren(...steps.map((step, index) => {
      const item = document.createElement("li"), number = document.createElement("span"), text = document.createElement("span");
      number.className = "step-number"; number.textContent = String(index + 1).padStart(2, "0"); text.textContent = step; item.append(number, text); return item;
    }));
  }
  updateScenario(); listen($("scenario"), "change", () => { cancelDemo(); updateScenario(); });
  listen($("speed"), "input", () => {
    cancelDemo(); client.setSpeed?.($("speed").value); setText($("speed-output"), `${$("speed").value}x`);
    $("speed").setAttribute("aria-valuetext", `${$("speed").value} times normal speed`);
  });
  listen($("run-simulation"), "click", () => void submit(SCENARIOS[$("scenario").value].prompt));
  listen($("device-toggle"), "click", () => { cancelDemo(); client.setConnected?.(!store.state.status.connected); });

  function renderJSON(map) {
    const container = $("json"), lines = JSON.stringify(map ?? {}, null, 2).split("\n");
    const previous = Array.from(container.children);
    lines.forEach((line, index) => {
      let node = previous[index];
      if (!node) { node = document.createElement("span"); node.className = "json-line"; container.append(node); }
      const changed = node.textContent !== line;
      node.textContent = line;
      if (changed && previous.length) {
        node.classList.add("is-changed"); clearTimeout(flashTimers.get(node));
        flashTimers.set(node, setTimeout(() => { node.classList.remove("is-changed"); flashTimers.delete(node); }, 400));
      }
    });
    previous.slice(lines.length).forEach(node => { clearTimeout(flashTimers.get(node)); flashTimers.delete(node); node.remove(); });
  }
  function renderHistory(history) {
    const valid = new Set(history.map(map => map.seq));
    historyGrids.forEach((record, seq) => { if (!valid.has(seq)) { record.grid.destroy(); record.node.remove(); historyGrids.delete(seq); } });
    history.forEach((map, index) => {
      let record = historyGrids.get(map.seq);
      if (!record) {
        const node = document.createElement("div"); node.className = "history-item";
        node.innerHTML = '<div class="history-grid"></div><span class="history-seq"></span><span class="history-gap"></span>';
        const grid = createGrid(node.querySelector(".history-grid"), { size: "thumb" }); grid.render(map);
        record = { node, grid, map }; historyGrids.set(map.seq, record);
      }
      if (record.map !== map) { record.grid.render(map); record.map = map; }
      setText(record.node.querySelector(".history-seq"), `#${String(map.seq).padStart(3, "0")}`);
      const gap = history[index + 1] ? `${Math.max(0, Math.round((map.timestamp - history[index + 1].timestamp) * 1000))} ms` : map.seq === 0 ? "first" : "n/a";
      setText(record.node.querySelector(".history-gap"), gap);
      const current = $("history").children[index]; if (current !== record.node) $("history").insertBefore(record.node, current ?? null);
    });
    setText($("history-count"), `${history.length} / ${CONFIG.maxHistoryFrames}`);
  }
  const unsubscribe = store.subscribe(state => {
    if (disposed) return;
    const offline = !state.status.connected, busy = state.status.busy;
    const blocked = busy || state.sending || offline || !state.socketOpen;
    setText($("connection-label"), !state.socketOpen ? "Reconnecting" : offline ? "Device offline" : "Live");
    $("connection").className = `connection-status ${!state.socketOpen ? "is-reconnecting" : offline ? "is-offline" : "is-live"}`;
    setText($("status-line"), offline && !state.loading ? "Device offline" : state.line);
    $("audience-status").classList.toggle("is-danger", state.danger && !offline);
    $("audience-status").classList.toggle("is-busy", busy);
    $("audience-status").classList.toggle("is-offline", offline);
    $("prompt").disabled = blocked; $("send").disabled = blocked; $("stop").hidden = !busy;
    $("prompt").placeholder = offline && !state.loading ? "Waiting for the device..." : busy ? "The agent is working..." : "What would you like to see?";
    root.querySelectorAll(".prompt-chip").forEach(chip => { chip.disabled = blocked; });
    $("prompt-error").hidden = !state.error; setText($("prompt-error"), state.error);
    $("simulation-error").hidden = !state.error || activeView !== "technical"; setText($("simulation-error"), state.error);
    $("scenario").disabled = busy; $("speed").disabled = busy;
    $("run-simulation").disabled = blocked; $("simulation-stop").hidden = !busy;
    setText($("simulation-button-label"), busy ? "Running..." : "Run simulation");
    $("device-toggle").setAttribute("aria-checked", String(!offline));
    setText($("simulation-state"), !state.socketOpen ? "WAITING FOR CONNECTION" : offline ? "DEVICE IS OFFLINE" : busy ? mock ? "SIMULATION IN PROGRESS" : "RUN IN PROGRESS" : mock ? "READY TO EXPLORE" : "LISTENING TO BACKEND");
    $("simulation-state-dot").className = offline || !state.socketOpen ? "is-offline" : "";
    setText($("map-live"), state.socketOpen && !offline ? "LIVE" : "WAITING");
    if (lastMap !== state.map) {
      largeGrid.render(state.map); miniGrid.render(state.map); renderJSON(state.map); lastMap = state.map;
      setText($("frame-label"), `FRAME ${sequence(state.map?.seq)}`);
      setText($("map-seq"), sequence(state.map?.seq));
      setText($("map-display"), state.map?.display ?? "unknown"); setText($("map-intensity"), state.map?.intensity ?? "-");
    }
    if (lastHistory !== state.history) { renderHistory(state.history); lastHistory = state.history; }
    if (lastTrace !== state.traceVersion) { trace.render(state.trace); lastTrace = state.traceVersion; }
    camera.render(state);
    const pill = cameraPill(state);
    $("camera-pill").hidden = !pill;
    if (pill) { setText($("camera-pill"), pill.text); $("camera-pill").className = `camera-pill is-${pill.tone}`; }
    if (cameraConfirmed(state)) $("prompt-warning").hidden = true;
  });
  listen($("copy"), "click", async () => {
    const value = JSON.stringify(store.state.map ?? {}, null, 2);
    let copied = false;
    try { await navigator.clipboard.writeText(value); copied = true; }
    catch {
      if (disposed) return;
      const area = document.createElement("textarea"); area.value = value; area.className = "clipboard-fallback"; root.append(area); area.select();
      try { copied = document.execCommand("copy"); } catch { /* Clipboard access can be restricted. */ }
      area.remove();
      $("copy").focus();
    }
    if (disposed) return;
    setText($("copy").querySelector("span"), copied ? "Copied" : "Unavailable");
    clearTimeout(copyTimer); copyTimer = setTimeout(() => setText($("copy").querySelector("span"), "Copy"), 1600);
  });
  if (params.get("view") === "technical") switchView("technical");
  void store.init().then(() => {
    // The hosted preview demonstrates a real mock run; explicit ?mock=1 starts blank.
    if (!disposed && options.preview && mock && !params.has("mock") && !params.has("replay") && !params.has("offline")) {
      demoTimer = setTimeout(() => { if (!disposed && !interacted && !store.state.status.busy && store.state.status.connected) void submit("Draw a heart", true); }, 500);
    }
  });
  return () => {
    disposed = true; clearTimeout(demoTimer); clearTimeout(copyTimer); controller.abort(); unsubscribe();
    flashTimers.forEach(clearTimeout); historyGrids.forEach(record => record.grid.destroy());
    largeGrid.destroy(); miniGrid.destroy(); trace.destroy(); camera.destroy(); store.dispose(); root.replaceChildren();
  };
}

const staticRoot = document.getElementById("led-dashboard");
if (staticRoot) createDashboard(staticRoot);