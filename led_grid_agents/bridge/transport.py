"""Serial transport to the firmware, plus an in-process fake with the same interface."""

import threading
import time

import serial

from bridge.fake_device import FakeDevice
from config import BAUD, SERIAL_TIMEOUT_S

BOOT_LINE = "ESP32_READY"
BOOT_WAIT_S = 5.0
READ_SLICE_S = 0.1


class TransportError(Exception):
    pass


class SerialTransport:
    def __init__(self, port: str, baud: int, timeout_s: float) -> None:
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.reboot_count = 0
        self._serial: serial.Serial | None = None
        self._buffer = b""
        self._lock = threading.Lock()

    def connect(self) -> None:
        with self._lock:
            try:
                self._serial = serial.Serial(self.port, self.baud, timeout=READ_SLICE_S)
                if self._wait_for_boot():
                    return
                self._write("PING")
                if self._read_reply(self.timeout_s) == "OK PONG":
                    return
            except (serial.SerialException, TransportError) as e:
                self._close()
                raise TransportError(f"connect to {self.port} failed: {e}") from e
            self._close()
            raise TransportError(f"no ESP32_READY or PONG from {self.port}")

    def send(self, line: str) -> str:
        with self._lock:
            try:
                self._write(line)
                return self._read_reply(self.timeout_s)
            except serial.SerialException as e:
                raise TransportError(str(e)) from e

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self._buffer = b""

    def _port(self) -> serial.Serial:
        if self._serial is None or not self._serial.is_open:
            raise TransportError("port not open")
        return self._serial

    def _write(self, line: str) -> None:
        port = self._port()
        port.write((line + "\n").encode("ascii"))
        port.flush()

    def _read_line(self) -> str:
        """Return one complete stripped line, or "" if none finished within one read slice."""
        self._buffer += self._port().readline()
        if not self._buffer.endswith(b"\n"):
            return ""
        raw, self._buffer = self._buffer, b""
        return raw.decode("ascii", errors="replace").strip()

    def _wait_for_boot(self) -> bool:
        deadline = time.monotonic() + BOOT_WAIT_S
        while time.monotonic() < deadline:
            if self._read_line() == BOOT_LINE:
                return True
        return False

    def _read_reply(self, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            text = self._read_line()
            if text == BOOT_LINE:
                self.reboot_count += 1
            elif text.startswith(("OK", "ERR")):
                return text
        raise TransportError("timeout waiting for reply")


class FakeTransport:
    def __init__(self, device: FakeDevice | None = None) -> None:
        self.device = device if device is not None else FakeDevice()
        self.reboot_count = 0
        self._reboot_pending = False
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._reboot_pending = False

    def send(self, line: str) -> str:
        with self._lock:
            if self._reboot_pending:
                self._reboot_pending = False
                self.reboot_count += 1
            reply = self.device.handle(line)
            if reply is None:
                raise TransportError("timeout waiting for reply")
            return reply

    def simulate_reboot(self) -> None:
        self._reboot_pending = True

    def close(self) -> None:
        pass


def open_transport(port: str) -> SerialTransport | FakeTransport:
    if port == "fake":
        return FakeTransport()
    return SerialTransport(port, BAUD, SERIAL_TIMEOUT_S)
