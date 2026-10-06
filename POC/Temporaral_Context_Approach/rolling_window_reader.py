"""Read-only helper for consuming the rolling-window file produced by
telemetry_service.py. Used by agent/agent_loop.py to optionally seed a
discovery run with recent sensor history. Pure file reader: never writes,
no MQTT/paho dependency, safe to import from either architecture.

The file format is minimal by design (see RollingWindow in
telemetry_service.py): one explanatory comment line, then one bare integer
reading per line, newest first. This module mirrors that exactly -- it does
not reconstruct timestamps/seq/source, since the LLM-facing use case never
needed them; recency is already encoded by position in the list.
"""
from pathlib import Path


def load_rolling_window(path):
    """Return the reading values currently in the rolling-window file,
    newest first (matching the file's own top-to-bottom order), or [] if
    the file doesn't exist yet, is empty, or unreadable. The comment header
    line and any unparsable line (e.g. caught mid-write) are skipped, not
    fatal.
    """
    p = Path(path)
    if not p.exists():
        return []
    values = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    values.append(int(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return values


def format_rolling_window(values, max_entries=None):
    """Format a newest-first list of reading values as a plain-text block
    for an LLM prompt. Raw values only -- no computed trend/average/
    statistics; the model reasons over the history itself, same as it
    already reasons over live reads. Returns "" if there's nothing to show.
    """
    if not values:
        return ""
    if max_entries is not None:
        values = values[:max_entries]  # newest-first: head = most recent
    lines = [f"  {v}" for v in values]
    return (
        f"Recent sensor history from continuous background telemetry "
        f"(most recent first, {len(values)} readings):\n" + "\n".join(lines)
    )
