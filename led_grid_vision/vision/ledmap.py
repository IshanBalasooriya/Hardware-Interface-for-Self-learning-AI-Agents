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
