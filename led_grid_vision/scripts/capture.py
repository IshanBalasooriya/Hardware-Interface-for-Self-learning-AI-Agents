"""Record a capture session of the standard test patterns and print the feasibility report.

.\\.venv\\Scripts\\python.exe -m scripts.capture [--frames 6] [--settle-ms 150] [--no-lock] [--out DIR]
"""

import argparse
import json
import sys

import config
from scripts.common import clear, make_show, open_all, wake
from vision.feasibility import analyse, format_report
from vision.patterns import standard_set
from vision.session import SessionWriter, write_json_atomic, write_png
from vision.viewfinder import Viewfinder


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--frames", type=int, default=config.VISION_AVG_FRAMES)
    p.add_argument("--settle-ms", type=int, default=config.VISION_SETTLE_MS)
    p.add_argument("--no-lock", action="store_true", help="do not try to lock exposure")
    p.add_argument("--out", default=str(config.VISION_CAPTURE_DIR), help="base folder for sessions")
    args = p.parse_args(argv)

    print(f"serial port: {config.SERIAL_PORT}   camera source: {config.CAMERA_SOURCE} "
          f"(backend {config.CAMERA_BACKEND})")
    link, camera = open_all()
    vf = Viewfinder(camera=camera)
    captured = {}
    try:
        wake(link)
        if not args.no_lock:
            print(f"exposure lock: {'locked' if camera.lock_exposure() else 'NOT locked'}")
        info = camera.info()
        print(f"camera info: {json.dumps(info)}")
        writer = SessionWriter(args.out, info, config.DEFAULT_INTENSITY, args.settle_ms)
        show = make_show(link, camera)
        items = standard_set()
        for i, (name, rows) in enumerate(items):
            label = f"capture {i + 1}/{len(items)} {name}"
            show(rows)
            vf.idle(camera, args.settle_ms, label)
            camera.flush(config.VISION_FLUSH_FRAMES)
            frames = camera.grab(args.frames)
            vf.update(frames[-1], label)  # draws on a copy; the saved frames stay raw
            writer.add(name, rows, frames)
            if name in ("all_off", "all_on"):
                captured[name] = frames
            print(f"  [{i + 1:2d}/{len(items)}] {name}")
        writer.close()
    finally:
        try:
            clear(link)
        except Exception as e:  # do not mask the original error
            print(f"warning: clear failed: {e}")
        finally:
            vf.close()
            link.close()
            camera.close()

    report, images = analyse(captured["all_off"], captured["all_on"], info)
    report["session"] = writer.dir.name
    write_json_atomic(writer.dir / "report.json", report)
    for name, img in images.items():
        write_png(writer.dir / f"{name}.png", img)
    print(format_report(report))
    print(f"session: {writer.dir}")
    return writer.dir


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
