"""CSV instrumentation for Stage 1 (discovery convergence) data capture.

Schema, per "POC/Data Captured/STAGE_1_discovery_convergence.md" and extended
for "POC/Experiments_NEW/STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md" section 18.1:
    run_id, run_index, iteration, timestamp, elapsed_s, duty_commanded,
    sensor_reading, target, tolerance, signed_error, absolute_error,
    within_tolerance

run_index/elapsed_s/absolute_error/within_tolerance are blank for callers
that don't supply run_index/run_start_ts (i.e. every caller before the
Stage 1 automated orchestrator) -- see agent_loop.py::run_discovery().

Purely additive observation of agent_loop.py's existing discovery loop -- no
participation in reasoning, control, or skill-saving. One row per real
set_pwm->read_analog pair, batch-written only when a run completes without
raising (see log_run's docstring for why).
"""
import csv
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "logs" / "discovery_runs.csv"
_FIELDS = [
    "run_id", "run_index", "iteration", "timestamp", "elapsed_s",
    "duty_commanded", "sensor_reading", "target", "tolerance",
    "signed_error", "absolute_error", "within_tolerance",
]


def new_run_id():
    """Timestamp-based run_id, unique enough for this stage's manual-run cadence."""
    return time.strftime("%Y%m%dT%H%M%S")


def log_run(rows):
    """Persist one completed run's buffered rows to the CSV in one batch.

    Called only after run_discovery() finishes without raising -- a run that
    crashes (e.g. a serial TimeoutError) never reaches this call, so its
    partial data is never written, per the Stage 1 doc's explicit
    "do not log this as data" instruction for connection faults.
    """
    if not rows:
        return
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not _LOG_PATH.exists()
    with open(_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
