"""
Layer: Agent
Component: Agent Loop -- the LLM-driven discovery loop for Stage 2 of the
mid-evaluation demo (see ../mid_evaluation_demo_plan.md). Connects to the
ESP32 over serial, hands the model the goal "Maintain a target light level
of 600," and lets it reason its own way there by calling the generic tools
in tool_declarations.py through registry.py.

This file deliberately does NOT know how to reach the target -- no PID, no
"if reading < target: increase duty" logic. That reasoning step belongs to
the LLM (see CLAUDE.md 2.2 -- pre-solving this in code would defeat the
point of the demo). This file only wires the tool-calling conversation
loop, enforces the iteration cap as a hard safety valve, and logs the
evidence trail (duty/reading/error per adjustment) for Section 4.6's table.
"""

import json
import os
import sys
import time
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent # absolute path to the directory containing this file
_BRIDGE_DIR = _AGENT_DIR.parent / "bridge"
sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))

from dotenv import load_dotenv
from openai import OpenAI

import registry
import serial_transport
import tool_declarations

load_dotenv()

TARGET = 1000
TOLERANCE = 25
MAX_ITERATIONS = 5

SENSOR_PIN = 34
LED_PIN = 5
PWM_MAX = 255  # matches firmware's 8-bit ledc resolution (main.cpp PWM_RESOLUTION_BITS); single
                # source of truth so nothing downstream (server.py, the dashboard) re-hardcodes it

# Kept derived from TARGET rather than a separate literal so the goal text
# the LLM reads can never drift out of sync with the constraint the system
# prompt enforces (it previously said "600" here while TARGET was 1000).
GOAL = f"Maintain a target light level of {TARGET}."

SYSTEM_PROMPT = f"""You are the reasoning layer of a hardware agent. You can only affect the \
physical world by calling the tools you're given -- you have no other way to act.

Hardware context for this task:
- Pin {SENSOR_PIN} is an analog-capable pin wired to a light sensor. Use read_analog({SENSOR_PIN}) \
to read it; higher values mean more light reaching the sensor.
- Pin {LED_PIN} is a PWM-capable pin wired to an LED. Use set_pwm({LED_PIN}, duty) to set its \
brightness; duty ranges 0-255.

You are not told what brightness achieves the goal -- discover it by reading the sensor, \
comparing the reading to the target, adjusting the LED brightness, and reading again. Do not \
assume a fixed or linear relationship between duty and reading; base each adjustment on what you \
actually observed.

Constraints:
- Target sensor reading: {TARGET}, tolerance: +/-{TOLERANCE}.
- You have at most {MAX_ITERATIONS} brightness adjustments (set_pwm calls) to get the reading \
within tolerance.
- Once you are within tolerance, or you have used all {MAX_ITERATIONS} adjustments, call \
save_skill once with a name (e.g. "maintain_light") and a definition containing: target, \
tolerance, brightness (the final duty you settled on), iterations (how many adjustments you \
took), final_error (target minus the last reading), and version (1, unless told otherwise).
- After saving, reply with one short plain-text sentence summarizing what you found, and call no \
further tools.
"""


def run_discovery(client: OpenAI, model: str, extra_context: str = "", on_event=None) -> list:
    """
    Runs one LLM-driven discovery attempt end to end. Returns the evidence
    trail: a list of {"duty": int, "reading": int, "error": int} dicts, one
    per set_pwm -> read_analog pair observed, in the order they happened.

    on_event, if given, is called as on_event(event_type: str, payload: dict)
    around each tool dispatch and sensor reading -- this is the hook the
    dashboard backend (agent/server.py) uses to mirror this loop's real
    activity onto the WebSocket wire contract in BACKEND_INTEGRATION_CONTRACT.md.
    Optional and side-effect-free when omitted: every existing caller (this
    file's own __main__, skill_runner.py) keeps behaving exactly as before.
    """
    def emit(event_type: str, payload: dict) -> None:
        if on_event is not None:
            on_event(event_type, payload)

    user_content = GOAL if not extra_context else f"{GOAL}\n\n{extra_context}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    trail = []
    pending_duty = None
    adjustments_used = 0
    warned_exhausted = False
    reading_count = 0
    emitted_executing = False

    # Generous cap on LLM round trips -- an independent safety valve so a
    # confused model can't loop forever; distinct from the MAX_ITERATIONS
    # constraint the model itself is told about.
    for _ in range(MAX_ITERATIONS * 4):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tool_declarations.TOOL_DECLARATIONS
        )
        message = response.choices[0].message

        if not message.tool_calls:
            if message.content:
                print(f"[agent] {message.content}")
                # A model turn with no tool calls means it answered in plain
                # text -- e.g. an informational prompt like "what can you
                # see?" rather than a control goal. Emitted as its own event
                # (distinct from "reasoning", which only ever accompanies a
                # sensor_reading) so a listener like the dashboard can render
                # it as a direct answer rather than treating this as a
                # discovery run that happened to do nothing.
                emit("agent_message", {"text": message.content})
            break

        messages.append(message)
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "set_pwm":
                adjustments_used += 1
                pending_duty = args.get("duty")

            if not emitted_executing:
                emitted_executing = True
                emit("status", {"state": "EXECUTING", "message": "Dispatching tool calls", "stage": "Stage 2 (Discovery)"})

            call_id = f"call_{time.time_ns()}"
            emit("tool_call", {"call_id": call_id, "tool": name, "args": args, "status": "pending"})

            start = time.perf_counter()
            try:
                result = registry.call_tool(name, args)
            except Exception as exc:
                result = {"success": False, "error": str(exc), "raw_response": None}
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
                print(f"  [tool] {name}({args}) -> EXCEPTION: {exc}")
                emit("tool_result", {"call_id": call_id, "tool": name, "result": result, "latency_ms": latency_ms, "status": "resolved"})
                raise

            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            print(f"  [tool] {name}({args}) -> {result}")
            emit("tool_result", {"call_id": call_id, "tool": name, "result": result, "latency_ms": latency_ms, "status": "resolved"})

            if name == "read_analog" and result.get("success"):
                reading = result.get("value")
                error = TARGET - reading
                reading_count += 1
                if pending_duty is not None:
                    trail.append({"duty": pending_duty, "reading": reading, "error": error})
                emit("sensor_reading", {
                    "iteration": reading_count,
                    "value": reading,
                    "target": TARGET,
                    "tolerance": TOLERANCE,
                    "duty": pending_duty if pending_duty is not None else 0,
                    "error": error,
                    "stage": "Stage 2 (Discovery)",
                })
                emit("reasoning", {
                    "step": reading_count,
                    "thought": f"Sensor pin {SENSOR_PIN} reads {reading} (target {TARGET}). Error is {error:+d}.",
                })

            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)}
            )

        if adjustments_used >= MAX_ITERATIONS and not warned_exhausted:
            warned_exhausted = True
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"You have used all {MAX_ITERATIONS} adjustments. Stop guessing new "
                        "brightness values -- save your best result as a skill now and reply."
                    ),
                }
            )

    return trail


def print_evidence_table(trail: list) -> None:
    print("\nTrial | Duty | Reading | Error")
    print("------|------|---------|------")
    for i, row in enumerate(trail, start=1):
        print(f"{i:5} | {row['duty']:4} | {row['reading']:7} | {row['error']:5}")


def main():
    port = os.environ.get("SERIAL_PORT")
    if not port:
        raise SystemExit("Set SERIAL_PORT in your .env (e.g. SERIAL_PORT=COM5) before running the agent loop.")
    serial_transport.connect(port)

    client = OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    print(f"[agent] goal: {GOAL}")
    trail = run_discovery(client, model="gpt-5.5")
    print_evidence_table(trail)


if __name__ == "__main__":
    main()
