"""Reliability run: show many known pictures, read each with the camera, count every error (VSTAGE_4).

.\\.venv\\Scripts\\python.exe -m scripts.soak [--frames 200] [--seed 1] [--settle-ms N] [--intensity-sweep]
                                             [--label TEXT] [--out DIR]

Uses the saved calibration as is (never recalibrates). Runs check_position before and after.
A frame is bad if it has a wrong cell while the status is `ok` (a confident error); flagged if the
status is unreliable / uncalibrated / camera_error, or dark while lit cells were shown. Dark on an
all-off picture is correct and counted as dark_frames. Exit 0 only if there are no wrong cells and no
bad frames. The report goes to <out>/soak_<timestamp>.json, debug images to <out>/soak_<timestamp>/.
"""

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

import config
from scripts.common import clear, make_show, open_all, set_intensity, wake
from scripts.read import save_debug
from vision import patterns as P
from vision.calibration import load_calibration
from vision.camera import CameraError
from vision.ledmap import compare
from vision.reader import GridReader
from vision.viewfinder import Viewfinder

N = 8
DENSITIES = (0.1, 0.25, 0.5, 0.75, 0.9)
TRANSITIONS = ("all_on", "all_off", "all_on", "checker_0", "checker_1")  # stale frames, exposure swing
SWEEP_LEVELS = (0x00, 0x08, 0x0F)
SWEEP_FRAMES = 20
FLAGGED = ("unreliable", "uncalibrated", "camera_error")


def _lit(rows) -> bool:
    return any("1" in r for r in rows)


def random_frames(rng: random.Random, n: int, start: int = 0) -> list[tuple[str, list[str]]]:
    """n random pictures, density cycling through DENSITIES."""
    out = []
    for i in range(start, start + n):
        d = DENSITIES[i % len(DENSITIES)]
        out.append((f"random_{i}_d{d}", P.random_frame(rng, d)))
    return out


def frame_list(seed: int, n: int) -> tuple[list[tuple[str, list[str]]], random.Random]:
    """The 24 standard pictures, the transitions, then random pictures, cut to exactly n.
    Returns the rng too, so the intensity sweep continues the same sequence."""
    std = dict(P.standard_set())
    items = P.standard_set() + [(f"t_{name}", std[name]) for name in TRANSITIONS]
    rng = random.Random(seed)
    if n > len(items):
        items += random_frames(rng, n - len(items))
    return items[:n], rng


class Tally:
    """Counts for one set of reads."""

    def __init__(self) -> None:
        self.frames = self.wrong_cells = self.wrong_cells_ok = self.bad_frames = 0
        self.uncertain_cells = self.flagged_frames = self.dark_frames = 0
        self.warnings = Counter()
        self.per_cell_wrong = np.zeros((N, N), int)
        self.per_cell_uncertain = np.zeros((N, N), int)
        self.read_ms: list[int] = []

    def add(self, expected: list[str], led_map: dict) -> str | None:
        """Score one read. Returns 'bad', 'flagged', 'dark' (correct dark) or None (correct ok)."""
        status = led_map["vision"]["status"]
        rows = led_map["rows"]
        wrong = compare(expected, rows)
        self.frames += 1
        self.wrong_cells += len(wrong)
        for d in wrong:
            self.per_cell_wrong[d["row"], d["col"]] += 1
        for r in range(N):
            for c in range(N):
                if rows[r][c] == "?":
                    self.per_cell_uncertain[r, c] += 1
        self.uncertain_cells += led_map["vision"]["uncertain"]
        self.warnings.update(led_map["warnings"])
        self.read_ms.append(led_map["vision"]["read_ms"])
        if status == "ok" and wrong:
            self.bad_frames += 1
            self.wrong_cells_ok += len(wrong)
            return "bad"
        if status in FLAGGED or (status == "dark" and _lit(expected)):
            self.flagged_frames += 1
            return "flagged"
        if status == "dark":
            self.dark_frames += 1
            return "dark"
        return None

    def summary(self) -> dict:
        ms = np.asarray(self.read_ms or [0])
        return {"frames": self.frames, "cells": self.frames * N * N,
                "wrong_cells": self.wrong_cells, "wrong_cells_ok": self.wrong_cells_ok,
                "bad_frames": self.bad_frames, "uncertain_cells": self.uncertain_cells,
                "flagged_frames": self.flagged_frames, "dark_frames": self.dark_frames,
                "warnings": dict(self.warnings),
                "per_cell_wrong": self.per_cell_wrong.tolist(),
                "per_cell_uncertain": self.per_cell_uncertain.tolist(),
                "read_ms": {"min": int(ms.min()), "median": int(np.median(ms)),
                            "p95": int(np.percentile(ms, 95)), "max": int(ms.max())}}


def _position(pos) -> dict:
    return {"ok": pos.ok, "max_corner_shift_px": pos.max_corner_shift_px, "detail": pos.detail}


def run_soak(link, camera, cal, *, frames=200, seed=1, settle_ms=None, intensity_sweep=False, label="",
             debug_dir=None, viewfinder=None) -> dict:
    """The whole run on an open link and camera. Returns the report; `aborted` is set if the
    position check at the start fails (nothing is read then)."""
    settle_ms = config.VISION_SETTLE_MS if settle_ms is None else settle_ms
    reader = GridReader(camera, cal, viewfinder=viewfinder)
    show = make_show(link, camera)
    report = {"label": label, "seed": seed, "settle_ms": settle_ms, "calibration_created": cal.created,
              "calibration_camera": cal.camera, "camera": camera.info(),
              "config": {k: getattr(config, k) for k in (
                  "VISION_FLUSH_FRAMES", "VISION_AVG_FRAMES", "VISION_REDUCE", "VISION_UNCERTAIN_BAND",
                  "VISION_SAMPLE_RADIUS_FRAC", "VISION_MAX_UNCERTAIN", "VISION_MOVE_TOLERANCE_FRAC",
                  "VISION_LEVEL_TOLERANCE", "VISION_SCENE_TOLERANCE", "VISION_SCENE_MIN_DELTA")},
              "channel": cal.channel, "reduce": cal.reduce, "sample_radius_px": cal.sample_radius_px,
              "debug_dir": None if debug_dir is None else str(debug_dir), "debug_images": []}

    start = reader.check_position(show, settle_ms=settle_ms)
    report["position_start"] = _position(start)
    report["position_start_px"] = start.max_corner_shift_px
    if not start.ok:
        report["aborted"] = "position check failed at the start"
        return report

    def one(tally, i, total, name, rows, tag):
        show(rows)
        m = reader.read(settle_ms=settle_ms)
        verdict = tally.add(rows, m)
        status = m["vision"]["status"]
        if verdict in ("bad", "flagged") and debug_dir is not None:
            path = Path(debug_dir) / f"{tag}frame_{i:03d}_{verdict}_{status}_{name}.png"
            saved = save_debug(reader, cal, rows, m, path=path)
            if saved:
                report["debug_images"].append(Path(saved).name)
        if viewfinder is not None and reader.last_frame is not None:
            viewfinder.update(reader.last_frame, f"soak {tag}{i + 1}/{total}  wrong {tally.wrong_cells}  "
                              f"bad {tally.bad_frames}  flagged {tally.flagged_frames}",
                              rows=m["rows"], status=status)

    items, rng = frame_list(seed, frames)
    main = Tally()
    for i, (name, rows) in enumerate(items):
        one(main, i, len(items), name, rows, "")
    report.update(main.summary())

    if intensity_sweep:
        sweep = {}
        try:
            for level in SWEEP_LEVELS:
                set_intensity(link, level)
                tally = Tally()
                for i, (name, rows) in enumerate(random_frames(rng, SWEEP_FRAMES)):
                    one(tally, i, SWEEP_FRAMES, name, rows, f"i{level:02X}_")
                sweep[f"{level:02X}"] = tally.summary()
        finally:
            set_intensity(link, config.DEFAULT_INTENSITY)
        report["intensity_sweep"] = sweep

    end = reader.check_position(show, settle_ms=settle_ms)
    report["position_end"] = _position(end)
    report["position_end_px"] = end.max_corner_shift_px
    return report


def format_summary(r: dict) -> str:
    lines = [f"Soak '{r['label']}'  seed {r['seed']}  settle {r['settle_ms']} ms"]
    ps = r.get("position_start", {})
    lines.append(f"  position start   {'OK' if ps.get('ok') else 'FAILED'}  max corner shift "
                 f"{r.get('position_start_px')} px  {json.dumps(ps.get('detail', {}))}")
    if r.get("aborted"):
        lines.append(f"  ABORTED: {r['aborted']}. Recalibrate, or run scripts.read --check-position to see why")
        return "\n".join(lines)
    ms = r["read_ms"]
    lines += [
        f"  frames           {r['frames']} ({r['cells']} cells)",
        f"  wrong cells      {r['wrong_cells']}   (in ok frames: {r['wrong_cells_ok']})",
        f"  bad frames       {r['bad_frames']}   (wrong cell with status ok)",
        f"  uncertain cells  {r['uncertain_cells']}",
        f"  flagged frames   {r['flagged_frames']}",
        f"  dark frames      {r['dark_frames']}   (all-off pictures read dark: correct)",
        f"  warnings         {json.dumps(r['warnings'])}",
        f"  read_ms          min {ms['min']}  median {ms['median']}  p95 {ms['p95']}  max {ms['max']}",
    ]
    for key in ("per_cell_wrong", "per_cell_uncertain"):
        if any(any(row) for row in r[key]):
            lines.append(f"  {key}:")
            lines += [f"    {' '.join(f'{v:3d}' for v in row)}" for row in r[key]]
    for level, s in (r.get("intensity_sweep") or {}).items():
        lines.append(f"  intensity {level}: {s['frames']} frames, wrong {s['wrong_cells']}, bad {s['bad_frames']}, "
                     f"uncertain {s['uncertain_cells']}, flagged {s['flagged_frames']}, warnings {json.dumps(s['warnings'])}")
    pe = r.get("position_end", {})
    lines.append(f"  position end     {'OK' if pe.get('ok') else 'FAILED'}  max corner shift "
                 f"{r.get('position_end_px')} px  {json.dumps(pe.get('detail', {}))}")
    if r["debug_images"]:
        lines.append(f"  debug images     {len(r['debug_images'])} in {r['debug_dir']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--frames", type=int, default=200)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--settle-ms", type=int, default=config.VISION_SETTLE_MS)
    p.add_argument("--intensity-sweep", action="store_true")
    p.add_argument("--label", default="")
    p.add_argument("--out", default=str(config.BASE_DIR / "logs" / "vision"), help="folder for the report")
    args = p.parse_args(argv)

    cal = load_calibration(config.CALIBRATION_FILE)
    if cal is None:
        print(f"no calibration at {config.CALIBRATION_FILE}. Run: .\\.venv\\Scripts\\python.exe -m scripts.calibrate",
              file=sys.stderr)
        return 1
    print(f"serial port: {config.SERIAL_PORT}   camera source: {config.CAMERA_SOURCE}   "
          f"calibration: {config.CALIBRATION_FILE}")
    try:
        link, camera = open_all()
    except CameraError as e:
        print(f"camera_error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"device_error: {e}", file=sys.stderr)
        return 1

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = Path(args.out)
    vf = Viewfinder("LED grid soak", camera=camera)
    try:
        wake(link)
        report = run_soak(link, camera, cal, frames=args.frames, seed=args.seed, settle_ms=args.settle_ms,
                          intensity_sweep=args.intensity_sweep, label=args.label,
                          debug_dir=out / f"soak_{stamp}", viewfinder=vf)
    except Exception as e:
        print(f"device_error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            clear(link)
        except Exception as e:
            print(f"warning: clear failed: {e}", file=sys.stderr)
        finally:
            vf.close()
            link.close()
            camera.close()

    print(format_summary(report))
    if report.get("aborted"):
        return 1
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"soak_{stamp}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"  report           {path}")
    return 0 if report["wrong_cells"] == 0 and report["bad_frames"] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
