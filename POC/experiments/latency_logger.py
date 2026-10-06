"""CSV instrumentation for Stage 2 (latency measurement) data capture.

Schema, per "POC/Data Captured/STAGE_2_latency_measurement.md" and extended
for "POC/Experiments_NEW/STAGE_1_AUTOMATED_DISCOVERY_WITH_MQTT.md" section 18.3:
    run_id, run_index, iteration, primitive, latency_ms

run_index is blank unless the caller is under an active
serial_transport.set_experiment_context() (i.e. the Stage 1 automated
orchestrator) -- every other caller (skill_runner.py's own replay calls)
logs it as None exactly as before.

Purely additive observation of bridge/serial_transport.py::send() -- no
participation in the command/response path itself. One row per completed
send() call; a call that times out (raises TimeoutError) produces no valid
measurement and is simply never logged, which is correct with no special
handling needed here.
"""
import csv
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "logs" / "latency.csv"
_FIELDS = ["run_id", "run_index", "iteration", "primitive", "latency_ms"]


def log_call(run_id, iteration, primitive, latency_ms, run_index=None):
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not _LOG_PATH.exists()
    with open(_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "run_id": run_id,
            "run_index": run_index,
            "iteration": iteration,
            "primitive": primitive,
            "latency_ms": latency_ms,
        })
