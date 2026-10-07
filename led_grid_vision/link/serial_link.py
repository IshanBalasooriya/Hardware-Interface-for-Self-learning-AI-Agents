"""Thin generic serial sender: PING and SHIFT_OUT only, used to put known pictures on the grid.

Protocol confirmed against ..\\led_grid\\firmware\\src\\main.cpp: newline-terminated ASCII,
boot line ESP32_READY, PING -> OK PONG, SHIFT_OUT <data> <clock> <latch> <group> <hex> -> OK,
errors start with ERR.
"""

import string
import time

import config

BOOT_DRAIN_S = 3.0
PING_ATTEMPTS = 5


class LinkError(Exception):
    """Raised by open() only."""


def validate_hex(data_hex: str) -> str | None:
    """Return a reason the hex would be rejected, or None if it is acceptable."""
    if not data_hex:
        return "empty"
    if len(data_hex) % 2:
        return "odd length"
    if any(c not in string.hexdigits for c in data_hex):
        return "not hex"
    n = len(data_hex) // 2
    if n > config.MAX_SHIFT_BYTES:
        return f"{n} bytes > {config.MAX_SHIFT_BYTES}"
    if n % config.GROUP_SIZE:
        return f"{n} bytes not a multiple of group size {config.GROUP_SIZE}"
    return None


def build_shift_out_line(data_hex: str) -> str:
    return (f"SHIFT_OUT {config.DATA_PIN} {config.CLOCK_PIN} {config.LATCH_PIN} "
            f"{config.GROUP_SIZE} {data_hex.upper()}\n")


class GridLink:
    def __init__(self, port: str, baud: int, timeout_s: float) -> None:
        self.port, self.baud, self.timeout_s = port, baud, timeout_s
        self._ser = None

    def open(self) -> None:
        import serial  # local: only the real link needs pyserial

        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.2)
        except serial.SerialException as e:
            raise LinkError(f"cannot open {self.port}: {e}") from e
        # The board may reset on open; discard boot output.
        deadline = time.monotonic() + BOOT_DRAIN_S
        while time.monotonic() < deadline:
            line = self._ser.readline().decode("ascii", "replace").strip()
            if line == "ESP32_READY":
                break
        self._ser.timeout = self.timeout_s
        for _ in range(PING_ATTEMPTS):
            self._ser.reset_input_buffer()
            if self.ping():
                return
        self.close()
        raise LinkError(f"no OK PONG from {self.port} after {PING_ATTEMPTS} attempts")

    def _request(self, line: str) -> str:
        self._ser.write(line.encode("ascii"))
        reply = self._ser.readline().decode("ascii", "replace").strip()
        return reply or "TIMEOUT"

    def ping(self) -> bool:
        return self._request("PING\n") == "OK PONG"

    def shift_out(self, data_hex: str) -> tuple[bool, str]:
        reason = validate_hex(data_hex)
        if reason:
            return False, f"REJECTED {reason}"
        reply = self._request(build_shift_out_line(data_hex))
        return reply == "OK", reply

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None


class FakeLink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._fail = 0

    def open(self) -> None:
        pass

    def ping(self) -> bool:
        return True

    def fail_next(self, n: int) -> None:
        self._fail = n

    def shift_out(self, data_hex: str) -> tuple[bool, str]:
        reason = validate_hex(data_hex)
        if reason:
            return False, f"REJECTED {reason}"
        if self._fail > 0:
            self._fail -= 1
            return False, "ERR BAD_ARGS"
        self.sent.append(data_hex.upper())
        return True, "OK"

    def close(self) -> None:
        pass


def open_link():
    """Build (not open) the link selected by config.SERIAL_PORT."""
    if config.SERIAL_PORT.lower() == "fake":
        return FakeLink()
    return GridLink(config.SERIAL_PORT, config.BAUD, config.SERIAL_TIMEOUT_S)
