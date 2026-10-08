import { createGrid } from "./grid.js";
import { icon } from "./icons.js";

const time = at => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const element = (tag, className, value) => {
  const node = document.createElement(tag); node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
};

export function createTrace(scroll, list, empty, jump, count) {
  const records = new Map();
  let follow = true, frame = 0, disposed = false;
  function scrollToLatest() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      if (disposed || !scroll.clientHeight) return;
      scroll.scrollTop = scroll.scrollHeight; jump.hidden = true;
    });
  }
  function onScroll() {
    if (!scroll.clientHeight) return;
    follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 48;
    if (!follow) cancelAnimationFrame(frame);
    jump.hidden = follow;
  }
  function onJump() { follow = true; scrollToLatest(); }
  scroll.addEventListener("scroll", onScroll, { passive: true });
  jump.addEventListener("click", onJump);

  function build(entry, open = false) {
    const node = element("article", `trace-entry trace-entry--${entry.view}`);
    let preview = null;
    if (entry.view === "start") {
      const meta = element("div", "run-meta");
      meta.append(element("span", "run-id", `RUN ${entry.runId ?? "-"}`), element("time", "trace-time", time(entry.at)));
      node.append(meta, element("h4", "run-prompt", entry.prompt));
    } else if (entry.view === "message") {
      const heading = element("div", "entry-heading");
      heading.append(element("span", "agent-label", "Agent"), element("time", "trace-time", time(entry.at)));
      node.append(heading, element("p", "agent-text", entry.text));
    } else if (entry.view === "tool") {
      const details = element("details", "tool-details"); details.open = open;
      const summary = element("summary", "tool-summary");
      summary.innerHTML = `${icon("chevron", "tool-chevron")}<span class="tool-description"><strong class="tool-name"></strong><span class="tool-arguments"></span></span><span class="tool-preview"></span><span class="tool-meta"><span class="tool-state"></span><span class="tool-duration"></span></span>`;
      summary.querySelector(".tool-name").textContent = entry.tool;
      summary.querySelector(".tool-arguments").textContent = entry.summary || "No arguments";
      const state = summary.querySelector(".tool-state");
      state.classList.add(entry.pending ? "is-pending" : entry.success ? "is-success" : "is-danger");
      state.innerHTML = entry.pending ? '<span class="pending-dot"></span> PENDING' : `${icon(entry.success ? "check" : "close")} ${entry.success ? "OK" : "FAILED"}`;
      summary.querySelector(".tool-duration").textContent = entry.duration == null ? "" : `${entry.duration} ms`;
      if (entry.preview) { preview = createGrid(summary.querySelector(".tool-preview"), { size: "thumb" }); preview.render(entry.preview); }
      else summary.querySelector(".tool-preview").hidden = true;
      const body = element("div", "tool-body");
      body.append(element("div", "code-label", "ARGUMENTS"), element("pre", "tool-json", JSON.stringify(entry.args ?? {}, null, 2)));
      body.append(element("div", "code-label", "RESULT"), element("pre", "tool-json", entry.pending ? "Waiting for the tool result..." : JSON.stringify(entry.result ?? {}, null, 2)));
      details.append(summary, body); node.append(details);
    } else if (entry.view === "saved") {
      node.innerHTML = icon("save");
      const line = element("p", "saved-text");
      line.append(document.createTextNode("Saved skill "), element("code", "skill-name", entry.name), document.createTextNode(` (v${entry.version})`));
      node.append(line);
    } else if (entry.view === "finish") {
      const failed = entry.outcome === "error" || entry.outcome === "max_turns";
      if (failed) node.classList.add("is-danger");
      const heading = element("div", "finish-heading");
      heading.innerHTML = icon(failed ? "close" : entry.outcome === "stopped" ? "stop" : "check");
      heading.append(element("strong", "finish-title", entry.outcome === "stopped" ? "Run stopped" : entry.outcome === "error" ? "Run failed" : entry.outcome === "max_turns" ? "Step limit reached" : "Run completed"));
      node.append(heading);
      if (entry.summary) node.append(element("p", "finish-summary", entry.summary));
      if (entry.error) node.append(element("p", "finish-error", entry.error));
    }
    return { node, revision: entry.revision ?? 0, preview };
  }
  return {
    render(entries) {
      const valid = new Set(entries.map(entry => entry.id));
      records.forEach((record, id) => {
        if (!valid.has(id)) { record.preview?.destroy(); record.node.remove(); records.delete(id); }
      });
      entries.forEach(entry => {
        const old = records.get(entry.id);
        if (!old) { const record = build(entry); records.set(entry.id, record); list.append(record.node); }
        else if (old.revision !== (entry.revision ?? 0)) {
          const record = build(entry, old.node.querySelector("details")?.open ?? false);
          old.preview?.destroy(); old.node.replaceWith(record.node); records.set(entry.id, record);
        }
      });
      empty.hidden = entries.length > 0;
      count.textContent = `${entries.length} entries`;
      if (follow) scrollToLatest();
    },
    onVisible() { if (follow) scrollToLatest(); },
    destroy() {
      disposed = true; cancelAnimationFrame(frame);
      scroll.removeEventListener("scroll", onScroll); jump.removeEventListener("click", onJump);
      records.forEach(record => record.preview?.destroy()); records.clear();
    }
  };
}