"""VisionService: the single owner of the camera inside a running process (VSTAGE_5 B1).

One lock serialises every camera operation. No method raises. No viewfinder window is opened:
the reader gets no Viewfinder (OpenCV windows from a server thread are unreliable on Windows)."""

import logging
import threading
import time
from dataclasses import asdict

import config
from vision.calibration import calibrate, format_summary, load_calibration, save_calibration
from vision.camera import open_camera
from vision.ledmap import failed_led_map, physical_check
from vision.reader import GridReader

logger = logging.getLogger(__name__)


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
        self._seq = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        with self._lock:
            if not self.enabled or self.camera_state == "ok":
                return
            try:
                camera = self.camera_factory()
                camera.open()
            except Exception as e:
                self.camera_state, self.camera_error = "error", f"{type(e).__name__}: {e}"
                logger.warning("Vision: camera unavailable (%s); continuing on the commanded map only",
                               self.camera_error)
                return
            self.camera, self.camera_state, self.camera_error = camera, "ok", None
            self.reader = GridReader(camera, load_calibration(self.calibration_path))

    def stop(self) -> None:
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
            }

    # ------------------------------------------------------------ observation

    def observe(self, commanded: dict | None) -> dict:
        """{"observed_state": vision LED map, "physical_check": dict}. Never raises."""
        with self._lock:
            try:
                observed = self._read()
            except Exception as e:  # GridReader.read never raises; this guards everything around it
                observed = self._failed("camera_error", [f"internal_error: {type(e).__name__}: {e}"])
            result = {"observed_state": observed, "physical_check": physical_check(commanded, observed)}
            self.last = result
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
            try:
                result = calibrate(self.camera, show, intensity=config.DEFAULT_INTENSITY, debug_dir=self.debug_dir)
                if result.ok:
                    save_calibration(result.calibration, self.calibration_path)
                    self.reader = GridReader(self.camera, result.calibration)
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
                return asdict(self.reader.check_position(show))
            except Exception as e:
                return {"ok": False, "max_corner_shift_px": None,
                        "detail": {"reason": "error", "message": f"{type(e).__name__}: {e}"}}
