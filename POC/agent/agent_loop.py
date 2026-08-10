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
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent
_BRIDGE_DIR = _AGENT_DIR.parent / "bridge"
sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))

from dotenv import load_dotenv
from openai import OpenAI

import registry
import serial_transport
import tool_declarations

load_dotenv()

TARGET = 600
TOLERANCE = 25
MAX_ITERATIONS = 5

SENSOR_PIN = 34
LED_PIN = 5

GOAL = "Maintain a target light level of 600."

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


def run_discovery(client: OpenAI, model: str, extra_context: str = "") -> list:
    """
    Runs one LLM-driven discovery attempt end to end. Returns the evidence
    trail: a list of {"duty": int, "reading": int, "error": int} dicts, one
    per set_pwm -> read_analog pair observed, in the order they happened.
    """
    user_content = GOAL if not extra_context else f"{GOAL}\n\n{extra_context}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    trail = []
    pending_duty = None
    adjustments_used = 0
    warned_exhausted = False

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
            break

        messages.append(message)
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "set_pwm":
                adjustments_used += 1
                pending_duty = args.get("duty")

            result = registry.call_tool(name, args)
            print(f"  [tool] {name}({args}) -> {result}")

            if name == "read_analog" and result.get("success") and pending_duty is not None:
                reading = result.get("value")
                trail.append({"duty": pending_duty, "reading": reading, "error": TARGET - reading})

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
