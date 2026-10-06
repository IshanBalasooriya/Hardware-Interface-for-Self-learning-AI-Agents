"""
Stage 1 automated discovery experiment orchestrator, with independent MQTT
observation. Implements
POC/Experiments_NEW/STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md.

Runs the existing, unmodified Architecture A discovery loop
(agent_loop.run_discovery()) N independent times, resetting the discovery
skill between runs, while Architecture B's MQTT telemetry
(Temporaral_Context_Approach/telemetry_service.py) streams independently to
adc_stream.txt for measurement/verification only -- it is never fed back
into the LLM. Produces the CSV/JSON outputs listed in the spec's section 18.

This script does not start any of its prerequisites -- they must already be
running before you invoke it:
  - ESP32 flashed with the MQTT-telemetry-enabled firmware build, connected
    on SERIAL_PORT.
  - Mosquitto broker running (see Temporaral_Context_Approach/README.md).
  - `python telemetry_service.py` running in a separate terminal (inside
    Temporaral_Context_Approach/), actively logging to adc_stream.txt.

Optionally, --use-rolling-window seeds each run's discovery conversation
with Architecture B's rolling-window telemetry (via
Temporaral_Context_Approach/rolling_window_reader.py, the same helper
agent_loop.py's own --use-rolling-window flag uses). This is a deliberate,
user-requested extension beyond STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md's
literal scope -- that document's section 32 invariant #3 states "no MQTT
values enter the LLM context" and lists "implement temporal-context
reasoning" as out of scope (section 3). Every other invariant in the
document still applies. Each condition (with/without seeding) writes to its
own subdirectory under experiments/logs/ and experiments/artifacts/, kept
completely separate so the two datasets can be compared without mixing.

Usage:
    python run_stage1_automated.py --n-runs 1    # required dry run first
    python run_stage1_automated.py --n-runs 10 --condition-label no_context
    python run_stage1_automated.py --n-runs 10 --use-rolling-window --condition-label with_rolling_window
"""

import argparse
import bisect
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_EXPERIMENTS_DIR = Path(__file__).resolve().parent
_POC_DIR = _EXPERIMENTS_DIR.parent
_AGENT_DIR = _POC_DIR / "agent"
_BRIDGE_DIR = _POC_DIR / "bridge"
_TELEMETRY_DIR = _POC_DIR / "Temporaral_Context_Approach"
_SKILLS_DIR = _POC_DIR / "skills"
_LOGS_DIR = _EXPERIMENTS_DIR / "logs"
_ARTIFACTS_DIR = _EXPERIMENTS_DIR / "artifacts"

sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))
sys.path.insert(0, str(_EXPERIMENTS_DIR))
sys.path.insert(0, str(_TELEMETRY_DIR))

from dotenv import load_dotenv
from openai import OpenAI

import agent_loop
import registry
import serial_transport
from rolling_window_reader import format_rolling_window, load_rolling_window

load_dotenv()

# --- Configuration ---------------------------------------------------------

N_RUNS_DEFAULT = 10
N_RUNS_MINIMUM_RECOMMENDED = 5
POST_CONVERGENCE_SECONDS_DEFAULT = 15
PRE_RUN_BASELINE_SECONDS_DEFAULT = 3
INTER_RUN_COOLDOWN_SECONDS_DEFAULT = 3

# discovery_runs.csv / latency.csv are always written here by the existing,
# unmodified discovery_logger.py/latency_logger.py (fixed module-level
# paths) -- main() moves them into the condition-labeled output directory
# once a batch finishes. Every other output file is written directly under
# the condition directory since the orchestrator controls those paths itself.
DISCOVERY_RUNS_CSV = _LOGS_DIR / "discovery_runs.csv"
LATENCY_CSV = _LOGS_DIR / "latency.csv"

ADC_STREAM_PATH = Path(
    os.environ.get("MQTT_LOG_FILE", str(_TELEMETRY_DIR / "logs" / "adc_stream.txt"))
)
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "hardware/telemetry/adc/+")
DEFAULT_ROLLING_WINDOW_FILE = os.environ.get(
    "MQTT_ROLLING_WINDOW_FILE", str(_TELEMETRY_DIR / "logs" / "rolling_window.txt")
)

MQTT_TS_FIELDS = [
    "run_id", "run_index", "condition_label", "timestamp", "elapsed_s", "phase", "source",
    "sensor_reading", "target", "tolerance", "absolute_error", "within_tolerance",
    "t_ms", "seq", "topic", "current_pwm", "nearest_iteration",
]
RUN_SUMMARY_FIELDS = [
    "run_id", "run_index", "condition_label", "status", "iterations_used", "target", "tolerance",
    "initial_sensor_mean", "final_control_reading", "final_absolute_error",
    "converged", "time_to_convergence_s", "post_samples", "post_mean",
    "post_std", "post_min", "post_max", "post_mean_absolute_error",
    "post_within_tolerance_count", "post_within_tolerance_fraction",
    "telemetry_seq_gaps", "rolling_window_readings_seeded",
    "skill_saved", "skill_archive_path", "notes",
]

_FAULT_STATUSES = ("hardware_fault", "llm_runtime_failure")


# --- Small utilities ---------------------------------------------------------

def log(msg):
    print(f"[stage1_auto {datetime.now():%H:%M:%S}] {msg}", flush=True)


def fail(msg):
    log(f"ABORT: {msg}")
    raise SystemExit(1)


def _git_commit():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(_POC_DIR),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


# --- Preflight (spec section 10) --------------------------------------------

def preflight_architecture_a(port):
    if not port:
        fail("SERIAL_PORT is not set (check your .env, e.g. SERIAL_PORT=COM5).")
    log(f"connecting to ESP32 on {port} ...")
    try:
        serial_transport.connect(port)
    except Exception as exc:
        fail(
            f"could not open serial port {port}: {exc}. "
            "If another process (dashboard, pio monitor, an earlier run) still "
            "owns it, see free_com_port.ps1."
        )
    ping = registry.call_tool("ping", {})
    if not ping.get("success"):
        fail(f"ESP32 did not respond to PING: {ping}")
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not os.access(_LOGS_DIR, os.W_OK):
        fail(f"discovery logging path is not writable: {_LOGS_DIR}")
    log("Architecture A preflight OK (serial connected, PING succeeded, logging path writable).")


def _tail_json_lines(path, max_lines=5):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    records = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def preflight_architecture_b():
    log(f"checking MQTT telemetry at {ADC_STREAM_PATH} ...")
    if not ADC_STREAM_PATH.exists():
        fail(
            f"{ADC_STREAM_PATH} does not exist -- is telemetry_service.py running? "
            "(python Temporaral_Context_Approach/telemetry_service.py)"
        )
    size_before = ADC_STREAM_PATH.stat().st_size
    before_tail = _tail_json_lines(ADC_STREAM_PATH)
    before_seq = before_tail[-1].get("seq") if before_tail else None

    time.sleep(3)

    size_after = ADC_STREAM_PATH.stat().st_size
    after_tail = _tail_json_lines(ADC_STREAM_PATH)
    after_seq = after_tail[-1].get("seq") if after_tail else None

    if size_after <= size_before:
        fail(
            "adc_stream.txt did not grow in a 3s window -- telemetry_service.py "
            "does not appear to be actively receiving MQTT telemetry. Confirm the "
            "broker and telemetry_service.py are running, then retry."
        )
    if not after_tail:
        fail("could not parse any telemetry records from adc_stream.txt.")
    if before_seq is not None and after_seq is not None and after_seq <= before_seq:
        fail(f"telemetry 'seq' did not advance ({before_seq} -> {after_seq}).")

    latest_source = after_tail[-1].get("source")
    expected_source = agent_loop.SENSOR_PIN
    if latest_source != expected_source:
        fail(
            f"telemetry source (pin {latest_source}) does not match the discovery "
            f"loop's sensor pin ({expected_source}) -- check firmware/include/"
            "telemetry_config.h's TELEMETRY_ADC_PIN."
        )
    log(
        f"Architecture B preflight OK (file grew {size_after - size_before} bytes, "
        f"seq {before_seq} -> {after_seq}, source pin {latest_source})."
    )


# --- Output-file reset (user-approved: back up, then overwrite fresh) -------

def _backup_and_reset_csv(path: Path, label: str):
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as fh:
        has_rows = len(fh.readlines()) > 1  # header + at least one data row
    if not has_rows:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}_pre_stage1_automated_{stamp}{path.suffix}.bak")
    shutil.copy2(path, backup_path)
    log(f"backed up existing {label} -> {backup_path.name}")
    path.unlink()


# --- Skill reset / archiving (spec sections 16, 17) -------------------------

def _list_skill_files():
    if not _SKILLS_DIR.exists():
        return set()
    return {f for f in os.listdir(_SKILLS_DIR) if f.endswith(".json")}


def _cleanup_leftover_skills():
    """
    Run once at the start of a batch, before the per-run reset-validation in
    run_one() would otherwise hard-abort on run 1. A prior condition's final
    run deliberately keeps its canonical skill in place (see
    _archive_and_reset_skill's keep_canonical), which is correct within that
    batch but would collide with the next condition's batch. That skill is
    already archived under its own condition's artifacts directory, so
    deleting the leftover canonical copy here is not data loss.
    """
    leftover = _list_skill_files()
    if not leftover:
        return
    log(f"clearing leftover canonical skill(s) from a prior batch: {sorted(leftover)}")
    for fname in leftover:
        (_SKILLS_DIR / fname).unlink()


def _archive_and_reset_skill(pre_run_files, run_id, condition_label, keep_canonical):
    after_files = _list_skill_files()
    new_files = sorted(after_files - pre_run_files)
    if not new_files:
        return {"skill_saved": False, "skill_archive_path": ""}
    if len(new_files) > 1:
        log(f"WARN multiple new skill files for {run_id}: {new_files} -- archiving all")
    run_artifact_dir = _ARTIFACTS_DIR / condition_label / run_id
    run_artifact_dir.mkdir(parents=True, exist_ok=True)
    archive_paths = []
    for fname in new_files:
        src = _SKILLS_DIR / fname
        dest = run_artifact_dir / ("saved_skill.json" if len(new_files) == 1 else fname)
        shutil.copy2(src, dest)
        archive_paths.append(str(dest))
        if not keep_canonical:
            src.unlink()
    return {"skill_saved": True, "skill_archive_path": ";".join(archive_paths)}


# --- Per-run lifecycle (spec section 5) -------------------------------------

def run_one(client, model, index, n_runs, timing, condition_label, rolling_window_cfg, retry_of=None):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"run_{condition_label}_{stamp}_{index:03d}" + ("_retry" if retry_of else "")
    log(f"=== [{condition_label}] run {index}/{n_runs}: {run_id} ===" + (f" (retry of {retry_of})" if retry_of else ""))

    pre_run_skills = _list_skill_files()
    if pre_run_skills:
        fail(f"skill reset validation failed before {run_id}: found {sorted(pre_run_skills)}")

    serial_transport.set_experiment_context(run_id, index)

    baseline_start = time.time()
    log(f"baseline phase ({timing['pre_run_baseline_s']}s, no control calls)...")
    time.sleep(timing["pre_run_baseline_s"])
    baseline_end = time.time()

    extra_context = ""
    rolling_window_readings_seeded = 0
    if rolling_window_cfg["enabled"]:
        # Loaded fresh per run, not once for the whole batch: rolling_window.txt
        # is continuously updated by the still-running telemetry_service.py
        # throughout the batch, so each run sees whatever real history has
        # accumulated up to that moment -- the actual point of testing
        # temporal context, not a static snapshot reused across all runs.
        values = load_rolling_window(rolling_window_cfg["file"])
        extra_context = format_rolling_window(values, rolling_window_cfg["max_entries"])
        rolling_window_readings_seeded = len(values)
        if extra_context:
            log(f"seeded {run_id} with {rolling_window_readings_seeded} readings from rolling window")
        else:
            log(f"WARN --use-rolling-window set but no data at {rolling_window_cfg['file']} for {run_id}")
        run_artifact_dir = _ARTIFACTS_DIR / condition_label / run_id
        run_artifact_dir.mkdir(parents=True, exist_ok=True)
        with open(run_artifact_dir / "rolling_window_context.txt", "w", encoding="utf-8") as fh:
            fh.write(extra_context)

    trail = []
    status = None
    notes = ""
    discovery_start = time.time()
    try:
        trail = agent_loop.run_discovery(
            client, model=model, extra_context=extra_context, run_id=run_id, run_index=index
        )
    except TimeoutError as exc:
        status = "hardware_fault"
        notes = f"TimeoutError: {exc}"
        log(f"run {run_id} FAILED (hardware fault): {notes}")
    except Exception as exc:
        status = "llm_runtime_failure"
        notes = f"{type(exc).__name__}: {exc}"
        log(f"run {run_id} FAILED (LLM/proxy fault): {notes}")
    discovery_end = time.time()

    if status is None:
        # No exception: outcome is decided purely by the discovery loop's own
        # last reading vs. TARGET/TOLERANCE (spec section 14/21), never by an
        # orchestrator-side shortcut.
        status = "converged" if (trail and abs(trail[-1]["error"]) <= agent_loop.TOLERANCE) else "not_converged"

    keep_canonical = index == n_runs and status not in _FAULT_STATUSES
    skill_info = _archive_and_reset_skill(pre_run_skills, run_id, condition_label, keep_canonical)

    post_start = time.time()
    if status not in _FAULT_STATUSES:
        log(f"post-convergence phase ({timing['post_convergence_s']}s, holding final actuator state)...")
        time.sleep(timing["post_convergence_s"])
    post_end = time.time()

    log(f"inter-run cooldown ({timing['inter_run_cooldown_s']}s)...")
    time.sleep(timing["inter_run_cooldown_s"])

    return {
        "run_id": run_id, "run_index": index, "condition_label": condition_label,
        "status": status, "notes": notes, "trail": trail,
        "rolling_window_readings_seeded": rolling_window_readings_seeded,
        "baseline_start": baseline_start, "baseline_end": baseline_end,
        "discovery_start": discovery_start, "discovery_end": discovery_end,
        "post_start": post_start, "post_end": post_end,
        **skill_info,
    }


# --- MQTT extraction / alignment (spec sections 8.2, 11, 19, 20) -----------

def _load_adc_stream():
    if not ADC_STREAM_PATH.exists():
        return []
    records = []
    with open(ADC_STREAM_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda r: r.get("timestamp", 0))
    return records


def _load_discovery_rows():
    if not DISCOVERY_RUNS_CSV.exists():
        return []
    with open(DISCOVERY_RUNS_CSV, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _build_pwm_lookup(discovery_rows):
    by_run = {}
    for row in discovery_rows:
        rid = row["run_id"]
        by_run.setdefault(rid, []).append(
            (float(row["timestamp"]), int(row["duty_commanded"]), int(row["iteration"]))
        )
    for rid in by_run:
        by_run[rid].sort(key=lambda t: t[0])
    return by_run


def _phase_for(ts, r):
    if r["baseline_start"] <= ts < r["baseline_end"]:
        return "baseline"
    if r["discovery_start"] <= ts < r["discovery_end"]:
        return "discovery"
    if r["post_start"] <= ts <= r["post_end"]:
        return "post_convergence"
    return None


def _write_mqtt_timeseries(run_results, adc_records, pwm_lookup, out_path):
    rows = []
    for r in run_results:
        events = pwm_lookup.get(r["run_id"], [])
        ts_list = [e[0] for e in events]
        window_start, window_end = r["baseline_start"], r["post_end"]
        run_records = [
            rec for rec in adc_records
            if "timestamp" in rec and window_start <= rec["timestamp"] <= window_end
        ]
        for rec in run_records:
            ts = rec["timestamp"]
            phase = _phase_for(ts, r)
            if phase is None:
                continue  # falls in the unlabeled inter-run cooldown gap
            current_pwm = nearest_iteration = None
            if phase in ("discovery", "post_convergence") and ts_list:
                idx = bisect.bisect_right(ts_list, ts) - 1
                if idx >= 0:
                    current_pwm = events[idx][1]
                    nearest_iteration = events[idx][2]
            value = rec.get("value")
            absolute_error = abs(value - agent_loop.TARGET) if isinstance(value, (int, float)) else None
            rows.append({
                "run_id": r["run_id"], "run_index": r["run_index"],
                "condition_label": r["condition_label"],
                "timestamp": ts, "elapsed_s": round(ts - r["baseline_start"], 3),
                "phase": phase, "source": rec.get("source"), "sensor_reading": value,
                "target": agent_loop.TARGET, "tolerance": agent_loop.TOLERANCE,
                "absolute_error": absolute_error,
                "within_tolerance": (absolute_error <= agent_loop.TOLERANCE) if absolute_error is not None else None,
                "t_ms": rec.get("t_ms"), "seq": rec.get("seq"), "topic": rec.get("topic"),
                "current_pwm": current_pwm, "nearest_iteration": nearest_iteration,
            })
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MQTT_TS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


# --- Run summary / stability metrics (spec sections 18.4, 22, 23) ----------

def _seq_gaps(records):
    seqs = sorted(rec["seq"] for rec in records if rec.get("seq") is not None)
    gaps = 0
    for a, b in zip(seqs, seqs[1:]):
        if b > a + 1:
            gaps += b - a - 1
    return gaps


def _build_run_summary(run_results, mqtt_rows_by_run, discovery_by_run):
    rows = []
    for r in run_results:
        run_id = r["run_id"]
        trail = r["trail"]
        run_mqtt = mqtt_rows_by_run.get(run_id, [])
        baseline_rows = [m for m in run_mqtt if m["phase"] == "baseline"]
        post_rows = [m for m in run_mqtt if m["phase"] == "post_convergence"]

        baseline_values = [m["sensor_reading"] for m in baseline_rows if m["sensor_reading"] is not None]
        initial_sensor_mean = round(statistics.mean(baseline_values), 2) if baseline_values else None

        recovered_from_skill = False
        if trail:
            final_control_reading = trail[-1]["reading"]
            final_absolute_error = abs(trail[-1]["error"])
            converged = final_absolute_error <= agent_loop.TOLERANCE
        elif r["skill_saved"]:
            # trail is empty (no set_pwm calls this run) but a skill was
            # still saved: the LLM found the initial reading already within
            # tolerance and made zero adjustments, so agent_loop.py's own
            # trail-building only records a row once set_pwm has been
            # called -- it never captured this reading. Recover it from the
            # skill, which independently recorded final_error = target -
            # reading per agent_loop.py's SYSTEM_PROMPT contract for
            # save_skill. Not fabricated: a real value read from a
            # different real file this run actually wrote.
            skill_path = r["skill_archive_path"].split(";")[0]
            try:
                with open(skill_path, "r", encoding="utf-8") as fh:
                    skill = json.load(fh)
                skill_target = skill.get("target", agent_loop.TARGET)
                skill_tolerance = skill.get("tolerance", agent_loop.TOLERANCE)
                skill_final_error = skill["final_error"]
                final_control_reading = skill_target - skill_final_error
                final_absolute_error = abs(skill_final_error)
                converged = final_absolute_error <= skill_tolerance
                recovered_from_skill = True
            except (OSError, KeyError, json.JSONDecodeError):
                final_control_reading = None
                final_absolute_error = None
                converged = False
        else:
            final_control_reading = None
            final_absolute_error = None
            converged = False

        status = r["status"]
        if status not in _FAULT_STATUSES:
            status = "converged" if converged else "not_converged"

        time_to_convergence_s = None
        if status == "converged":
            run_disc_rows = discovery_by_run.get(run_id, [])
            if run_disc_rows:
                time_to_convergence_s = float(run_disc_rows[-1]["elapsed_s"])
            elif recovered_from_skill:
                # No logged discovery row to read elapsed_s from (zero
                # set_pwm calls) -- approximate from the whole discovery
                # call's wall-clock duration instead. Coarser than the
                # normal per-row elapsed_s; documented in notes below.
                time_to_convergence_s = round(r["discovery_end"] - r["discovery_start"], 3)

        post_values = [m["sensor_reading"] for m in post_rows if m["sensor_reading"] is not None]
        if post_values:
            post_mean = round(statistics.mean(post_values), 2)
            post_std = round(statistics.pstdev(post_values), 2)
            post_min = min(post_values)
            post_max = max(post_values)
            post_abs_errors = [abs(v - agent_loop.TARGET) for v in post_values]
            post_mean_absolute_error = round(statistics.mean(post_abs_errors), 2)
            post_within_count = sum(1 for e in post_abs_errors if e <= agent_loop.TOLERANCE)
            post_within_fraction = round(post_within_count / len(post_values), 4)
        else:
            post_mean = post_std = post_min = post_max = None
            post_mean_absolute_error = None
            post_within_count = 0
            post_within_fraction = None

        notes = r["notes"]
        if recovered_from_skill:
            extra_note = (
                "0 set_pwm calls -- initial reading already within tolerance; "
                "final_control_reading/final_absolute_error/converged recovered "
                "from the saved skill's final_error; time_to_convergence_s "
                "approximated from total discovery-call duration."
            )
            notes = f"{notes}; {extra_note}" if notes else extra_note

        rows.append({
            "run_id": run_id, "run_index": r["run_index"], "condition_label": r["condition_label"],
            "status": status,
            "iterations_used": len(trail), "target": agent_loop.TARGET, "tolerance": agent_loop.TOLERANCE,
            "initial_sensor_mean": initial_sensor_mean, "final_control_reading": final_control_reading,
            "final_absolute_error": final_absolute_error, "converged": converged,
            "time_to_convergence_s": time_to_convergence_s, "post_samples": len(post_values),
            "post_mean": post_mean, "post_std": post_std, "post_min": post_min, "post_max": post_max,
            "post_mean_absolute_error": post_mean_absolute_error,
            "post_within_tolerance_count": post_within_count,
            "post_within_tolerance_fraction": post_within_fraction,
            "telemetry_seq_gaps": _seq_gaps(run_mqtt),
            "rolling_window_readings_seeded": r["rolling_window_readings_seeded"],
            "skill_saved": r["skill_saved"], "skill_archive_path": r["skill_archive_path"],
            "notes": notes,
        })
    return rows


# --- Post-experiment validation (spec section 28) ---------------------------

def _validate(run_results, summary_rows, discovery_rows, mqtt_rows):
    problems = []

    ids = [r["run_id"] for r in run_results]
    if len(set(ids)) != len(ids):
        problems.append("duplicate run_id values across attempts")

    for r in run_results:
        n_iter = sum(1 for row in discovery_rows if row["run_id"] == r["run_id"])
        # 0 is valid: the LLM can find the initial reading already within
        # tolerance and save a skill with zero set_pwm adjustments (see
        # _build_run_summary's recovered_from_skill fallback).
        if r["status"] not in _FAULT_STATUSES and not (0 <= n_iter <= agent_loop.MAX_ITERATIONS):
            problems.append(f"{r['run_id']}: expected 0..{agent_loop.MAX_ITERATIONS} discovery rows, found {n_iter}")

    targets = {row["target"] for row in discovery_rows}
    tolerances = {row["tolerance"] for row in discovery_rows}
    if len(targets) > 1:
        problems.append(f"inconsistent target values across discovery rows: {targets}")
    if len(tolerances) > 1:
        problems.append(f"inconsistent tolerance values across discovery rows: {tolerances}")

    for r in run_results:
        if r["status"] == "hardware_fault":
            continue
        run_mqtt = [m for m in mqtt_rows if m["run_id"] == r["run_id"]]
        if not run_mqtt:
            problems.append(f"{r['run_id']}: no MQTT telemetry rows captured")
        elif r["status"] == "converged" and not any(m["phase"] == "post_convergence" for m in run_mqtt):
            problems.append(f"{r['run_id']}: converged but no post_convergence telemetry samples")

    for r in run_results:
        if r["skill_saved"]:
            archive_dir = _ARTIFACTS_DIR / r["condition_label"] / r["run_id"]
            if not archive_dir.exists() or not any(archive_dir.iterdir()):
                problems.append(f"{r['run_id']}: skill_saved=True but no archived artifact found")

    if len(summary_rows) != len(run_results):
        problems.append("run_summary.csv row count does not match number of run attempts")

    return problems


# --- Final handback (spec section 29) ---------------------------------------

def _print_handback(run_results, summary_rows, n_requested, condition_label, output_paths):
    converged = sum(1 for s in summary_rows if s["status"] == "converged")
    not_converged = sum(1 for s in summary_rows if s["status"] == "not_converged")
    faults = sum(1 for s in summary_rows if s["status"] in _FAULT_STATUSES)
    valid = [s for s in summary_rows if s["status"] in ("converged", "not_converged")]

    mean_iters = round(statistics.mean(s["iterations_used"] for s in valid), 2) if valid else None
    final_errs = [s["final_absolute_error"] for s in valid if s["final_absolute_error"] is not None]
    mean_final_abs_err = round(statistics.mean(final_errs), 2) if final_errs else None
    post_fracs = [s["post_within_tolerance_fraction"] for s in summary_rows if s["post_within_tolerance_fraction"] is not None]
    post_stds = [s["post_std"] for s in summary_rows if s["post_std"] is not None]
    mean_post_frac = round(statistics.mean(post_fracs) * 100, 1) if post_fracs else None
    mean_post_std = round(statistics.mean(post_stds), 2) if post_stds else None
    total_gaps = sum(s["telemetry_seq_gaps"] for s in summary_rows)

    print(f"\nStage 1 automated experiment complete -- condition: {condition_label}\n")
    if len(run_results) < n_requested:
        print(f"NOTE: batch stopped early after {len(run_results)} attempt(s) (see notes/status in run_summary.csv).\n")
    print(f"Requested runs: {n_requested}")
    print(f"Completed valid discovery runs: {len(valid)}")
    print(f"Converged: {converged}")
    print(f"Not converged: {not_converged}")
    print(f"Hardware/runtime failures: {faults}\n")
    print(f"Mean iterations to convergence: {mean_iters}")
    print(f"Mean final absolute error: {mean_final_abs_err} ADC\n")
    print("Post-convergence stability:")
    print(f"Mean within-tolerance fraction: {mean_post_frac}%")
    print(f"Mean post-convergence std: {mean_post_std} ADC\n")
    print(f"Telemetry sequence gaps: {total_gaps}\n")
    print("Outputs:")
    for p in output_paths.values():
        print(f"- {p}")
    archived = [r for r in run_results if r["skill_saved"]]
    if archived:
        print("\nArchived skills:")
        for r in archived:
            print(f"- {r['skill_archive_path']}")


# --- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 1 automated discovery experiment orchestrator.")
    parser.add_argument("--n-runs", type=int, default=N_RUNS_DEFAULT)
    parser.add_argument("--post-convergence-seconds", type=float, default=POST_CONVERGENCE_SECONDS_DEFAULT)
    parser.add_argument("--pre-run-baseline-seconds", type=float, default=PRE_RUN_BASELINE_SECONDS_DEFAULT)
    parser.add_argument("--inter-run-cooldown-seconds", type=float, default=INTER_RUN_COOLDOWN_SECONDS_DEFAULT)
    parser.add_argument("--serial-port", default=os.environ.get("SERIAL_PORT"))
    parser.add_argument(
        "--use-rolling-window", action="store_true",
        help=(
            "Seed each run's discovery conversation with Architecture B's rolling-window "
            "telemetry. Deliberate extension beyond STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md's "
            "invariant #3 (no MQTT values enter the LLM context) -- off by default."
        ),
    )
    parser.add_argument("--rolling-window-file", default=DEFAULT_ROLLING_WINDOW_FILE)
    parser.add_argument("--rolling-window-max-entries", type=int, default=None)
    parser.add_argument(
        "--condition-label", default=None,
        help="Output subdirectory name under experiments/logs and experiments/artifacts. "
             "Defaults to 'with_rolling_window' or 'no_context' based on --use-rolling-window.",
    )
    args = parser.parse_args()

    if args.n_runs < 1:
        fail("--n-runs must be >= 1")
    if args.n_runs < N_RUNS_MINIMUM_RECOMMENDED:
        log(f"WARN --n-runs={args.n_runs} is below the spec's recommended minimum of {N_RUNS_MINIMUM_RECOMMENDED} for the paper-grade dataset.")

    condition_label = args.condition_label or ("with_rolling_window" if args.use_rolling_window else "no_context")
    rolling_window_cfg = {
        "enabled": args.use_rolling_window,
        "file": args.rolling_window_file,
        "max_entries": args.rolling_window_max_entries,
    }
    if args.use_rolling_window:
        log(
            f"NOTE: condition '{condition_label}' deliberately deviates from "
            "STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md section 32 invariant #3 "
            "(seeding the LLM context from MQTT-derived telemetry), per explicit user request."
        )

    out_dir = _LOGS_DIR / condition_label
    out_dir.mkdir(parents=True, exist_ok=True)
    mqtt_timeseries_csv = out_dir / "mqtt_sensor_timeseries.csv"
    run_summary_csv = out_dir / "run_summary.csv"
    metadata_json = out_dir / "experiment_metadata.json"
    discovery_runs_csv_out = out_dir / "discovery_runs.csv"
    latency_csv_out = out_dir / "latency.csv"

    timing = {
        "pre_run_baseline_s": args.pre_run_baseline_seconds,
        "post_convergence_s": args.post_convergence_seconds,
        "inter_run_cooldown_s": args.inter_run_cooldown_seconds,
    }

    start_time = datetime.now().isoformat()

    preflight_architecture_a(args.serial_port)
    preflight_architecture_b()

    _cleanup_leftover_skills()
    _backup_and_reset_csv(DISCOVERY_RUNS_CSV, "discovery_runs.csv")
    _backup_and_reset_csv(LATENCY_CSV, "latency.csv")

    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    model = "gpt-5.5"

    run_results = []
    index = 1
    while index <= args.n_runs:
        result = run_one(client, model, index, args.n_runs, timing, condition_label, rolling_window_cfg)
        run_results.append(result)

        if result["status"] == "hardware_fault":
            log(f"aborting remaining runs after hardware fault in {result['run_id']}.")
            break

        if result["status"] == "llm_runtime_failure":
            log(f"retrying run {index} once after LLM/proxy failure...")
            retry = run_one(
                client, model, index, args.n_runs, timing, condition_label, rolling_window_cfg,
                retry_of=result["run_id"],
            )
            run_results.append(retry)
            if retry["status"] == "llm_runtime_failure":
                log(f"retry also failed for run {index}; aborting remaining runs.")
                break

        index += 1

    end_time = datetime.now().isoformat()

    discovery_rows = _load_discovery_rows()
    adc_records = _load_adc_stream()
    pwm_lookup = _build_pwm_lookup(discovery_rows)
    mqtt_rows = _write_mqtt_timeseries(run_results, adc_records, pwm_lookup, mqtt_timeseries_csv)

    mqtt_rows_by_run = {}
    for row in mqtt_rows:
        mqtt_rows_by_run.setdefault(row["run_id"], []).append(row)

    discovery_by_run = {}
    for row in discovery_rows:
        discovery_by_run.setdefault(row["run_id"], []).append(row)
    for rid in discovery_by_run:
        discovery_by_run[rid].sort(key=lambda r: int(r["iteration"]))

    summary_rows = _build_run_summary(run_results, mqtt_rows_by_run, discovery_by_run)
    with open(run_summary_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUN_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    metadata = {
        "experiment_name": "Stage 1 automated discovery with independent MQTT observation",
        "condition_label": condition_label,
        "use_rolling_window": args.use_rolling_window,
        "rolling_window_file": args.rolling_window_file if args.use_rolling_window else None,
        "rolling_window_max_entries": args.rolling_window_max_entries if args.use_rolling_window else None,
        "n_runs_requested": args.n_runs,
        "n_runs_attempted": len(run_results),
        "post_convergence_seconds": timing["post_convergence_s"],
        "pre_run_baseline_seconds": timing["pre_run_baseline_s"],
        "inter_run_cooldown_seconds": timing["inter_run_cooldown_s"],
        "target": agent_loop.TARGET,
        "tolerance": agent_loop.TOLERANCE,
        "max_iterations": agent_loop.MAX_ITERATIONS,
        "sensor_pin": agent_loop.SENSOR_PIN,
        "actuator_pin": agent_loop.LED_PIN,
        "mqtt_topic": MQTT_TOPIC,
        "telemetry_source": str(ADC_STREAM_PATH),
        "start_time": start_time,
        "end_time": end_time,
        "git_commit": _git_commit(),
        "deviation_from_spec": (
            "This condition seeds the LLM's discovery context from MQTT-derived rolling-window "
            "telemetry, which STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md section 32 (invariant #3) "
            "states must never happen. Collected as a deliberate, explicit user-requested extension "
            "for a with/without-temporal-context comparison; every other invariant in that document "
            "still applies to this condition's data."
        ) if args.use_rolling_window else "",
        "notes": "",
    }
    with open(metadata_json, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    problems = _validate(run_results, summary_rows, discovery_rows, mqtt_rows)
    if problems:
        log("VALIDATION FAILED:")
        for p in problems:
            log(f"  - {p}")
        log("All collected files have been preserved as-is; no data was rewritten to force this to pass.")
    else:
        log("validation passed.")

    # discovery_runs.csv/latency.csv are produced by the existing, unmodified
    # discovery_logger.py/latency_logger.py at fixed flat paths -- move them
    # into this condition's output directory now that everything that reads
    # them (above) has finished, so the two conditions never share a file.
    if DISCOVERY_RUNS_CSV.exists():
        shutil.move(str(DISCOVERY_RUNS_CSV), str(discovery_runs_csv_out))
    if LATENCY_CSV.exists():
        shutil.move(str(LATENCY_CSV), str(latency_csv_out))

    output_paths = {
        "discovery_runs.csv": discovery_runs_csv_out,
        "mqtt_sensor_timeseries.csv": mqtt_timeseries_csv,
        "latency.csv": latency_csv_out,
        "run_summary.csv": run_summary_csv,
        "experiment_metadata.json": metadata_json,
    }
    _print_handback(run_results, summary_rows, args.n_runs, condition_label, output_paths)


if __name__ == "__main__":
    main()
