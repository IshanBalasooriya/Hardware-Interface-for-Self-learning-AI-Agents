"""Rows <-> row-frame hex, and comparison (VISION_MASTER.md sections 6.1, 6.2, 6.4).

Row and bit order confirmed against ..\\led_grid\\bridge\\grid_model.py: address byte i+1 is
rows[i] (top to bottom); with MSB_IS_LEFT bit 7 is the leftmost LED.
"""

import config

N = 8


def rows_to_hex(rows: list[str], msb_is_left: bool = config.MSB_IS_LEFT) -> str:
    if len(rows) != N or any(len(r) != N or set(r) - {"0", "1"} for r in rows):
        raise ValueError(f"rows must be 8 strings of 8 '0'/'1': {rows!r}")
    out = []
    for i, row in enumerate(rows):
        value = int(row if msb_is_left else row[::-1], 2)
        out.append(f"{i + 1:02X}{value:02X}")
    return "".join(out)


def hex_to_rows(data_hex: str, base_rows: list[str] | None = None,
                msb_is_left: bool = config.MSB_IS_LEFT) -> list[str]:
    """Apply 2-byte (address, data) groups with address 01..08 onto base_rows."""
    rows = list(base_rows) if base_rows is not None else ["0" * N] * N
    data = bytes.fromhex(data_hex)
    for i in range(0, len(data) - 1, 2):
        addr, value = data[i], data[i + 1]
        if 1 <= addr <= N:
            bits = format(value, "08b")
            rows[addr - 1] = bits if msb_is_left else bits[::-1]
    return rows


def compare(expected_rows: list[str], observed_rows: list[str]) -> list[dict]:
    out = []
    for r in range(N):
        for c in range(N):
            e, o = expected_rows[r][c], observed_rows[r][c]
            if e in "01" and o in "01" and e != o:
                out.append({"row": r, "col": c, "expected": e, "observed": o})
    return out


def build_led_map(seq: int, timestamp: float, rows: list[str], warnings: list[str], status: str,
                  read_ms: float) -> dict:
    """The vision LED map (master section 6.3): led_grid's seven keys, then `source` and `vision`."""
    rows = [str(r) for r in rows]
    uncertain = sum(r.count("?") for r in rows)
    return {
        "seq": int(seq),
        "timestamp": float(timestamp),
        "display": "on" if any("1" in r for r in rows) else "unknown",
        "intensity": None,
        "rows": rows,
        "bytes": rows_to_hex(rows) if uncertain == 0 else None,
        "warnings": [str(w) for w in warnings],
        "source": "camera",
        "vision": {"status": str(status), "uncertain": int(uncertain), "read_ms": int(round(float(read_ms)))},
    }


def failed_led_map(seq: int, timestamp: float, status: str, warnings: list[str], read_ms: float) -> dict:
    """The all-unknown form, for `uncalibrated` and `camera_error`."""
    return build_led_map(seq, timestamp, ["?" * N] * N, warnings, status, read_ms)


# ---------------------------------------------------------------- physical check (stage 5, B2)

CHECK_RESULTS = ("match", "mismatch", "not_visible", "consistent_dark", "uncertain", "unavailable")


def effective_rows(commanded: dict | None) -> list[str]:
    """What the chip should light: shutdown -> all 0, display test -> all 1, else the commanded rows."""
    if not commanded or not isinstance(commanded.get("rows"), list) or len(commanded["rows"]) != N:
        return ["?" * N] * N
    if commanded.get("display") == "shutdown":
        return ["0" * N] * N
    if commanded.get("display") == "test":
        return ["1" * N] * N
    return [str(r) for r in commanded["rows"]]


def _ranges(cols: list[int]) -> str:
    parts, start = [], cols[0]
    for prev, cur in zip(cols, cols[1:] + [None]):
        if cur != prev + 1:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = cur
    return ", ".join(parts)


def _where(mismatches: list[dict]) -> str:
    by_row: dict[int, list[int]] = {}
    for m in mismatches:
        by_row.setdefault(m["row"], []).append(m["col"])
    return ", ".join(f"row {r} col{'s' if len(c) > 1 else ''} {_ranges(sorted(c))}" for r, c in sorted(by_row.items()))


def physical_check(commanded: dict | None, observed: dict | None) -> dict:
    """Compare the commanded LED map with the camera's (vision) LED map. Pure; no camera needed.

    Rows and columns in `mismatches` and `summary` are 0-based (row 0 = top, col 0 = left)."""
    status = ((observed or {}).get("vision") or {}).get("status")
    warnings = [str(w) for w in (observed or {}).get("warnings", [])]

    def out(result, summary, mismatches=()):
        return {"result": result, "mismatches": list(mismatches), "warnings": warnings, "summary": summary}

    if status not in ("ok", "dark", "unreliable"):
        reason = {None: "vision is off", "disabled": "vision is off", "uncalibrated": "camera not calibrated",
                  "camera_error": "camera error"}.get(status, str(status))
        return out("unavailable", f"Camera unavailable ({reason}); the picture is not camera-confirmed.")
    if status == "unreliable":
        return out("uncertain", f"Camera view unreliable ({', '.join(warnings) or 'no detail'}); "
                                "the picture is not camera-confirmed.")
    want = effective_rows(commanded)
    lit = sum(r.count("1") for r in want)
    if status == "dark":
        if lit:
            return out("not_visible", f"Camera sees no lit LEDs, but {lit} should be lit "
                                      "(covered, moved, disconnected or unpowered?).")
        return out("consistent_dark", "Camera sees the display dark, as commanded.")

    seen = observed["rows"]
    known = sum(1 for r in range(N) for c in range(N) if want[r][c] in "01" and seen[r][c] in "01")
    if known == 0:
        return out("uncertain", "The commanded picture is unknown; the camera cannot confirm it.")
    mismatches = [{"row": d["row"], "col": d["col"], "commanded": d["expected"], "observed": d["observed"]}
                  for d in compare(want, seen)]
    if mismatches:
        n = len(mismatches)
        return out("mismatch", f"Camera sees {n} LED{'s' if n > 1 else ''} differ from the command "
                               f"({_where(mismatches)}).", mismatches)
    return out("match", f"Camera confirms the picture ({known}/{N * N} LEDs).")
