"""LED map store: owns the chip model, persists it, logs frames, notifies listeners."""

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from bridge.grid_model import Max7219Model
from config import DEFAULT_INTENSITY, MAX_HISTORY

TAIL_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


class GridStore:
    def __init__(self, state_path: Path, log_path: Path, msb_is_left: bool = True) -> None:
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self.msb_is_left = msb_is_left
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []
        self._reset()

    def _reset(self) -> None:
        self._model = Max7219Model(self.msb_is_left)
        self._seq = 0
        self._timestamp = 0.0
        self._bytes = ""
        self._map = self._build_map()

    def _build_map(self) -> dict:
        picture = self._model.picture()
        return {
            "seq": self._seq,
            "timestamp": self._timestamp,
            "display": picture["display"],
            "intensity": picture["intensity"],
            "rows": picture["rows"],
            "bytes": self._bytes,
            "warnings": picture["warnings"],
        }

    def load(self) -> bool:
        with self._lock:
            if not self.state_path.exists():
                return False
            try:
                saved = json.loads(self.state_path.read_text(encoding="utf-8"))
                model = Max7219Model.from_dict(saved["model"], self.msb_is_left)
                seq, timestamp, data = int(saved["seq"]), float(saved["timestamp"]), str(saved["bytes"])
            except (ValueError, KeyError, TypeError):
                corrupt = self.state_path.with_name(self.state_path.name + ".corrupt")
                os.replace(self.state_path, corrupt)
                logger.warning("Corrupt state file moved to %s", corrupt)
                self._reset()
                return False
            self._model, self._seq, self._timestamp, self._bytes = model, seq, timestamp, data
            self._map = self._build_map()
            return True

    def record(self, data: bytes, group_size: int = 2) -> dict:
        with self._lock:
            self._model.apply(data, group_size)
            led_map = self._commit(data.hex().upper())
        self._notify(led_map)
        return led_map

    def mark_unknown(self) -> dict:
        with self._lock:
            self._model.reset_unknown()
            led_map = self._commit("")
        self._notify(led_map)
        return led_map

    def _commit(self, data_hex: str) -> dict:
        self._seq += 1
        self._timestamp = time.time()
        self._bytes = data_hex
        self._map = self._build_map()
        self._save_state()
        self._append_log()
        return copy.deepcopy(self._map)

    def _save_state(self) -> None:
        state = {"seq": self._seq, "timestamp": self._timestamp, "bytes": self._bytes,
                 "model": self._model.to_dict()}
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _append_log(self) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self._map) + "\n")
            f.flush()

    def _notify(self, led_map: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(copy.deepcopy(led_map))
            except Exception:
                logger.exception("LED map listener failed")

    def current(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._map)

    def recent(self, count: int) -> list[dict]:
        count = max(1, min(count, MAX_HISTORY))
        with self._lock:
            if not self.log_path.exists():
                return []
            with self.log_path.open("rb") as f:
                size = f.seek(0, os.SEEK_END)
                offset = max(0, size - TAIL_BYTES)
                f.seek(offset)
                tail = f.read()
        lines = tail.decode("utf-8", errors="replace").splitlines()
        if offset > 0:
            lines = lines[1:]
        frames = []
        for line in lines:
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                frames.append(frame)
        return frames[-count:]

    def since(self, seq: int) -> list[dict]:
        """All logged frames with a seq greater than `seq`, oldest first, no cap."""
        with self._lock:
            if not self.log_path.exists():
                return []
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        frames = []
        for line in text.splitlines():
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            frame_seq = frame.get("seq")
            if isinstance(frame_seq, int) and not isinstance(frame_seq, bool) and frame_seq > seq:
                frames.append(frame)
        return frames

    def resync_bytes(self) -> bytes:
        with self._lock:
            return self._model.resync_bytes(DEFAULT_INTENSITY)

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)
