export function createGrid(container, { size = "large" } = {}) {
  container.classList.add("led-grid", `led-grid--${size}`);
  container.setAttribute("role", "img");
  const leds = Array.from({ length: 64 }, () => {
    const led = document.createElement("span");
    led.className = "led";
    led.setAttribute("aria-hidden", "true");
    container.append(led);
    return led;
  });
  const caption = document.createElement("span");
  caption.className = "grid-caption";
  container.append(caption);
  let nextMap = null, nextMarks = null, frame = 0, destroyed = false;
  function paint() {
    frame = 0;
    if (destroyed) return;
    const map = nextMap, marks = nextMarks;
    const display = map?.display ?? "unknown";
    const brightness = 0.45 + Math.max(0, Math.min(15, map?.intensity ?? 2)) / 15 * 0.55;
    container.style.setProperty("--brightness", String(brightness));
    let lit = 0, unknown = 0;
    leds.forEach((led, index) => {
      const bit = display === "shutdown" ? "0" : display === "test" ? "1" :
        display === "unknown" ? "?" : map?.rows?.[Math.floor(index / 8)]?.[index % 8] ?? "?";
      const state = (bit === "1" ? "is-on" : bit === "0" ? "is-off" : "is-unknown") + (marks?.has(index) ? " is-marked" : "");
      if (led.dataset.state !== state) { led.dataset.state = state; led.className = `led ${state}`; }
      if (bit === "1") lit++;
      if (bit === "?") unknown++;
    });
    const label = display === "shutdown" ? "Display off" : display === "test" ? "Test mode" : display === "unknown" ? "State unknown" : "";
    caption.textContent = label;
    caption.hidden = !label || size === "thumb";
    container.setAttribute("aria-label", `8 by 8 LED matrix. ${lit} of 64 LEDs lit${unknown ? `, ${unknown} unknown` : ""}${label ? `. ${label}` : ""}.`);
  }
  return {
    // marks: optional Set of cell indices (row * 8 + col) to outline, e.g. camera mismatches.
    render(map, marks = null) { nextMap = map; nextMarks = marks; if (!frame) frame = requestAnimationFrame(paint); },
    destroy() { destroyed = true; cancelAnimationFrame(frame); container.replaceChildren(); }
  };
}