"""Bridge: the single entry point for hardware actions. Keeps the LED map in step with the device."""

import logging
import re
import threading
import time
from typing import Callable

from bridge.grid_store import GridStore
from bridge.transport import TransportError
from config import CLOCK_PIN, DATA_PIN, LATCH_PIN, MAX_SHIFT_BYTES, MAX_WAIT_MS

WAIT_SLICE_S = 0.05
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")

logger = logging.getLogger(__name__)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_hex(data_hex: object) -> bool:
    return (isinstance(data_hex, str) and len(data_hex) % 2 == 0
            and 0 < len(data_hex) // 2 <= MAX_SHIFT_BYTES and _HEX_RE.fullmatch(data_hex) is not None)


class Bridge:
    def __init__(self, transport, store: GridStore) -> None:
        self.transport = transport
        self.store = store
        self.connected = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self.transport.connect()
        self.store.load()
        self.connected = True
        self.resync()

    def ping(self) -> bool:
        with self._lock:
            try:
                self.connected = self.transport.send("PING") == "OK PONG"
            except TransportError:
                self.connected = False
        return self.connected

    def shift_out(self, data_pin: int, clock_pin: int, latch_pin: int,
                  group_size: int, data_hex: str) -> dict:
        if not all(_is_int(v) for v in (data_pin, clock_pin, latch_pin, group_size)) or not _valid_hex(data_hex):
            return {"success": False, "error": "bad_args"}
        data_hex = data_hex.upper()
        reboots_before = self.transport.reboot_count
        with self._lock:
            result = self._send_and_record(data_pin, clock_pin, latch_pin, group_size, data_hex)
        if self.transport.reboot_count != reboots_before:
            logger.warning("Device rebooted during shift_out; re-syncing")
            self.resync()
            result["resynced"] = True
        return result

    def _send_and_record(self, data_pin: int, clock_pin: int, latch_pin: int,
                         group_size: int, data_hex: str) -> dict:
        try:
            reply = self.transport.send(f"SHIFT_OUT {data_pin} {clock_pin} {latch_pin} {group_size} {data_hex}")
        except TransportError:
            self.connected = False
            return {"success": False, "error": "timeout"}
        if reply != "OK":
            return {"success": False, "error": reply, "raw_response": reply}
        result: dict = {"success": True, "raw_response": reply}
        if (data_pin, clock_pin, latch_pin) == (DATA_PIN, CLOCK_PIN, LATCH_PIN):
            result["decoded_state"] = self.store.record(bytes.fromhex(data_hex), group_size)
        return result

    def wait(self, duration_ms: int, should_stop: Callable[[], bool] | None = None) -> dict:
        deadline = time.monotonic() + max(0, min(duration_ms, MAX_WAIT_MS)) / 1000
        while True:
            if should_stop is not None and should_stop():
                return {"success": True, "stopped": True}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"success": True}
            time.sleep(min(WAIT_SLICE_S, remaining))

    def resync(self) -> dict:
        return self.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, 2, self.store.resync_bytes().hex())

    def reconnect(self) -> bool:
        with self._lock:
            try:
                self.transport.close()
                self.transport.connect()
                self.connected = True
            except Exception as e:
                logger.warning("Reconnect failed: %s", e)
                self.connected = False
        if self.connected:
            self.resync()
        return self.connected

    def close(self) -> None:
        with self._lock:
            self.transport.close()
            self.connected = False
