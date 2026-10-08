"""One live read: show a picture (or keep what is there), read it with the camera, print the LED map.

.\\.venv\\Scripts\\python.exe -m scripts.read [--pattern NAME | --hex HEX | --keep] [--repeat N]
                                             [--check-position] [--json] [--settle-ms MS]

--pattern  a name from the standard set (all_on, checker_0, row_3, ...) or random:<seed>:<density>
--hex      a raw row frame, sent as is; expected rows come from hex_to_rows
--keep     send nothing; read what is on the grid (no expected rows)
Default: --pattern checker_0.
"""

import argparse
import json
import random
import sys
import time

import config
from scripts.common import clear, make_show, open_all, send_hex, wake
from vision import patterns as P
from vision.calibration import draw_overlay, load_calibration
from vision.camera import CameraError
from vision.ledmap import compare
from vision.reader import GridReader
from vision.session import write_png
from vision.viewfinder import Viewfinder


def parse_pattern(name: str) -> list[str]:
    if name.startswith("random:"):
        try:
            _, seed, density = name.split(":")
            return P.random_frame(random.Random(int(seed)), float(density))
        except ValueError:
            raise ValueError(f"use random:<seed>:<density>, e.g. random:1:0.5 (got {name!r})") from None
    names = dict(P.standard_set())
    if name not in names:
        raise ValueError(f"unknown pattern {name!r}; use one of {', '.join(names)} or random:<seed>:<density>")
    return names[name]


def side_by_side(expected, observed) -> list[str]:
    lines = ["expected    observed" if expected else "observed"]
    for r in range(8):
        obs = observed[r]
        if expected:
            marked = "".join(o if o == "?" or o == e else "x" for o, e in zip(obs, expected[r]))
            note = "     <- x marks a difference, ? marks uncertain" if marked != expected[r] else ""
            lines.append(f"{expected[r]}    {marked}{note}")
        else:
            lines.append(obs)
    return lines


def save_debug(reader, cal, expected, led_map) -> str | None:
    """Grid region, upscaled, with wrong cells circled red and uncertain cells yellow."""
    if reader.last_frame is None:
        return None
    marks = {}
    if expected:
        marks = {(d["row"], d["col"]): (0, 0, 255) for d in compare(expected, led_map["rows"])}
    marks.update({(r, c): (0, 255, 255) for r in range(8) for c in range(8) if led_map["rows"][r][c] == "?"})
    config.VISION_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.VISION_DEBUG_DIR / f"read_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
    write_png(path, draw_overlay(reader.last_frame, cal, marks))
    return str(path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    what = p.add_mutually_exclusive_group()
    what.add_argument("--pattern", help="standard pattern name or random:<seed>:<density>")
    what.add_argument("--hex", help="raw row frame hex, sent as is")
    what.add_argument("--keep", action="store_true", help="send nothing; read what is there")
    p.add_argument("--repeat", type=int, default=1, help="read N times without re-sending")
    p.add_argument("--check-position", action="store_true", help="run the explicit position check first")
    p.add_argument("--json", action="store_true", help="print the LED map object alone")
    p.add_argument("--settle-ms", type=int, default=config.VISION_SETTLE_MS)
    args = p.parse_args(argv)
    out = sys.stderr if args.json else sys.stdout  # with --json, stdout carries only the objects

    expected = None
    try:
        if args.hex:
            data_hex = args.hex.strip().upper()
            bytes.fromhex(data_hex)
        elif not args.keep:
            expected = parse_pattern(args.pattern or "checker_0")
    except ValueError as e:
        print(f"bad picture: {e}", file=sys.stderr)
        return 2

    cal = load_calibration(config.CALIBRATION_FILE)
    if cal is None:
        print(f"no calibration at {config.CALIBRATION_FILE}. Run: .\\.venv\\Scripts\\python.exe -m scripts.calibrate",
              file=sys.stderr)
        return 1

    print(f"serial port: {config.SERIAL_PORT}   camera source: {config.CAMERA_SOURCE}   "
          f"calibration: {config.CALIBRATION_FILE}", file=out)
    try:
        link, camera = open_all()
    except CameraError as e:
        print(f"camera_error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"device_error: {e}", file=sys.stderr)
        return 1

    vf = Viewfinder("LED grid read", camera=camera)
    reader = GridReader(camera, cal, viewfinder=vf)
    show = make_show(link, camera)
    sent = saved = False
    maps = []
    try:
        if not args.keep:
            wake(link)
        if args.check_position:
            sent = True
            pos = reader.check_position(show, settle_ms=args.settle_ms)
            print(f"position check: {'OK' if pos.ok else 'FAILED'}   max corner shift "
                  f"{pos.max_corner_shift_px} px   {json.dumps(pos.detail)}", file=out)
            if args.keep:
                print("note: the position check replaced the picture on the grid", file=out)
        if args.hex:
            expected = send_hex(link, camera, data_hex)
            sent = True
        elif expected is not None:
            show(expected)
            sent = True
        for _ in range(max(1, args.repeat)):
            maps.append(reader.read(settle_ms=args.settle_ms))
            if args.json:
                print(json.dumps(maps[-1]))
            bad = maps[-1]["vision"]["status"] != "ok" or (expected and maps[-1]["rows"] != expected)
            if bad and not saved:  # one image per run: the first bad read
                path = save_debug(reader, cal, expected, maps[-1])
                saved = True
                if path:
                    print(f"debug image: {path}", file=out)
    except Exception as e:
        print(f"device_error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            if sent:
                clear(link)
        except Exception as e:
            print(f"warning: clear failed: {e}", file=sys.stderr)
        finally:
            vf.close()
            link.close()
            camera.close()

    first = maps[0]
    for line in side_by_side(expected, first["rows"]):
        print(line, file=out)
    for i, m in enumerate(maps, 1):
        diffs = len(compare(expected, m["rows"])) if expected else None
        print(f"read {i}: status {m['vision']['status']}   uncertain {m['vision']['uncertain']}   "
              f"warnings {m['warnings']}   read_ms {m['vision']['read_ms']}"
              + (f"   differences {diffs}" if expected else ""), file=out)
    if len(maps) > 1:
        same = sum(m["rows"] == first["rows"] for m in maps)
        print(f"identical to read 1: {same}/{len(maps)}", file=out)
        if expected:
            print(f"equal to expected:   {sum(m['rows'] == expected for m in maps)}/{len(maps)}", file=out)
    ms = sorted(m["vision"]["read_ms"] for m in maps)
    print(f"read_ms median {ms[len(ms) // 2]}   max {ms[-1]}", file=out)
    mv = reader.last_checks.get("movement")
    if mv:
        print(f"movement: lit {mv['lit_cells']}, median offset {mv['median_offset_frac']} "
              f"(limit {config.VISION_MOVE_TOLERANCE_FRAC}), guard max {mv['guard_max']} "
              f"(limit {mv['guard_limit']})", file=out)
    ok = all(m["vision"]["status"] == "ok" for m in maps) and (not expected or all(m["rows"] == expected for m in maps))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
