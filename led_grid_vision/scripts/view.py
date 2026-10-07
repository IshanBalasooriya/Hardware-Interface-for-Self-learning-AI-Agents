"""Live viewfinder for aiming the camera. Locks onto the lit grid; a re-locks, click sets a manual centre,
s saves a snapshot, q quits.

.\\.venv\\Scripts\\python.exe -m scripts.view [--light]

--light opens the board, wakes it and lights every LED so the grid is easy to find; clears on exit.
"""

import argparse
import sys

import config
from link.serial_link import open_link
from scripts.common import clear, make_show, wake
from vision.camera import open_camera
from vision.patterns import all_on
from vision.viewfinder import Viewfinder

LABEL = "view - a re-lock, click = manual centre, s snapshot, q quit"


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--light", action="store_true", help="light the whole grid while viewing")
    args = p.parse_args(argv)

    print(f"camera source: {config.CAMERA_SOURCE} (backend {config.CAMERA_BACKEND})"
          + (f"   serial port: {config.SERIAL_PORT}" if args.light else ""))
    link = None
    camera = open_camera()
    camera.open()
    vf = Viewfinder(camera=camera)
    try:
        if args.light:
            link = open_link()
            link.open()
            wake(link)
            make_show(link, camera)(all_on())
            print("grid lit (all on)")
        if not vf.active:
            print("viewfinder is off (fake camera, VISION_VIEW=0, or under pytest); nothing to show")
            return
        print(LABEL)
        while vf.active:
            vf.update(camera.grab(1)[0], LABEL)
        if not vf.quit_requested:
            print("viewfinder stopped after a window error")
    finally:
        vf.close()
        if link is not None:
            try:
                clear(link)
            except Exception as e:
                print(f"warning: clear failed: {e}")
            finally:
                link.close()
        camera.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
