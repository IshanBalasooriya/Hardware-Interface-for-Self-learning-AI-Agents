"""Live calibration: locate and map the grid, verify it, save CALIBRATION_FILE.

.\\.venv\\Scripts\\python.exe -m scripts.calibrate [--settle-ms 150] [--no-lock] [--preview-s 4]
"""

import argparse
import json
import sys
import time

import config
from scripts.common import clear, make_show, open_all, wake
from vision import patterns as P
from vision.calibration import (MESSAGES, calibrate, classify, format_summary, reduce_frames,
                                sample_cells, save_calibration)
from vision.camera import CameraError
from vision.viewfinder import Viewfinder


def _preview(vf, camera, show, cal, seconds) -> None:
    """Show checker_0 with each sample disc coloured by its live classification."""
    if not vf.active or seconds <= 0:
        return
    vf.calibration = cal.to_dict()
    show(P.checker(0))
    deadline = time.monotonic() + seconds
    while vf.active and time.monotonic() < deadline:
        frame = camera.grab(1)[0]
        rows = classify(sample_cells(reduce_frames([frame], cal.channel, cal.reduce), cal), cal)
        vf.update(frame, "calibrated: live check on checker_0", rows=rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--settle-ms", type=int, default=config.VISION_SETTLE_MS)
    p.add_argument("--no-lock", action="store_true", help="do not try to lock exposure")
    p.add_argument("--preview-s", type=float, default=4.0, help="live disc preview after success")
    args = p.parse_args(argv)

    print(f"serial port: {config.SERIAL_PORT}   camera source: {config.CAMERA_SOURCE} "
          f"(backend {config.CAMERA_BACKEND})")
    try:
        link, camera = open_all()
    except CameraError as e:
        print(f"Calibration FAILED: camera_error\n  {MESSAGES['camera_error']}: {e}")
        return 1
    except Exception as e:
        print(f"Calibration FAILED: device_error\n  {MESSAGES['device_error']}: {e}")
        return 1
    vf = Viewfinder("LED grid calibration", camera=camera)
    result = None
    try:
        try:
            wake(link)
        except Exception as e:
            print(f"Calibration FAILED: device_error\n  {MESSAGES['device_error']}: {e}")
            return 1
        if not args.no_lock:
            print(f"exposure lock: {'locked' if camera.lock_exposure() else 'NOT locked'}")
        print(f"camera info: {json.dumps(camera.info())}")
        show = make_show(link, camera)
        result = calibrate(camera, show, intensity=config.DEFAULT_INTENSITY, settle_ms=args.settle_ms,
                           debug_dir=config.VISION_DEBUG_DIR, viewfinder=vf)
        if result.ok:
            save_calibration(result.calibration, config.CALIBRATION_FILE)
            try:
                _preview(vf, camera, show, result.calibration, args.preview_s)
            except Exception as e:  # the preview is display only
                print(f"warning: preview skipped: {e}")
    finally:
        try:
            clear(link)
        except Exception as e:  # do not mask the original error
            print(f"warning: clear failed: {e}")
        finally:
            vf.close()
            link.close()
            camera.close()

    print(format_summary(result))
    if result.ok:
        print(f"  saved            {config.CALIBRATION_FILE}")
    print(f"  debug images     {config.VISION_DEBUG_DIR}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
