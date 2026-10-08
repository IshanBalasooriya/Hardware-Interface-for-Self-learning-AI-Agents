"""Audit helpers for the stage 4 guided run: parse script output, pre-check the exit gate, render the
appendix of the run log. Pure functions (plus read-only git and file listing for the environment).

The gate pre-check is automatic and NOT the verdict: Claude Code checks the images and decides."""

import ast
import json
import math
import platform
import re
import subprocess
from pathlib import Path

import config

READ_RE = re.compile(r"^read (\d+): status (\S+)\s+uncertain (\d+)\s+warnings (\[.*?\])\s+read_ms (\d+)"
                     r"(?:\s+differences (\d+))?")
SHIFT_RE = re.compile(r"max corner shift (\S+) px")
REASON_RE = re.compile(r'"reason": "([^"]+)"')
OK_STATUSES_G7B = ("dark", "unreliable")
PRECHECK_NOTE = "Automatic pre-check from the run output, NOT the verdict. Claude Code checks the images and decides."


# ---------------------------------------------------------------- parsing

def parse_reads(output: str) -> list[dict]:
    reads = []
    for line in output.splitlines():
        m = READ_RE.match(line.strip())
        if m:
            try:
                warnings = list(ast.literal_eval(m.group(4)))
            except (ValueError, SyntaxError):
                warnings = [m.group(4)]
            reads.append({"n": int(m.group(1)), "status": m.group(2), "uncertain": int(m.group(3)),
                          "warnings": warnings, "read_ms": int(m.group(5)),
                          "differences": None if m.group(6) is None else int(m.group(6))})
    return reads


def parse_position(output: str, prefix: str = "position check:") -> dict | None:
    """{ok, shift_px, reason} from the first line starting with `prefix`, or None."""
    for line in output.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            rest = s[len(prefix):].split()
            m = SHIFT_RE.search(s)
            shift = None
            if m and m.group(1) != "None":
                try:
                    shift = float(m.group(1))
                except ValueError:
                    pass
            r = REASON_RE.search(s)
            return {"ok": bool(rest) and rest[0] == "OK", "shift_px": shift, "reason": r.group(1) if r else None}
    return None


def report_path(output: str) -> str | None:
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("report ") and s.endswith(".json"):
            return s[len("report"):].strip()
    return None


def debug_images(output: str) -> list[str]:
    return [line.strip()[len("debug image:"):].strip() for line in output.splitlines()
            if line.strip().startswith("debug image:")]


def calibration_ok(run) -> bool:
    return run.code == 0 and any(ln.strip() == "Calibration OK" for ln in run.output.splitlines())


def grid_not_found(output: str) -> bool:
    pos = parse_position(output)
    return bool(pos and pos["reason"] == "grid_not_found")


# ---------------------------------------------------------------- gate pre-check

def _step(results, n):
    for sr in results:
        if sr.step.n == n:
            return sr
    return None


def _runs(results, n, kind=None):
    sr = _step(results, n)
    return [] if sr is None else [r for r in sr.runs if kind is None or r.kind == kind]


def _confident_wrong(reads) -> bool:
    return any(r["status"] == "ok" and (r["differences"] or 0) > 0 for r in reads)


def _clean(reads) -> bool:
    return bool(reads) and all(r["status"] == "ok" and r["differences"] == 0 for r in reads)


def _fmt_reads(reads) -> str:
    if not reads:
        return "no reads"
    out = []
    for r in reads:
        w = f" {','.join(r['warnings'])}" if r["warnings"] else ""
        out.append(f"{r['status']}/{r['differences']}{w}")
    return " ".join(out)


def min_pitch(cal: dict | None) -> float | None:
    try:
        return float(cal["geometry"]["min_pitch_px"])
    except (TypeError, KeyError, ValueError):
        return None


def gate_precheck(results, soaks: dict, cal_before: dict | None, answers: dict | None = None) -> list[dict]:
    """One row per requirement: id, requirement, found, auto (pass | fail | needs review | not run).
    `soaks`: label -> soak report dict (or None if the JSON could not be read). `cal_before`: the
    calibration used for the soaks (step 3's copy)."""
    answers = answers or {}
    rows = []

    def row(gid, req, found, auto):
        rows.append({"id": gid, "requirement": req, "found": found, "auto": auto})

    # G1
    cals = [_runs(results, n, "calibrate") for n in (1, 2, 3)]
    if not all(cals):
        row("G1", "3 calibrations in a row", "steps 1-3 not all run", "not run")
    else:
        firsts = [calibration_ok(c[0]) for c in cals]
        found = ", ".join(f"step {n}: {'OK' if f else 'FAILED'}" + (f" ({len(c)} attempts)" if len(c) > 1 else "")
                          for n, f, c in zip((1, 2, 3), firsts, cals))
        row("G1", "3 calibrations in a row", found, "pass" if all(firsts) else "fail")

    # G2, G3
    pair = [soaks.get(k) for k in ("main", "seed2")]
    if not all(k in soaks for k in ("main", "seed2")):
        row("G2", "main + seed2: 0 wrong cells", "soak missing", "not run")
        row("G3", "uncertain <= 26, flagged <= 4", "soak missing", "not run")
    elif any(p is None for p in pair):
        row("G2", "main + seed2: 0 wrong cells", "soak report JSON missing", "needs review")
        row("G3", "uncertain <= 26, flagged <= 4", "soak report JSON missing", "needs review")
    else:
        wrong = sum(p["wrong_cells"] for p in pair)
        frames = sum(p["frames"] for p in pair)
        row("G2", "main + seed2: 0 wrong cells", f"{wrong} wrong in {frames} frames ({frames * 64} cells)",
            "pass" if wrong == 0 and frames >= 400 else "fail")
        unc = sum(p["uncertain_cells"] for p in pair)
        flg = sum(p["flagged_frames"] for p in pair)
        row("G3", "uncertain <= 26, flagged <= 4", f"uncertain {unc}, flagged {flg}",
            "pass" if unc <= 26 and flg <= 4 else "fail")

    # G4
    a = soaks.get("after10min")
    pitch = min_pitch(cal_before)
    if "after10min" not in soaks:
        row("G4", "after10min: 0 wrong, drift < 0.2 pitch", "soak missing", "not run")
    elif a is None or pitch is None:
        row("G4", "after10min: 0 wrong, drift < 0.2 pitch", "report or calibration copy missing", "needs review")
    else:
        end = a.get("position_end_px")
        frac = None if end is None else end / pitch
        found = (f"wrong {a['wrong_cells']}, end shift {end} px / min pitch {pitch:.2f} = "
                 f"{'n/a' if frac is None else f'{frac:.3f}'} pitch")
        row("G4", "after10min: 0 wrong, drift < 0.2 pitch", found,
            "pass" if a["wrong_cells"] == 0 and frac is not None and frac < 0.2 else "fail")

    # G5
    timed = {k: soaks[k] for k in ("main", "seed2", "after10min") if soaks.get(k)}
    if not timed:
        row("G5", "read median <= 600 ms, p95 <= 1200 ms", "no soak reports", "not run")
    else:
        med = max(r["read_ms"]["median"] for r in timed.values())
        p95 = max(r["read_ms"]["p95"] for r in timed.values())
        row("G5", "read median <= 600 ms, p95 <= 1200 ms",
            f"highest per-run median {med}, highest per-run p95 {p95} ({', '.join(timed)}; conservative)",
            "pass" if med <= 600 and p95 <= 1200 else "fail")

    # G6
    reads9 = [r for run in _runs(results, 9) for r in parse_reads(run.output)]
    if not _step(results, 9):
        row("G6", "card: differences only under the card", "step 9 not run", "not run")
    else:
        row("G6", "card: differences only under the card",
            f"covered: {answers.get('half', '?')}; reads (status/differences): {_fmt_reads(reads9)}", "needs review")

    # G7
    if not all(_step(results, n) for n in (10, 11, 12)):
        row("G7", "faults c, d, e: never ok with wrong cells; c dark", "steps 10-12 not all run", "not run")
    else:
        r10, r11, r12 = ([r for run in _runs(results, n) for r in parse_reads(run.output)] for n in (10, 11, 12))
        found = f"c: {_fmt_reads(r10)} | d: {_fmt_reads(r11)} | e: {_fmt_reads(r12)}"
        if any(_confident_wrong(x) for x in (r10, r11, r12)):
            auto = "fail"
        elif r10 and all(r["status"] == "dark" for r in r10):
            auto = "pass"
        else:
            auto = "needs review"
        row("G7", "faults c, d, e: never ok with wrong cells; c dark", found, auto)

    # G7b
    runs16 = _runs(results, 16)
    if not runs16:
        row("G7b", "grid moved far: dark or unreliable, never ok", "step 16 not run", "not run")
    else:
        hit = [r for r in runs16 if grid_not_found(r.output)]
        if not hit:
            row("G7b", "grid moved far: dark or unreliable, never ok",
                f"grid_not_found not reached in {len(runs16)} attempts; last: {_fmt_reads(parse_reads(runs16[-1].output))}",
                "needs review")
        else:
            reads = parse_reads(hit[0].output)
            good = bool(reads) and all(r["status"] in OK_STATUSES_G7B for r in reads)
            row("G7b", "grid moved far: dark or unreliable, never ok",
                f"grid_not_found on attempt {runs16.index(hit[0]) + 1}; reads {_fmt_reads(reads)}",
                "pass" if good else "fail")

    # G8
    if not (_step(results, 13) and _step(results, 14)):
        row("G8", "recovery correct; table tap OK or grid_moved", "steps 13-14 not all run", "not run")
    else:
        r13 = [r for run in _runs(results, 13) for r in parse_reads(run.output)]
        out14 = "\n".join(run.output for run in _runs(results, 14))
        pos14, r14 = parse_position(out14), parse_reads(out14)
        tap_ok = ((pos14 is not None and pos14["ok"] and _clean(r14))
                  or any("grid_moved" in r["warnings"] for r in r14))
        found = (f"recovery: {_fmt_reads(r13)} | tap: position {'OK' if pos14 and pos14['ok'] else 'FAILED'}"
                 f" {pos14 and pos14['shift_px']} px, read {_fmt_reads(r14)}")
        row("G8", "recovery correct; table tap OK or grid_moved", found,
            "pass" if _clean(r13) and tap_ok else ("fail" if _confident_wrong(r13 + r14) or not _clean(r13) else "needs review"))

    # G9
    if not (_step(results, 15) and _step(results, 17)):
        row("G9", "lid press detected; correct after recalibrating", "steps 15/17 not all run", "not run")
    else:
        out15 = "\n".join(run.output for run in _runs(results, 15))
        pos15, r15 = parse_position(out15), parse_reads(out15)
        detected = (pos15 is not None and not pos15["ok"]) or any("grid_moved" in r["warnings"] for r in r15)
        cal17 = _runs(results, 17, "calibrate")
        read17 = _runs(results, 17, "read")
        r17 = parse_reads(read17[-1].output) if read17 else []
        recal = bool(cal17) and calibration_ok(cal17[-1]) and _clean(r17)
        found = (f"press: position {'OK' if pos15 and pos15['ok'] else 'FAILED'} {pos15 and pos15['shift_px']} px, "
                 f"read {_fmt_reads(r15)} | after recalibration: "
                 f"{'Calibration OK' if cal17 and calibration_ok(cal17[-1]) else 'calibration FAILED'}, read {_fmt_reads(r17)}")
        if detected and recal:
            auto = "pass"
        elif _confident_wrong(r15) or not recal:
            auto = "fail"
        else:
            auto = "needs review"  # nothing detected but the read was correct: did the press move the grid?
        row("G9", "lid press detected; correct after recalibrating", found, auto)
    return rows


def precheck_table(rows) -> str:
    out = [f"_{PRECHECK_NOTE}_", "", "| Gate | Requirement | Found | Auto |", "|---|---|---|---|"]
    out += [f"| {r['id']} | {r['requirement']} | {r['found'].replace('|', '/')} | **{r['auto']}** |" for r in rows]
    return "\n".join(out)


def precheck_text(rows) -> str:
    """Plain lines for the terminal."""
    return "\n".join([PRECHECK_NOTE] + [f"  {r['id']:4s} {r['auto']:13s} {r['found']}" for r in rows])


# ---------------------------------------------------------------- appendix

def environment() -> str:
    def git(*args):
        try:
            p = subprocess.run(["git", "-C", str(config.BASE_DIR), *args], capture_output=True, text=True, timeout=10)
            return p.stdout.strip() or p.stderr.strip()
        except Exception as e:
            return f"(git unavailable: {e})"

    consts = {k: getattr(config, k) for k in sorted(dir(config))
              if k.startswith(("VISION_", "CAMERA_")) or k in ("DEFAULT_INTENSITY", "SERIAL_PORT", "CALIBRATION_FILE")}
    lines = ["### Environment", "",
             f"- git HEAD: `{git('rev-parse', 'HEAD')}`",
             f"- Python {platform.python_version()} on {platform.platform()}",
             "- uncommitted changes in this folder:", "", "```text", git("status", "--short", ".") or "(none)", "```",
             "", "- config (runner process; children get the same file, with the env vars removed):", "", "```text"]
    lines += [f"{k} = {v}" for k, v in consts.items()]
    lines.append("```")
    return "\n".join(lines)


def _grid(name, grid) -> list[str]:
    if not grid or not any(any(row) for row in grid):
        return [f"- {name}: all zero"]
    return [f"- {name}:", "", "```text"] + [" ".join(f"{v:3d}" for v in row) for row in grid] + ["```", ""]


def soak_section(label, step_n, report, filename) -> str:
    if report is None:
        return f"#### Step {step_n} soak `{label}`: report JSON not available"
    ms = report["read_ms"]
    lines = [f"#### Step {step_n} soak `{label}` — `{filename}`", "",
             "| frames | wrong | wrong in ok | bad | uncertain | flagged | dark | read_ms min/med/p95/max | position start / end px | warnings |",
             "|---|---|---|---|---|---|---|---|---|---|",
             f"| {report['frames']} | {report['wrong_cells']} | {report.get('wrong_cells_ok', '-')} | {report['bad_frames']} "
             f"| {report['uncertain_cells']} | {report['flagged_frames']} | {report.get('dark_frames', '-')} "
             f"| {ms['min']}/{ms['median']}/{ms['p95']}/{ms['max']} "
             f"| {report.get('position_start_px')} / {report.get('position_end_px')} | {json.dumps(report['warnings'])} |", ""]
    lines += _grid("per_cell_wrong", report.get("per_cell_wrong"))
    lines += _grid("per_cell_uncertain", report.get("per_cell_uncertain"))
    sweep = report.get("intensity_sweep") or {}
    if sweep:
        lines += ["", "| intensity | frames | wrong | bad | uncertain | flagged | warnings |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {lv} | {s['frames']} | {s['wrong_cells']} | {s['bad_frames']} | {s['uncertain_cells']} "
                  f"| {s['flagged_frames']} | {json.dumps(s['warnings'])} |" for lv, s in sweep.items()]
    imgs = report.get("debug_images") or []
    lines += ["", f"- debug images: {len(imgs)}" + (f" ({', '.join(imgs[:20])}{' ...' if len(imgs) > 20 else ''})" if imgs else ""),
              f"- seed {report.get('seed')}, settle {report.get('settle_ms')} ms, calibration created {report.get('calibration_created')}",
              f"- config used: `{json.dumps(report.get('config', {}))}`"]
    return "\n".join(lines)


def cal_section(cals: dict) -> str:
    """cals: tag -> (filename, dict or None), in run order."""
    lines = ["### Calibrations", "",
             "| copy | created | min pitch | orientation | separation min / median | verification | corners px (tl tr bl br) |",
             "|---|---|---|---|---|---|---|"]
    for tag, (fname, c) in cals.items():
        if c is None:
            lines.append(f"| {fname} | not readable | | | | | |")
            continue
        g, v = c.get("geometry", {}), c.get("verification", {})
        gaps = [on - off for ron, roff in zip(c.get("on_level", []), c.get("off_level", [])) for on, off in zip(ron, roff)]
        sep = f"{min(gaps):.1f} / {sorted(gaps)[len(gaps) // 2]:.1f}" if gaps else "-"
        corners = " ".join(f"({p[0]:.1f},{p[1]:.1f})" for p in (c.get("corners_px", {}).get(k, [0, 0]) for k in ("tl", "tr", "bl", "br")))
        created = c.get("created")
        lines.append(f"| {fname} | {created} | {g.get('min_pitch_px')} | {g.get('orientation')} mirrored {g.get('mirrored')} "
                     f"| {sep} | {v.get('cells_checked')} cells, {v.get('cells_wrong')} wrong, {v.get('cells_uncertain')} uncertain "
                     f"| {corners} |")
    readable = [(t, c) for t, (_, c) in cals.items() if c and all(k in c.get("corners_px", {}) for k in ("tl", "tr", "bl", "br"))]
    if len(readable) >= 2:
        (t0, c0), (t1, c1) = readable[0], readable[-1]
        drift = max(math.dist(c0["corners_px"][k], c1["corners_px"][k]) for k in ("tl", "tr", "bl", "br"))
        lines += ["", f"- corner drift {t0} -> {t1}: max {drift:.2f} px"]
    return "\n".join(lines)


def artifacts_section(folder: Path | None) -> str:
    lines = ["### Artifacts", ""]
    if folder is None or not Path(folder).exists():
        return "\n".join(lines + ["(no artifact folder)"])
    lines.append(f"Folder: `{folder}`")
    lines.append("")
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file())
    lines += [f"- `{p.relative_to(folder)}` ({p.stat().st_size} bytes)" for p in files] or ["(empty)"]
    return "\n".join(lines)


def appendix(rows, soaks_meta, cals, folder, env_text=None) -> str:
    """soaks_meta: label -> (step_n, report or None, filename)."""
    parts = ["## Audit appendix", "", env_text if env_text is not None else environment(), "",
             "### Gate pre-check", "", precheck_table(rows), "", "### Soak reports", ""]
    parts += [soak_section(label, n, rep, fn) + "\n" for label, (n, rep, fn) in soaks_meta.items()] or ["(none)"]
    parts += ["", cal_section(cals), "", artifacts_section(folder)]
    return "\n".join(parts)
