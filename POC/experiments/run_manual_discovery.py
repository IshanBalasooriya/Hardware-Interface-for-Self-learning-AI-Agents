"""
Manual single-run discovery script.

Run this once per discovery attempt, by hand, whenever you're ready --
unlike run_stage1_automated.py (a fixed, non-interactive batch), this is
meant to be invoked one run at a time so you can physically vary ambient
light during the run and see how the agent reacts.

Each invocation:
  1. Resets telemetry: stops any running telemetry_service.py, clears
     adc_stream.txt and rolling_window.txt, and restarts telemetry_service.py
     as a subprocess this script owns. This is the only way to actually
     reset rolling_window.txt -- it mirrors an in-memory deque inside that
     process, so clearing the file alone would be overwritten again within
     about half a second by the still-running old process.
  2. Deletes any skill file(s) left in POC/skills/ from the previous run.
  3. Turns the LED off (set_pwm duty 0) before starting.
  4. Runs one unmodified agent_loop.run_discovery() call.
  5. Keeps recording for --post-run-seconds afterward (settle/confirmation
     window, no further control calls).
  6. Appends one row per sensor reading observed during the whole window
     (start of run through end of settle window) to a single ongoing CSV,
     sourced from the freshly-restarted telemetry stream
     (Temporaral_Context_Approach/logs/adc_stream.txt).

Requires the MQTT broker already running in its own terminal (see
Temporaral_Context_Approach/README.md) -- this script manages
telemetry_service.py itself, but not the broker.

Usage:
    python run_manual_discovery.py
    python run_manual_discovery.py --post-run-seconds 20
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_EXPERIMENTS_DIR = Path(__file__).resolve().parent
_POC_DIR = _EXPERIMENTS_DIR.parent
_AGENT_DIR = _POC_DIR / "agent"
_BRIDGE_DIR = _POC_DIR / "bridge"
_TELEMETRY_DIR = _POC_DIR / "Temporaral_Context_Approach"
_SKILLS_DIR = _POC_DIR / "skills"

sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))
sys.path.insert(0, str(_EXPERIMENTS_DIR))

from dotenv import load_dotenv
from openai import OpenAI

import agent_loop
import registry
import serial_transport

load_dotenv()

POST_RUN_SECONDS_DEFAULT = 15
CSV_PATH_DEFAULT = _EXPERIMENTS_DIR / "logs" / "manual_runs.csv"
CSV_FIELDS = ["run", "timestamp", "sensor_reading", "absolute_error", "percentage_error"]

ADC_STREAM_PATH = Path(
    os.environ.get("MQTT_LOG_FILE", str(_TELEMETRY_DIR / "logs" / "adc_stream.txt"))
)
ROLLING_WINDOW_PATH = Path(
    os.environ.get("MQTT_ROLLING_WINDOW_FILE", str(_TELEMETRY_DIR / "logs" / "rolling_window.txt"))
)
TELEMETRY_SUBPROCESS_LOG = _TELEMETRY_DIR / "logs" / "telemetry_service_manual.log"
TELEMETRY_STARTUP_TIMEOUT_S = 10
TELEMETRY_STABILITY_WINDOW_S = 3


def log(msg):
    print(f"[manual_run] {msg}", flush=True)


def fail(msg):
    log(f"ABORT: {msg}")
    raise SystemExit(1)


# --- Lightweight preflight ---------------------------------------------------

def preflight(port):
    if not port:
        fail("SERIAL_PORT is not set (check your .env, e.g. SERIAL_PORT=COM5).")
    log(f"connecting to ESP32 on {port} ...")
    try:
        serial_transport.connect(port)
    except Exception as exc:
        fail(f"could not open serial port {port}: {exc} (see free_com_port.ps1 if it's held by another process).")
    ping = registry.call_tool("ping", {})
    if not ping.get("success"):
        fail(f"ESP32 did not respond to PING: {ping}")
    log("preflight OK (serial connected, PING succeeded).")


# --- Telemetry reset (adc_stream.txt + rolling_window.txt) -------------------

def _kill_telemetry_service():
    """
    Stop any running telemetry_service.py, wherever it was started from --
    mirrors free_com_port.ps1's existing pattern of finding known processes
    by command line via Win32_Process rather than adding a new Python
    dependency (e.g. psutil) just for this.

    Name is filtered to python.exe/pythonw.exe as well as the command-line
    substring -- matching on CommandLine alone is dangerous: any shell
    process (bash.exe, powershell.exe) that happens to be running a command
    which itself mentions the text "telemetry_service.py" -- for example,
    while diagnosing or testing this very filter -- would otherwise match
    and get force-killed too. Verified live: an unfiltered CommandLine-only
    query matched this script's own diagnostic shell invocations.
    """
    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') "
        "-and $_.CommandLine -like '*telemetry_service.py*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=15,
    )


def _backup_and_truncate(path: Path, label: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return
    if path.stat().st_size == 0:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}_pre_manual_run_{stamp}{path.suffix}.bak")
    shutil.copy2(path, backup_path)
    log(f"backed up existing {label} -> {backup_path.name}")
    path.write_text("", encoding="utf-8")


def _start_telemetry_service():
    TELEMETRY_SUBPROCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(TELEMETRY_SUBPROCESS_LOG, "w", encoding="utf-8")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "telemetry_service.py"],
        cwd=str(_TELEMETRY_DIR),
        stdout=log_fh, stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def _tail_last_record(path: Path):
    """Return the last valid JSON record in the file, or None."""
    if not path.exists():
        return None
    last = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _confirm_telemetry_stable(
    timeout_s=TELEMETRY_STARTUP_TIMEOUT_S, stability_window_s=TELEMETRY_STABILITY_WINDOW_S
) -> bool:
    """
    Two-phase check, mirroring run_stage1_automated.py's
    preflight_architecture_b(): first wait for any telemetry to appear at
    all after the restart, then confirm the connection is actually stable
    (still growing, seq still incrementing, correct source pin) rather than
    proceeding on a single lucky reading right as the connection flaps.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ADC_STREAM_PATH.exists() and ADC_STREAM_PATH.stat().st_size > 0 and _tail_last_record(ADC_STREAM_PATH) is not None:
            break
        time.sleep(0.5)
    else:
        log(f"WARN no telemetry observed at all within {timeout_s}s of restarting.")
        return False

    size_before = ADC_STREAM_PATH.stat().st_size
    before = _tail_last_record(ADC_STREAM_PATH)
    before_seq = before.get("seq") if before else None

    log(f"telemetry is live; confirming stability over {stability_window_s}s before proceeding...")
    time.sleep(stability_window_s)

    size_after = ADC_STREAM_PATH.stat().st_size
    after = _tail_last_record(ADC_STREAM_PATH)
    after_seq = after.get("seq") if after else None

    if size_after <= size_before:
        log(f"WARN telemetry stopped growing during the {stability_window_s}s stability check.")
        return False
    if before_seq is not None and after_seq is not None and after_seq <= before_seq:
        log(f"WARN telemetry 'seq' did not advance during stability check ({before_seq} -> {after_seq}).")
        return False

    latest_source = after.get("source") if after else None
    if latest_source != agent_loop.SENSOR_PIN:
        log(
            f"WARN telemetry source (pin {latest_source}) does not match the discovery "
            f"loop's sensor pin ({agent_loop.SENSOR_PIN}) -- check "
            "firmware/include/telemetry_config.h's TELEMETRY_ADC_PIN."
        )
        return False

    log(
        f"telemetry stability check OK (grew {size_after - size_before} bytes, "
        f"seq {before_seq} -> {after_seq}, source pin {latest_source})."
    )
    return True


def _reset_telemetry():
    """
    Genuinely resets both telemetry outputs before a run. rolling_window.txt
    can't be reset by deleting the file alone -- it's a live mirror of an
    in-memory deque inside telemetry_service.py, so the still-running old
    process would just overwrite it again within ~0.5s. Stopping and
    restarting the process is what actually clears it. The new process is
    left running after this function returns (not killed at end-of-run) --
    the next invocation's own reset step is what "reset before a new run"
    means in practice.
    """
    log("resetting telemetry: stopping any running telemetry_service.py ...")
    _kill_telemetry_service()
    time.sleep(1)  # let the OS release the file handle / old MQTT client_id first

    _backup_and_truncate(ADC_STREAM_PATH, "adc_stream.txt")
    _backup_and_truncate(ROLLING_WINDOW_PATH, "rolling_window.txt")

    log("starting a fresh telemetry_service.py ...")
    _start_telemetry_service()

    if not _confirm_telemetry_stable():
        fail(
            f"could not confirm a stable MQTT telemetry connection after restarting "
            f"telemetry_service.py -- check {TELEMETRY_SUBPROCESS_LOG} and confirm the "
            "MQTT broker is running (mosquitto.exe -c mosquitto.conf -v)."
        )
    log("telemetry reset confirmed: fresh telemetry_service.py is live and stable.")


# --- Skill reset --------------------------------------------------------------

def _clear_previous_skill():
    if not _SKILLS_DIR.exists():
        return
    existing = [f for f in os.listdir(_SKILLS_DIR) if f.endswith(".json")]
    if not existing:
        return
    log(f"deleting previous run's skill file(s): {existing}")
    for fname in existing:
        (_SKILLS_DIR / fname).unlink()


# --- Telemetry extraction -----------------------------------------------------

def _load_adc_stream_window(start_ts, end_ts):
    if not ADC_STREAM_PATH.exists():
        return []
    records = []
    with open(ADC_STREAM_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp")
            if ts is not None and start_ts <= ts <= end_ts:
                records.append(rec)
    records.sort(key=lambda r: r["timestamp"])
    return records


# --- CSV writing ---------------------------------------------------------------

def _next_run_number(csv_path: Path) -> int:
    if not csv_path.exists():
        return 1
    max_run = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row.get("run"):
                continue
            try:
                max_run = max(max_run, int(row["run"]))
            except ValueError:
                continue
    return max_run + 1


def _append_run_rows(csv_path: Path, run_number: int, records: list, target: int):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        else:
            fh.write("\n")  # blank-line separator before each new run's block
        for rec in records:
            reading = rec.get("value")
            if reading is None:
                continue
            absolute_error = abs(reading - target)
            percentage_error = round(absolute_error / target * 100, 3)
            writer.writerow({
                "run": run_number,
                "timestamp": rec["timestamp"],
                "sensor_reading": reading,
                "absolute_error": absolute_error,
                "percentage_error": percentage_error,
            })


# --- One run -------------------------------------------------------------------

def run_once(client, model="gpt-5.5", csv_path: Path = CSV_PATH_DEFAULT, post_run_seconds: float = POST_RUN_SECONDS_DEFAULT):
    csv_path = Path(csv_path)
    run_number = _next_run_number(csv_path)
    log(f"=== manual run #{run_number} ===")

    _reset_telemetry()
    _clear_previous_skill()

    off = registry.call_tool("set_pwm", {"pin": agent_loop.LED_PIN, "duty": 0})
    if not off.get("success"):
        fail(f"could not turn LED off before the run: {off}")
    log("LED off, confirmed.")

    start_ts = time.time()
    log(f"[agent] goal: {agent_loop.GOAL}")
    trail = agent_loop.run_discovery(client, model=model)
    discovery_end_ts = time.time()

    log(f"settle window ({post_run_seconds}s, no further control calls)...")
    time.sleep(post_run_seconds)
    end_ts = time.time()

    records = _load_adc_stream_window(start_ts, end_ts)
    _append_run_rows(csv_path, run_number, records, agent_loop.TARGET)

    final_reading = records[-1]["value"] if records else None
    log(
        f"run #{run_number} done: {len(records)} readings logged to {csv_path} "
        f"(discovery used {len(trail)} adjustment(s); last telemetry reading: {final_reading}, "
        f"target {agent_loop.TARGET})"
    )

    # Convergence verdict from the settle window's own last telemetry sample
    # (not the LLM's self-reported final_error) -- this is the genuine
    # post-hoc confirmation the settle window exists to provide.
    if final_reading is not None:
        final_abs_error = abs(final_reading - agent_loop.TARGET)
        if final_abs_error <= agent_loop.TOLERANCE:
            convergence_note = f"CONVERGED (|{final_reading} - {agent_loop.TARGET}| = {final_abs_error} <= {agent_loop.TOLERANCE})"
        else:
            convergence_note = f"NOT CONVERGED (|{final_reading} - {agent_loop.TARGET}| = {final_abs_error} > {agent_loop.TOLERANCE})"
    else:
        convergence_note = "convergence unknown (no telemetry readings captured)"
    log(f"final settle-window convergence check: {convergence_note}")

    log("")
    log(f"---------------------- END OF RUN #{run_number} ----------------------")
    log("")

    return run_number, records


# --- Main ------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run one manual discovery attempt and log it.")
    parser.add_argument("--post-run-seconds", type=float, default=POST_RUN_SECONDS_DEFAULT)
    parser.add_argument("--csv-path", default=str(CSV_PATH_DEFAULT))
    parser.add_argument("--serial-port", default=os.environ.get("SERIAL_PORT"))
    args = parser.parse_args()

    preflight(args.serial_port)

    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    run_once(client, csv_path=Path(args.csv_path), post_run_seconds=args.post_run_seconds)


if __name__ == "__main__":
    main()
