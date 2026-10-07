"""Capture-session writer and reader (VISION_MASTER.md section 6.6)."""

import json
import os
import time
from pathlib import Path

import numpy as np
import cv2

VERSION = 1


def write_png(path: Path, img: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise OSError(f"PNG encode failed for {path}")
    Path(path).write_bytes(buf.tobytes())


def write_json_atomic(path: Path, obj) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class SessionWriter:
    def __init__(self, base_dir, camera_info: dict, intensity: int, settle_ms: int) -> None:
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        self.created = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.created))
        path, n = base / stamp, 2
        while path.exists():
            path, n = base / f"{stamp}_{n}", n + 1
        path.mkdir()
        self.dir = path
        self.manifest = {"version": VERSION, "created": self.created, "camera": dict(camera_info),
                         "intensity": intensity, "settle_ms": settle_ms, "items": []}

    def add(self, name: str, rows: list[str], frames: list[np.ndarray]) -> None:
        index = len(self.manifest["items"])
        item_dir = f"{index:02d}_{name}"
        (self.dir / item_dir).mkdir()
        names = []
        for i, frame in enumerate(frames):
            names.append(f"frame_{i:02d}.png")
            write_png(self.dir / item_dir / names[-1], frame)
        self.manifest["items"].append({"index": index, "name": name, "rows": list(rows),
                                       "dir": item_dir, "frames": names})

    def close(self) -> None:
        write_json_atomic(self.dir / "manifest.json", self.manifest)


def load_session(session_dir) -> dict:
    """Manifest with `session_dir` and per-item absolute frame `paths` added."""
    session_dir = Path(session_dir).resolve()
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["session_dir"] = str(session_dir)
    for item in manifest["items"]:
        item["paths"] = [str(session_dir / item["dir"] / f) for f in item["frames"]]
    return manifest


def latest_session(base_dir) -> Path | None:
    base = Path(base_dir)
    if not base.is_dir():
        return None
    sessions = [p for p in base.iterdir() if (p / "manifest.json").is_file()]
    return max(sessions, key=lambda p: p.name) if sessions else None
