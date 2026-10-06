"""CSV instrumentation for Stage 3 (replay verification) data capture.

Schema, per "POC/Data Captured/STAGE_3_replay_verification.md":
    iteration, sensor_reading, timestamp

No run_id column -- this stage is explicitly a single-pass check, not a
multi-run series like Stages 1/2. Rows are appended immediately per
iteration (not buffered), since the doc's own failure-handling wants
partial data kept if a replay fails partway through, not discarded.
"""
import csv
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "logs" / "replay_verification.csv"
_FIELDS = ["iteration", "sensor_reading", "timestamp"]


def log_iteration(iteration, sensor_reading):
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not _LOG_PATH.exists()
    with open(_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "iteration": iteration,
            "sensor_reading": sensor_reading,
            "timestamp": round(time.time(), 3),
        })
