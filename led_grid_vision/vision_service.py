"""VisionService: the single owner of the camera inside a running process (VSTAGE_5 B1, C2).

One lock serialises every camera operation. No method raises. No OpenCV window is ever opened (windows
from a server thread are unreliable on Windows); instead the viewfinder's own logic (vision.viewfinder.
PreviewState: lock-and-hold tracker, readout, render_view) feeds a dashboard preview:

- While at least one preview client is open and no operation (run, calibration, position check, light)
  is active, a loop grabs single frames for the preview, about PREVIEW_FPS per second.
- During operations the loop pauses and the preview shows the frames those operations grab
  (calibration steps, reads) through _PreviewSink, which stands in for the Viewfinder.
- Measurement reads never use preview frames: GridReader.read and calibrate flush and grab their own.
"""

import logging
import threading
import time
from dataclasses import asdict

import cv2

import config
from vision.calibration import calibrate, format_summary, load_calibration, save_calibration
from vision.camera import open_camera
from vision.ledmap import failed_led_map, physical_check
from vision.reader import GridReader
from vision.viewfinder import PreviewState

PREVIEW_FPS = 9.0
PREVIEW_LABEL = "live"
JPEG_QUALITY = 75

logger = logging.getLogger(__name__)


class _PreviewSink:
    """Viewfinder stand-in given to GridReader and calibrate. Inert while no preview client is open."""

    def __init__(self, service: "VisionService") -> None:
        self._service = service

    calibration = property(lambda self: self._service.preview_state.calibration,
                           lambda self, value: setattr(self._service.preview_state, "calibration", value))

    @property
    def active(self) -> bool:
        return self._service.preview_active

    def update(self, frame, label="", rows=None, status=None) -> None:
        if self.active:
            self._service._publish(frame, label, rows, status)

    def idle(self, camera, ms, label="") -> None:
        """As Viewfinder.idle: show live frames for `ms` (discarded), or sleep when no client is open."""
        deadline = time.monotonic() + ms / 1000
        while self.active and time.monotonic() < deadline:
            self.update(camera.grab(1)[0], label)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


class VisionService:
    def __init__(self, enabled, camera_factory=open_camera, calibration_path=config.CALIBRATION_FILE,
                 debug_dir=config.VISION_DEBUG_DIR) -> None:
        self.enabled = bool(enabled)
        self.camera_factory = camera_factory
        self.calibration_path = calibration_path
        self.debug_dir = debug_dir
        self.camera = None
        self.reader: GridReader | None = None
        self.camera_state = "off"  # ok | error | off
        self.camera_error: str | None = None
        self.last: dict | None = None  # the last observe() result
        self.position_ok: bool | None = None  # last check_position result since the last calibration
        self.operation: str | None = None  # run | calibrate | check_position | light: pauses the preview loop
        self.preview_state = PreviewState()
        self._sink = _PreviewSink(self)
        self._seq = 0
        self._lock = threading.RLock()  # every camera operation
        self._data_lock = threading.Lock()  # preview state and latest preview frame
        self._listeners: dict[str, list] = {"observation": [], "metrics": []}
        self._clients = 0
        self._loop_thread: threading.Thread | None = None
        self._measure_waiting = threading.Event()
        self._stopped = threading.Event()
        self._jpeg: bytes | None = None
        self._raw = None
        self._metrics: dict | None = None
        self._frame_no = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        with self._lock:
            if not self.enabled or self.camera_state == "ok":
                return
            self._stopped.clear()
            try:
                camera = self.camera_factory()
                camera.open()
            except Exception as e:
                self.camera_state, self.camera_error = "error", f"{type(e).__name__}: {e}"
                logger.warning("Vision: camera unavailable (%s); continuing on the commanded map only",
                               self.camera_error)
                return
            self.camera, self.camera_state, self.camera_error = camera, "ok", None
            self.reader = GridReader(camera, load_calibration(self.calibration_path), viewfinder=self._sink)

    def stop(self) -> None:
        self._stopped.set()
        thread = self._loop_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self.camera is not None:
                try:
                    self.camera.close()
                except Exception as e:
                    logger.warning("Vision: camera close failed: %s", e)
            self.camera, self.reader = None, None
            if self.camera_state == "ok":
                self.camera_state = "off"

    def status(self) -> dict:
        with self._lock:
            cal = self.reader.calibration if self.reader is not None else None
            return {
                "enabled": self.enabled,
                "camera": self.camera_state if self.enabled else "off",
                "calibrated": cal is not None,
                "calibration_created": cal.created if cal is not None else None,
                "last_status": self.last["observed_state"]["vision"]["status"] if self.last else None,
                "position_ok": self.position_ok,
                "preview_active": self.preview_active,
                "operation": self.operation,
            }

    def add_listener(self, kind: str, fn) -> None:
        """kind "observation": fn(observe result); kind "metrics": fn(preview metrics). Called outside the lock."""
        self._listeners[kind].append(fn)

    def _notify(self, kind: str, payload: dict) -> None:
        for fn in list(self._listeners[kind]):
            try:
                fn(payload)
            except Exception:
                logger.exception("Vision %s listener failed", kind)

    def set_operation(self, name: str | None) -> None:
        """Mark a run / calibration / position check / light change as active; the preview loop pauses."""
        self.operation = name

    # ------------------------------------------------------------ observation

    def observe(self, commanded: dict | None) -> dict:
        """{"observed_state": vision LED map, "physical_check": dict}. Never raises."""
        self._measure_waiting.set()  # the preview loop yields the camera
        try:
            with self._lock:
                self._measure_waiting.clear()
                try:
                    observed = self._read()
                except Exception as e:  # GridReader.read never raises; this guards everything around it
                    observed = self._failed("camera_error", [f"internal_error: {type(e).__name__}: {e}"])
                result = {"observed_state": observed, "physical_check": physical_check(commanded, observed)}
                self.last = result
        finally:
            self._measure_waiting.clear()
        self._notify("observation", result)
        return result

    def _read(self) -> dict:
        if not self.enabled:
            return self._failed("disabled", [])
        if self.reader is None:
            return self._failed("camera_error", [f"camera_error: {self.camera_error or 'not started'}"])
        return self.reader.read()

    def _failed(self, status: str, warnings: list[str]) -> dict:
        self._seq += 1
        return failed_led_map(self._seq, time.time(), status, warnings, 0)

    def last_frame(self):
        with self._lock:
            return None if self.reader is None else self.reader.last_frame

    # ------------------------------------------------------------ calibration and position

    def calibrate(self, show) -> dict:
        """Run calibration through `show(rows)`; save on success and reload the reader. Never raises."""
        with self._lock:
            if self.reader is None:
                return {"success": False, "reason": "camera_unavailable", "detail": {"message": self.camera_error},
                        "summary": "Calibration not run: camera unavailable."}
            self.position_ok = None
            try:
                result = calibrate(self.camera, show, intensity=config.DEFAULT_INTENSITY, debug_dir=self.debug_dir,
                                   viewfinder=self._sink)
                if result.ok:
                    save_calibration(result.calibration, self.calibration_path)
                    self.reader = GridReader(self.camera, result.calibration, viewfinder=self._sink)
                else:
                    self._sink.calibration = (self.reader.calibration.to_dict()
                                              if self.reader.calibration is not None else None)
                return {"success": result.ok, "reason": result.reason, "detail": result.detail,
                        "summary": format_summary(result)}
            except Exception as e:
                return {"success": False, "reason": "internal_error", "detail": {"message": f"{type(e).__name__}: {e}"},
                        "summary": "Calibration failed: internal error."}

    def check_position(self, show) -> dict:
        """Run the reader's position check through `show(rows)`. The caller restores the picture."""
        with self._lock:
            if self.reader is None:
                return {"ok": False, "max_corner_shift_px": None, "detail": {"reason": "camera_unavailable"}}
            try:
                result = asdict(self.reader.check_position(show))
            except Exception as e:
                result = {"ok": False, "max_corner_shift_px": None,
                          "detail": {"reason": "error", "message": f"{type(e).__name__}: {e}"}}
            self.position_ok = bool(result["ok"]) if self.reader.calibration is not None else None
            return result

    # ------------------------------------------------------------ dashboard preview

    @property
    def preview_active(self) -> bool:
        return self._clients > 0

    def open_preview(self) -> None:
        """A dashboard client opened the preview stream; the loop runs while any client remains."""
        if not self.enabled:
            return
        with self._data_lock:
            self._clients += 1
            if self._loop_thread is None or not self._loop_thread.is_alive():
                self._loop_thread = threading.Thread(target=self._loop, name="vision-preview", daemon=True)
                self._loop_thread.start()

    def close_preview(self) -> None:
        with self._data_lock:
            self._clients = max(0, self._clients - 1)

    def _loop(self) -> None:
        period = 1.0 / PREVIEW_FPS
        while self._clients > 0 and not self._stopped.is_set():
            started = time.monotonic()
            self._preview_once()
            time.sleep(max(0.005, period - (time.monotonic() - started)))

    def _preview_once(self) -> bool:
        """Grab one frame for the preview unless an operation or a measurement needs the camera."""
        if self.operation is not None or self._measure_waiting.is_set() or self.camera is None:
            return False
        if not self._lock.acquire(blocking=False):
            return False  # drop the frame rather than wait
        try:
            if self.camera is None:
                return False
            frame = self.camera.grab(1)[0]
        except Exception as e:
            logger.debug("Vision preview grab failed: %s", e)
            return False
        finally:
            self._lock.release()
        self._publish(frame, PREVIEW_LABEL)
        return True

    def _publish(self, frame, label="", rows=None, status=None) -> None:
        try:
            with self._data_lock:
                view, metrics = self.preview_state.step(frame, label, rows, status)
                ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self._jpeg, self._raw, self._metrics = buf.tobytes(), frame, metrics
                    self._frame_no += 1
        except Exception:
            logger.exception("Vision preview render failed")
            return
        self._notify("metrics", metrics)

    def preview_frame(self) -> tuple[int, bytes | None]:
        """(frame number, latest annotated JPEG). Never reads the camera."""
        with self._data_lock:
            return self._frame_no, self._jpeg

    def preview_jpeg(self) -> bytes | None:
        """Latest annotated frame; one fresh grab if there is none yet and the camera is idle."""
        if self._jpeg is None:
            self._preview_once()
        return self.preview_frame()[1]

    def metrics(self) -> dict | None:
        if self._metrics is None:
            self._preview_once()
        with self._data_lock:
            return None if self._metrics is None else dict(self._metrics)

    def raw_preview_frame(self):
        """The camera frame behind the latest preview image (for tests and diagnostics)."""
        with self._data_lock:
            return self._raw

    def lock(self, action: str, x: float | None = None, y: float | None = None) -> bool:
        """"release" = the viewfinder's `a` key; "point" = a click at canvas pixel (x, y)."""
        with self._data_lock:
            if action == "release":
                self.preview_state.release()
                return True
            if action == "point" and x is not None and y is not None:
                shape = self._raw.shape if self._raw is not None else (config.CAMERA_HEIGHT, config.CAMERA_WIDTH)
                return self.preview_state.click(float(x), float(y), shape)
            return False
