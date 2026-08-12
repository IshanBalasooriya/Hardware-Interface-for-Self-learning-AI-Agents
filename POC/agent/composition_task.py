"""
Layer: Agent
Component: Composition Task Runner -- the LLM-driven loop for the new
opening sequence in mid_evaluation_demo_plan_revised.md (Stages 2-4):
turning an LED on, composing a blink from primitives and saving it as a
skill, then generalizing that saved skill into a parameterized N-blink
skill.

This is a SEPARATE file from agent_loop.py on purpose (see
CLAUDE_addendum_composition_stages.md section 0): agent_loop.py's
run_discovery(), SYSTEM_PROMPT, GOAL, and TARGET/TOLERANCE/MAX_ITERATIONS
are the working, rehearsed Stage 5-8 pipeline and must not be touched.
This file reuses the same tool registry and transport, but has its own
system prompt and its own, simpler loop: no numeric convergence target,
no evidence trail, no error tracking -- just "keep calling tools until
the model says it's done." That's a deliberate difference from
run_discovery(), not an oversight: composition/generalization is
instruction-following, not closed-loop discovery (see the demo plan's
Section 1 distinction).
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

LED_PIN = 5

# Independent safety valve on LLM round trips, same rationale as
# agent_loop.py's cap -- this task has no MAX_ITERATIONS of its own to
# derive a cap from, so it's just a fixed generous number.
MAX_ROUND_TRIPS = 20

SYSTEM_PROMPT = f"""You are the reasoning layer of a hardware agent. You can only affect the \
physical world by calling the tools you're given -- you have no other way to act.

Hardware context for this task:
- Pin {LED_PIN} is a digital-capable pin wired to an LED. Use set_gpio({LED_PIN}, 1) to turn it on, \
set_gpio({LED_PIN}, 0) to turn it off.
- wait(duration_ms) pauses for a number of milliseconds with no hardware round trip -- use it to \
space out on/off calls so a sequence like a blink is visibly timed.
- list_skills() lists the names of skills you have already saved. get_skill(name) loads one saved \
skill's full definition so you can inspect exactly what it contains.
- save_skill(name, definition) persists a skill to the skill library. For skills in this task, set \
definition["type"] to "action_sequence", give it an "actions" list of {{"tool": <tool name>, "args": \
{{...}}}} steps in the order they should run, and a "version" number.

When asked to generalize a skill, first call list_skills() and get_skill(...) on the relevant saved \
skill to inspect what you actually saved -- reason from that real, persisted data, not from what you \
remember saying earlier in this conversation.

Once you have completed the requested action (and saved a skill, if asked to), reply with one short \
plain-text sentence summarizing what you did, and call no further tools.
"""


def run_composition_task(goal: str) -> None:
    """
    Runs one LLM-driven composition/generalization attempt end to end.
    Structurally similar to agent_loop.run_discovery()'s round-trip loop
    (send messages, handle tool calls, repeat until the model stops
    calling tools), but with no trail/error tracking -- this task has no
    numeric convergence target.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal},
    ]

    client = OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    for _ in range(MAX_ROUND_TRIPS):
        response = client.chat.completions.create(
            model="gpt-5.5", messages=messages, tools=tool_declarations.TOOL_DECLARATIONS
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

            result = registry.call_tool(name, args)
            print(f"  [tool] {name}({args}) -> {result}")

            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)}
            )


# The three composition/generalization goals from
# mid_evaluation_demo_plan_revised.md Section 6, Stages 2-4.
_STAGE_GOALS = {
    "2": "Turn the LED on.",
    "3": "Blink the LED twice, then save this behaviour as a skill called blink_twice.",
    "4": (
        "Look at your saved skills, and if you have one for blinking a fixed number of times, "
        "generalize it into a reusable skill that can blink the LED any number of times. "
        "Save it as blink_n_times."
    ),
}


if __name__ == "__main__":
    port = os.environ.get("SERIAL_PORT")
    if not port:
        raise SystemExit("Set SERIAL_PORT in your .env (e.g. SERIAL_PORT=COM5) before running the composition task.")
    serial_transport.connect(port)

    arg = sys.argv[1] if len(sys.argv) > 1 else "2"
    goal = _STAGE_GOALS.get(arg, arg)  # a bare stage number ("2"/"3"/"4"), or a literal goal string

    print(f"[agent] goal: {goal}")
    run_composition_task(goal)
