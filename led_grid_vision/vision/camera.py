"""Camera layer: one small interface over a real camera, a replayed session and a fake renderer."""

import numpy as np
import cv2

import config

BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}


class CameraError(Exception):
    pass


class BaseCamera:
    """Shared context-manager plumbing. Subclasses implement open/close/grab/info."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def flush(self, n: int) -> None:
        self.grab(n)

    def lock_exposure(self) -> bool:
        return False


class OpenCVCamera(BaseCamera):
    def __init__(self, source: str, width: int, height: int, backend: str, warmup_frames: int) -> None:
        self.source = str(source)
        self.width, self.height = width, height
        self.backend = backend
        self.warmup_frames = warmup_frames
        self._cap = None
        self._exposure_locked = False
        self._auto_wb_off = False
        self._autofocus_off = False

    def open(self) -> None:
        if self.backend not in BACKENDS:
            raise CameraError(f"unknown backend {self.backend!r}; use one of {sorted(BACKENDS)}")
        src = int(self.source) if self.source.isdigit() else self.source
        cap = cv2.VideoCapture(src, BACKENDS[self.backend])
        if not cap.isOpened():
            cap.release()
            raise CameraError(f"cannot open camera source {self.source!r} with backend {self.backend}")
        self._cap = cap
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._auto_wb_off = self._try_set(cv2.CAP_PROP_AUTO_WB, 0)
        self._autofocus_off = self._try_set(cv2.CAP_PROP_AUTOFOCUS, 0)
        try:
            self.flush(self.warmup_frames)
        except CameraError:
            self.close()
            raise

    def _try_set(self, prop: int, value: float) -> bool:
        try:
            self._cap.set(prop, value)
            return self._cap.get(prop) == value
        except cv2.error:
            return False

    def lock_exposure(self) -> bool:
        """Switch auto-exposure off and hold the current exposure. True only if read back as manual."""
        if self._cap is None:
            return False
        cap = self._cap
        try:
            orig_auto = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
            exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
            # DirectShow uses 0.25 for manual and 0.75 for auto; some drivers use 0 / 1.
            for manual in (0.25, 0.0):
                if manual == orig_auto:
                    continue
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
                if cap.get(cv2.CAP_PROP_AUTO_EXPOSURE) == manual:
                    cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
                    self._exposure_locked = True
                    return True
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, orig_auto)
        except cv2.error:
            pass
        self._exposure_locked = False
        return False

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def grab(self, n: int) -> list[np.ndarray]:
        if self._cap is None:
            raise CameraError("camera not open")
        frames = []
        for _ in range(n):
            ok, frame = self._cap.read()
            if not ok or frame is None:
                raise CameraError("frame read failed")
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            frames.append(frame)
        return frames

    def info(self) -> dict:
        cap = self._cap
        get = (lambda p: float(cap.get(p))) if cap is not None else (lambda p: 0.0)
        return {
            "source": self.source,
            "backend": self.backend,
            "width": int(get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": get(cv2.CAP_PROP_FPS),
            "exposure_locked": self._exposure_locked,
            "exposure": get(cv2.CAP_PROP_EXPOSURE) if self._exposure_locked else None,
            "auto_wb_off": self._auto_wb_off,
            "autofocus_off": self._autofocus_off,
        }


class ReplayCamera(BaseCamera):
    """Serves frames from a saved capture session."""

    def __init__(self, session_dir) -> None:
        from vision.session import load_session

        self.manifest = load_session(session_dir)
        self._item = None
        self._pos = 0
        self._cache: dict[str, np.ndarray] = {}

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def select(self, rows: list[str]) -> None:
        for item in self.manifest["items"]:
            if item["rows"] == list(rows):
                self._item, self._pos = item, 0
                return
        raise CameraError(f"no item in session shows {rows!r}")

    def flush(self, n: int) -> None:
        pass

    def grab(self, n: int) -> list[np.ndarray]:
        if self._item is None:
            raise CameraError("no item selected")
        paths = self._item["paths"]
        frames = []
        for _ in range(n):
            path = paths[self._pos % len(paths)]
            self._pos += 1
            if path not in self._cache:
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise CameraError(f"cannot read {path}")
                self._cache[path] = img
            frames.append(self._cache[path].copy())
        return frames

    def info(self) -> dict:
        return dict(self.manifest["camera"])


def open_camera(source: str | None = None):
    """Build (not open) the camera selected by `source` or config.CAMERA_SOURCE."""
    source = config.CAMERA_SOURCE if source is None else str(source)
    if source.lower() == "fake":
        from vision.fake_camera import FakeCamera

        return FakeCamera()
    return OpenCVCamera(source, config.CAMERA_WIDTH, config.CAMERA_HEIGHT,
                        config.CAMERA_BACKEND, config.CAMERA_WARMUP_FRAMES)
