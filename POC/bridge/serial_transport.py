"""
Layer: Bridge
Component #1: Serial Transport Module.

Owns the raw serial connection. Purely responsible for: open the port, send a line of text,
read a line of text back, handle timeouts. Kept deliberately separate from
tool_functions.py for clairty so the two responsibilities (moving bytes vs. knowing what
the bytes mean) are not mixed together.
-------------------------------------------------
connect() opens the pipe once and confirms the firmware is alive;
send() is called once per command, pushes text out, and blocks until a text reply comes back or times out 
— every other part of your system only ever interacts with the ESP32 through this one function.
"""

import sys
import threading
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
import latency_logger

_ser = None # module-level variable to hold the serial connection object

# Guards the write+readline pair in send() below. Only matters once there's
# more than one thread able to call send() against the same connection --
# e.g. agent/server.py's background sensor-polling task running alongside
# an active LLM-driven run's own tool calls, both via asyncio.to_thread
# (real OS threads, not coroutines). Without this, two threads' write/read
# pairs can interleave and one thread silently reads the *other* thread's
# response -- corrupting both, with no error raised.
_lock = threading.Lock()

# Stage 2 (latency measurement) instrumentation: one "run" == one connect()
# session, since this module has no visibility into agent_loop.py's own
# run_id/iteration concept (Stage 2 scopes this file only, not agent_loop.py).
# Correlates 1:1 with each discovery run's process lifetime in practice, but
# the run_id values themselves are independent of discovery_logger.py's --
# not joinable by exact ID, only by order/session.
_PRIMITIVE_NAMES = {
    "PING": "ping",
    "SET_GPIO": "set_gpio",
    "READ_GPIO": "read_gpio",
    "SET_PWM": "set_pwm",
    "READ_ADC": "read_analog",
    "READ_PWM": "read_pwm",
}
_run_id = None
_run_index = None
_iteration = 0


def connect(port: str, baud: int = 115200, timeout: float = 2.0) -> None:
    """Open the serial connection and wait for the firmware's READY line."""
    global _ser, _run_id, _run_index, _iteration # Alter the module-level (global variable) not create a new local variable
    _ser = serial.Serial(port, baud, timeout=timeout)
    time.sleep(2)  # ESP32 resets when the serial port opens; give it a moment
    _ser.reset_input_buffer() # Clean buffer after bootup
    ready_line = _ser.readline().decode(errors="ignore").strip() # blocks (till timeout) to get the "READY/n" alive message
    print(f"[transport] connected on {port}: {ready_line!r}")
    _run_id = time.strftime("%Y%m%dT%H%M%S")
    _run_index = None
    _iteration = 0


def set_experiment_context(run_id: str, run_index: int) -> None:
    """
    Override the run_id/run_index attributed to subsequent latency rows,
    without reconnecting the underlying serial session -- used by
    experiments/run_stage1_automated.py, which keeps one physical connection
    open across all N discovery runs (reconnecting per run would force an
    ESP32 reset between runs, disturbing the pre-run baseline). _iteration
    resets to 0 so each experiment run's latency numbering starts clean.
    No other caller uses this, so connect()/send()'s existing behavior for
    every other entry point (agent_loop.py's own __main__, skill_runner.py,
    server.py) is unaffected.
    """
    global _run_id, _run_index, _iteration
    _run_id = run_id
    _run_index = run_index
    _iteration = 0


def send(cmd: str) -> str:
    """Send one command line, return the single response line (raw string)."""
    if _ser is None:
        raise RuntimeError("Transport not connected -- call transport.connect() first.")
    global _iteration
    start = time.perf_counter()
    with _lock:
        _ser.write((cmd + "\n").encode()) # convert the python string to raw bytes for transmission over the serial port
        response = _ser.readline().decode(errors="ignore").strip() # Waits for a response line or times out from the firmware.
    if response == "":
        raise TimeoutError(f"No response from MCU for command: {cmd!r}")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    _iteration += 1
    primitive = _PRIMITIVE_NAMES.get(cmd.split()[0], cmd.split()[0].lower())
    latency_logger.log_call(_run_id, _iteration, primitive, latency_ms, run_index=_run_index)
    return response