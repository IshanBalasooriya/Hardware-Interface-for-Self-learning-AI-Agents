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

import threading
import time
import serial

_ser = None # module-level variable to hold the serial connection object

# Guards the write+readline pair in send() below. Only matters once there's
# more than one thread able to call send() against the same connection --
# e.g. agent/server.py's background sensor-polling task running alongside
# an active LLM-driven run's own tool calls, both via asyncio.to_thread
# (real OS threads, not coroutines). Without this, two threads' write/read
# pairs can interleave and one thread silently reads the *other* thread's
# response -- corrupting both, with no error raised.
_lock = threading.Lock()


def connect(port: str, baud: int = 115200, timeout: float = 2.0) -> None:
    """Open the serial connection and wait for the firmware's READY line."""
    global _ser # Alter the module-level (global variable) not create a new local variable
    _ser = serial.Serial(port, baud, timeout=timeout)
    time.sleep(2)  # ESP32 resets when the serial port opens; give it a moment
    _ser.reset_input_buffer() # Clean buffer after bootup
    ready_line = _ser.readline().decode(errors="ignore").strip() # blocks (till timeout) to get the "READY/n" alive message
    print(f"[transport] connected on {port}: {ready_line!r}")


def send(cmd: str) -> str:
    """Send one command line, return the single response line (raw string)."""
    if _ser is None:
        raise RuntimeError("Transport not connected -- call transport.connect() first.")
    with _lock:
        _ser.write((cmd + "\n").encode()) # convert the python string to raw bytes for transmission over the serial port
        response = _ser.readline().decode(errors="ignore").strip() # Waits for a response line or times out from the firmware.
    if response == "":
        raise TimeoutError(f"No response from MCU for command: {cmd!r}")
    return response