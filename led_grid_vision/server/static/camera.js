import { createGrid } from "./grid.js";

// Camera section of the Technical tab (stage 5). Visibility only: every number and colour in the
// readout comes from the server's viewfinder code (/api/vision/metrics); nothing is measured here.
const STEPS = [
  "Open the Technical tab", "Watch the live camera preview", "Adjust the camera or grid",
  "Readout all green: READY", "Press Calibrate", "Calibration locks (CALIBRATED)",
  "Press Check position", "Start the agent"
];
const CHECK_LABELS = {
  match: "CONFIRMED", mismatch: "DIFFERS", not_visible: "NOT VISIBLE",
  consistent_dark: "DARK, AS COMMANDED", uncertain: "UNCERTAIN", unavailable: "UNAVAILABLE"
};
const OP_LABELS = { run: "AGENT RUN", calibrate: "CALIBRATING", check_position: "CHECKING POSITION", light: "LIGHT" };
const POLL_MS = 500;

const setText = (node, value) => { if (node.textContent !== String(value)) node.textContent = String(value); };

export function allGreen(metrics) {
  const c = metrics?.colours;
  return Boolean(c) && metrics.ready === "READY" && ["ready", "px_per_led", "in_frame", "lock"].every(k => c[k] === "green");
}

export function readoutItems(m) {
  if (!m) return [];
  return [
    [m.ready, m.colours.ready],
    [m.px_per_led == null ? "px/LED -" : `px/LED ${m.px_per_led.toFixed(1)}`, m.colours.px_per_led],
    [`in frame: ${m.in_frame ? "yes" : "no"}`, m.colours.in_frame],
    [`sharp ${m.sharpness == null ? "-" : Math.round(m.sharpness)}`, m.colours.sharpness],
    [m.lock, m.colours.lock]
  ];
}

export function verificationLine(summary) {
  return String(summary ?? "").split("\n").find(line => line.trim().startsWith("verification"))?.trim() ?? "";
}

export function createCameraPanel(container, { client, store, listen }) {
  container.innerHTML = `
    <section class="camera-section" aria-labelledby="camera-title">
      <div class="panel-heading"><h3 id="camera-title">Camera</h3><span class="camera-op" data-cam="op">IDLE</span></div>
      <div class="camera-layout">
        <div class="camera-main">
          <div class="camera-preview">
            <img data-cam="img" alt="Live camera preview with the grid overlay. Click to centre the zoom.">
            <span class="camera-preview-empty" data-cam="empty">Waiting for the camera...</span>
            <button class="camera-relock" data-cam="relock" type="button" title="Drop the lock and search again (viewfinder key a)">Re-lock</button>
          </div>
          <div class="camera-readout" role="status" aria-live="off"><div class="readout-main" data-cam="readout"></div><div class="readout-sub" data-cam="readout-sub">-</div></div>
          <div class="camera-buttons">
            <button class="camera-button" data-cam="light" type="button" aria-pressed="false">Light grid</button>
            <button class="camera-button camera-button--primary" data-cam="calibrate" type="button" title="Shift+click to calibrate even when the readout is not READY">Calibrate</button>
            <button class="camera-button" data-cam="position" type="button">Check position</button>
          </div>
          <p class="camera-message" data-cam="message" role="alert" hidden></p>
        </div>
        <aside class="camera-checklist"><span class="control-label">SETUP</span><ol data-cam="steps"></ol><p class="camera-verification" data-cam="verification" hidden></p></aside>
      </div>
      <div class="camera-compare">
        <figure><figcaption>COMMANDED</figcaption><div class="compare-bay"><div data-cam="commanded"></div></div></figure>
        <figure><figcaption>OBSERVED</figcaption><div class="compare-bay compare-bay--observed"><div data-cam="observed"></div></div></figure>
        <div class="camera-check"><span class="check-badge" data-cam="badge">NO LOOK YET</span><p data-cam="summary">The camera result appears after the next drawing command.</p></div>
      </div>
    </section>`;
  const $ = name => container.querySelector(`[data-cam="${name}"]`);
  const img = $("img"), commanded = createGrid($("commanded"), { size: "mini" }), observed = createGrid($("observed"), { size: "mini" });
  $("observed").classList.add("led-grid--observed");
  const steps = STEPS.map((text, index) => {
    const item = document.createElement("li"), mark = document.createElement("span"), label = document.createElement("span");
    mark.className = "step-mark"; mark.textContent = String(index + 1); label.textContent = text;
    item.append(mark, label); $("steps").append(item); return item;
  });
  let visible = false, streaming = false, fallbackTimer = 0, lastState = null;

  function startPreview() {
    if (!visible || img.dataset.mode) return;
    img.dataset.mode = "stream"; img.src = client.previewStreamUrl();
  }
  function stopPreview() {
    clearInterval(fallbackTimer); fallbackTimer = 0; delete img.dataset.mode;
    img.removeAttribute("src"); streaming = false;
  }
  listen(img, "load", () => { streaming = true; if (lastState) render(lastState); });
  listen(img, "error", () => {
    // MJPEG unavailable: poll single frames instead.
    if (!visible || img.dataset.mode === "frames") return;
    img.dataset.mode = "frames";
    fallbackTimer = setInterval(() => { if (visible) img.src = client.previewFrameUrl(); }, POLL_MS);
  });
  listen(img, "click", event => {
    const rect = img.getBoundingClientRect();
    if (!img.naturalWidth || !rect.width) return;
    const x = (event.clientX - rect.left) * img.naturalWidth / rect.width, y = (event.clientY - rect.top) * img.naturalHeight / rect.height;
    void store.lockPreview("point", Math.round(x), Math.round(y));
  });
  listen($("relock"), "click", () => void store.lockPreview("release"));
  listen($("light"), "click", () => void store.setLight(!store.state.vision?.light));
  listen($("calibrate"), "click", event => {
    if (!allGreen(store.state.metrics) && !event.shiftKey) {
      store.state.visionMessage = "The readout is not all green READY yet. Shift+click to calibrate anyway.";
      store.state.visionVersion++; render(store.state); return;
    }
    void store.calibrate();
  });
  listen($("position"), "click", () => void store.checkPosition());

  function render(state) {
    lastState = state;
    const vision = state.vision;
    container.hidden = !vision;
    if (!vision) { stopPreview(); return; }
    if (visible) startPreview();
    const op = state.visionOp ?? vision.operation ?? (state.status.busy ? "run" : null);
    const busy = Boolean(op) || state.status.busy || !state.status.connected;
    setText($("op"), op ? OP_LABELS[op] ?? op.toUpperCase() : vision.camera === "ok" ? "IDLE" : `CAMERA ${String(vision.camera).toUpperCase()}`);
    $("op").className = `camera-op${op ? " is-busy" : vision.camera === "ok" ? "" : " is-bad"}`;

    const m = state.metrics;
    $("readout").replaceChildren(...readoutItems(m).map(([text, colour]) => {
      const span = document.createElement("span"); span.className = `readout-item is-${colour}`; span.textContent = text; return span;
    }));
    // LOCKED holds the box (as the OpenCV viewfinder): after moving the grid, Re-lock with all LEDs lit.
    const hint = m?.lock === "LOCKED" ? "held: press Re-lock after moving the grid" : null;
    setText($("readout-sub"), m ? [m.label, `${m.width}x${m.height}`, m.fps == null ? null : `${m.fps.toFixed(1)} fps`, hint].filter(Boolean).join("  ·  ") : "no frame yet");
    $("empty").hidden = streaming;

    const green = streaming && allGreen(m), calibrated = Boolean(vision.calibrated), positioned = vision.position_ok === true;
    const done = [green, green, green, green, calibrated, calibrated, positioned, green && calibrated && positioned];
    const current = done.indexOf(false);
    steps.forEach((item, index) => {
      item.className = done[index] ? "is-done" : index === current ? "is-current" : "";
      item.querySelector(".step-mark").textContent = done[index] ? "✓" : String(index + 1);
    });
    setText(steps[7].lastChild, done[7] ? "Ready for prompts" : STEPS[7]);
    const verification = verificationLine(state.calibration?.summary);
    $("verification").hidden = !calibrated || !verification;
    setText($("verification"), verification);

    $("light").disabled = busy; $("light").setAttribute("aria-pressed", String(Boolean(vision.light)));
    setText($("light"), vision.light ? "Light off" : "Light grid");
    $("calibrate").disabled = busy;
    $("calibrate").classList.toggle("is-soft-disabled", !allGreen(m));
    $("position").disabled = busy || !calibrated;
    $("message").hidden = !state.visionMessage; setText($("message"), state.visionMessage);

    const check = state.observation?.check, marks = new Set((check?.mismatches ?? []).map(c => c.row * 8 + c.col));
    commanded.render(state.map, marks);
    const seen = state.observation?.map;
    // A camera map says display "unknown" when nothing is lit; draw its cells as seen.
    observed.render(seen ? { ...seen, display: "on", intensity: state.map?.intensity ?? 2 } : null, marks);
    setText($("badge"), check ? CHECK_LABELS[check.result] ?? check.result : "NO LOOK YET");
    $("badge").className = `check-badge${check ? ` is-${check.result}` : ""}`;
    setText($("summary"), check?.summary ?? "The camera result appears after the next drawing command.");

  }

  return {
    render,
    setVisible(value) {
      visible = value;
      if (visible) startPreview(); else stopPreview();
      if (lastState) render(lastState);
    },
    destroy() { stopPreview(); commanded.destroy(); observed.destroy(); container.replaceChildren(); }
  };
}

export function cameraPill(state) {
  // Audience tab: one pill from the last physical_check; hidden when the server has no vision.
  const vision = state.vision;
  if (!vision) return null;
  if (vision.camera !== "ok" || !vision.calibrated) return { text: "Camera: off", tone: "off" };
  const result = state.observation?.check?.result;
  if (!result) return null;
  return {
    match: { text: "Camera: confirmed", tone: "good" }, consistent_dark: { text: "Camera: confirmed", tone: "good" },
    mismatch: { text: "Camera: differs", tone: "bad" }, not_visible: { text: "Camera: not visible", tone: "bad" },
    uncertain: { text: "Camera: uncertain", tone: "warn" }
  }[result] ?? { text: "Camera: off", tone: "off" };
}
