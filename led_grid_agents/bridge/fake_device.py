"""In-process fake of the firmware: same protocol rules and reply strings as firmware/src/main.cpp."""

import re

MAX_LINE_CHARS = 255
MAX_BYTES = 64
_INT_RE = re.compile(r"[0-9]{1,9}")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")


class FakeDevice:
    def __init__(self, allowed_pins: tuple[int, ...] = (25, 26, 27)) -> None:
        self.allowed_pins = tuple(allowed_pins)
        self.sent: list[tuple[tuple[int, int, int], int, bytes]] = []

    def handle(self, line: str) -> str | None:
        """Return the reply line for one input line, or None for an empty line."""
        line = line.removesuffix("\n")
        if len(line) > MAX_LINE_CHARS:
            return "ERR BAD_ARGS"
        line = line.removesuffix("\r")
        if not line:
            return None
        tokens = line.split(" ")
        if tokens[0] == "PING":
            return "OK PONG" if len(tokens) == 1 else "ERR BAD_ARGS"
        if tokens[0] == "SHIFT_OUT":
            return self._shift_out(tokens)
        return "ERR UNKNOWN_COMMAND"

    def _shift_out(self, tokens: list[str]) -> str:
        if len(tokens) != 6:
            return "ERR BAD_ARGS"
        if not all(_INT_RE.fullmatch(t) for t in tokens[1:5]):
            return "ERR BAD_ARGS"
        data, clock, latch, group = (int(t) for t in tokens[1:5])
        if not all(pin in self.allowed_pins for pin in (data, clock, latch)):
            return "ERR PIN_NOT_ALLOWED"
        if len({data, clock, latch}) != 3:
            return "ERR BAD_ARGS"
        if group < 1:
            return "ERR BAD_ARGS"
        hex_str = tokens[5]
        if len(hex_str) < 2 or len(hex_str) % 2 != 0 or not _HEX_RE.fullmatch(hex_str):
            return "ERR BAD_ARGS"
        payload = bytes.fromhex(hex_str)
        if len(payload) > MAX_BYTES or len(payload) % group != 0:
            return "ERR BAD_ARGS"
        self.sent.append(((data, clock, latch), group, payload))
        return "OK"
