"""Offline calibration and reading on a saved capture session. Never touches the board or the camera.

.\\.venv\\Scripts\\python.exe -m scripts.evaluate [session_dir] [--cal FILE] [--shift A.json B.json]
                                                 [--single] [--verbose]

Default: calibrate on the session (debug images go to <session>/eval/), then read every item through
GridReader, with all frames combined and then with each single raw frame alone.
--cal FILE        read with an existing calibration file instead of calibrating on the session.
--shift A B       movement replay: move the calibration by the image displacement between the
                  calibration files A and B, i.e. read as if A's calibration were applied to frames
                  taken where B was. With no --cal, the session's own offline calibration is moved.
CALIBRATION_FILE is never written.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

import config
from vision.calibration import calibrate, format_summary, load_calibration
from vision.camera import ReplayCamera
from vision.ledmap import compare
from vision.reader import GridReader, shifted_calibration
from vision.session import latest_session


class _SingleFrame:
    """ReplayCamera view that serves only frame `k` of the selected item."""

    def __init__(self, base: ReplayCamera, k: int) -> None:
        self.base, self.k = base, k

    def select(self, rows) -> None:
        self.base.select(rows)

    def flush(self, n) -> None:
        pass

    def grab(self, n):
        paths = self.base._item["paths"]
        self.base._pos = self.k % len(paths)
        return self.base.grab(1) * n

    def info(self) -> dict:
        return self.base.info()


def _read_items(camera, cal, items):
    """Read every item; per item (name, map, wrong, uncertain, checks)."""
    reader = GridReader(camera, cal)
    out = []
    for item in items:
        camera.select(item["rows"])
        m = reader.read(settle_ms=0)
        wrong = len(compare(item["rows"], m["rows"]))
        out.append((item["name"], m, wrong, m["vision"]["uncertain"], {**reader.last_checks, "expected": item["rows"]}))
    return out


def _totals(results) -> dict:
    dark = [r for r in results if r[1]["vision"]["status"] == "dark"]
    dark_lit = sum(any("1" in row for row in r[4].get("expected", [])) for r in dark)
    return {"reads": len(results), "wrong": sum(r[2] for r in results), "uncertain": sum(r[3] for r in results),
            # `dark` on an all-off item is correct; `dark` on an item with lit cells is a flagged read
            "not_ok": sum(r[1]["vision"]["status"] not in ("ok", "dark") for r in results) + dark_lit,
            "dark": len(dark), "dark_lit": dark_lit,
            "confident_wrong": sum(r[2] > 0 and r[1]["vision"]["status"] == "ok" for r in results),
            # of those, reads that still claim lit cells (display on); the rest read all dark (display unknown)
            "confident_wrong_lit": sum(r[2] > 0 and r[1]["vision"]["status"] == "ok" and r[1]["display"] == "on"
                                       for r in results),
            "grid_moved": sum("grid_moved" in r[1]["warnings"] for r in results),
            "lighting_changed": sum("lighting_changed" in r[1]["warnings"] for r in results)}


def _fmt_totals(t: dict) -> str:
    return (f"{t['reads']} reads, {t['wrong']} wrong cells, {t['uncertain']} uncertain cells, "
            f"{t['not_ok']} not ok ({t['grid_moved']} grid_moved, {t['lighting_changed']} lighting_changed, "
            f"{t['dark_lit']} dark with lit cells expected), {t['dark'] - t['dark_lit']} dark on all-off, "
            f"{t['confident_wrong']} ok-but-wrong ({t['confident_wrong_lit']} with lit cells, "
            f"{t['confident_wrong'] - t['confident_wrong_lit']} all dark)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("session_dir", nargs="?", help="capture session folder (default: latest)")
    p.add_argument("--cal", help="calibration file to read with, instead of calibrating on the session")
    p.add_argument("--shift", nargs=2, metavar=("A", "B"), help="move the calibration by B -> A displacement")
    p.add_argument("--single", action="store_true", help="also read each single raw frame alone (default on "
                   "when calibrating on the session)")
    p.add_argument("--verbose", action="store_true", help="print the movement and level numbers per item")
    args = p.parse_args(argv)

    session = Path(args.session_dir) if args.session_dir else latest_session(config.VISION_CAPTURE_DIR)
    if session is None or not (session / "manifest.json").is_file():
        print(f"no capture session found ({args.session_dir or config.VISION_CAPTURE_DIR})")
        return 1
    camera = ReplayCamera(session)
    print(f"session: {session}")

    if args.cal:
        cal = load_calibration(args.cal)
        if cal is None:
            print(f"cannot load calibration {args.cal}")
            return 1
        print(f"calibration: {args.cal} (not fitted on this session)")
    else:
        debug_dir = session / "eval"
        result = calibrate(camera, lambda rows: None, intensity=camera.manifest.get("intensity"),
                           settle_ms=0, debug_dir=None if args.shift else debug_dir)
        print(format_summary(result))
        if not args.shift:
            print(f"debug images: {debug_dir}")
        if not result.ok:
            return 1
        cal = result.calibration

    if args.shift:
        a, b = (load_calibration(f) for f in args.shift)
        if a is None or b is None:
            print(f"cannot load {args.shift}")
            return 1
        m = np.asarray(a.homography) @ np.linalg.inv(np.asarray(b.homography))
        moves = [np.hypot(*(np.asarray(a.corners_px[k]) - np.asarray(b.corners_px[k]))) for k in a.corners_px]
        print(f"shift: calibration of {Path(args.shift[0]).name} applied to frames at "
              f"{Path(args.shift[1]).name}; corner displacement {min(moves):.2f}-{max(moves):.2f} px")
        cal = shifted_calibration(cal, m)

    items = camera.manifest["items"]
    results = _read_items(camera, cal, items)
    print("\nreads, all frames combined:")
    print(f"  {'pattern':12s} {'status':10s} wrong unsure  lit  offset  guard/limit  off_q1/on_med  warnings")
    for name, mp, wrong, unsure, ck in results:
        mv, lv = ck.get("movement", {}), ck.get("levels", {})
        off = mv.get("median_offset_frac")
        print(f"  {name:12s} {mp['vision']['status']:10s} {wrong:5d} {unsure:6d}  {mv.get('lit_cells', '-'):>3}  "
              f"{'-' if off is None else f'{off:.3f}':>6}  {mv.get('guard_max', '-')!s:>5}/{mv.get('guard_limit', '-')!s:<5}  "
              f"{lv.get('off_low', '-')!s:>6}/{lv.get('on_mid', '-')!s:<6}  {','.join(mp['warnings'])}")
        if args.verbose:
            print(f"      {ck}")
    print(f"  TOTAL combined: {_fmt_totals(_totals(results))}")
    ms = [r[1]["vision"]["read_ms"] for r in results]
    print(f"  read_ms (offline, settle 0): median {int(np.median(ms))}, max {max(ms)}")

    if args.single or not (args.cal or args.shift):
        n_frames = min(len(it["frames"]) for it in items)
        single = []
        for k in range(n_frames):
            single += _read_items(_SingleFrame(camera, k), cal, items)
        print(f"\nreads, each single raw frame alone ({n_frames} frames x {len(items)} items):")
        print(f"  TOTAL single: {_fmt_totals(_totals(single))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
