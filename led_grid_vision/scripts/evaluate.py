"""Offline calibration on a saved capture session. Never touches the board or the camera.

.\\.venv\\Scripts\\python.exe -m scripts.evaluate [session_dir]     (default: latest session)

Debug images go to <session>/eval/. CALIBRATION_FILE is not written.
"""

import argparse
import sys
from pathlib import Path

import config
from vision.calibration import calibrate, format_summary
from vision.camera import ReplayCamera
from vision.session import latest_session


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("session_dir", nargs="?", help="capture session folder (default: latest)")
    args = p.parse_args(argv)

    session = Path(args.session_dir) if args.session_dir else latest_session(config.VISION_CAPTURE_DIR)
    if session is None or not (session / "manifest.json").is_file():
        print(f"no capture session found ({args.session_dir or config.VISION_CAPTURE_DIR})")
        return 1
    camera = ReplayCamera(session)
    debug_dir = session / "eval"
    result = calibrate(camera, lambda rows: None, intensity=camera.manifest.get("intensity"),
                       settle_ms=0, debug_dir=debug_dir)
    print(f"session: {session}")
    print(format_summary(result))
    print(f"debug images: {debug_dir}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
