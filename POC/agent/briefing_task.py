"""
Layer: Agent
Component: Briefing Task -- a minimal, separate LLM round-trip for
informational questions about the hardware/tools ("What can you see?"),
used by the dashboard's hero-screen briefing card (liquid-gas/frontend's
"Ask" button, see agent/server.py's /api/briefing).

Deliberately NOT agent_loop.run_discovery(): that function's SYSTEM_PROMPT
unconditionally frames every conversation as the light-control discovery
task (TARGET/TOLERANCE/MAX_ITERATIONS, mandatory save_skill, "reply with
one sentence and stop"), regardless of what the user actually typed -- so
routing a question like "what can you see?" through it produces an
unwanted real discovery run rather than a plain answer. This file gives
informational questions their own honest system prompt instead, following
the same "separate file, don't touch run_discovery" pattern
CLAUDE_addendum_composition_stages.md already established for
composition_task.py.
"""

import json
import os
import sys
import time
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent
_BRIDGE_DIR = _AGENT_DIR.parent / "bridge"
sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))

from openai import OpenAI

import agent_loop
import registry
import tool_declarations

# This should never need many round trips -- one answer, at most one
# verification tool call first.
MAX_ROUND_TRIPS = 6

# f-string, not a static template: pulled from agent_loop's own constants
# (never duplicated as literals here) so this can never go stale or
# contradict the real hardware config the discovery flow actually uses.
# Without this, the model has no way to answer "what pins are you on?"
# other than deflecting -- it was never told, so it can't recite them.
SYSTEM_PROMPT = f"""You are the reasoning layer of a hardware agent, answering a question about \
your own setup right now -- not executing a control task.

Your actual hardware configuration:
- Pin {agent_loop.SENSOR_PIN} is an analog-capable pin wired to a light sensor. Read via \
read_analog({agent_loop.SENSOR_PIN}), raw range 0-4095.
- Pin {agent_loop.LED_PIN} is a PWM-capable pin wired to an LED. Set brightness via \
set_pwm({agent_loop.LED_PIN}, duty), duty 0-{agent_loop.PWM_MAX}, or plain on/off via \
set_gpio({agent_loop.LED_PIN}, 1 or 0).
- The separate light-control discovery flow (not this conversation) targets a sensor reading of \
{agent_loop.TARGET} +/- {agent_loop.TOLERANCE}, capped at {agent_loop.MAX_ITERATIONS} adjustments.

You have access to the same tools as always (set_gpio, read_gpio, read_analog, set_pwm, save_skill, \
wait, list_skills, get_skill), but in this mode your job is ONLY to answer the user's question in \
plain text, using the real pin numbers above where relevant. Only call a tool if the question \
specifically requires checking live state -- e.g. "is the hardware connected?" -> ping(), "what's \
the current sensor reading?" -> read_analog({agent_loop.SENSOR_PIN}), "what skills do you have \
saved?" -> list_skills(). Do not attempt to run a light-level discovery loop, adjust brightness \
toward a target, or save a new skill in this mode -- that only happens through the actual goal-run \
flow, not here.

Once you've answered the question (after any single verification tool call it actually required, if \
any), reply with your answer in plain text and call no further tools.
"""


def run_briefing(prompt: str, on_event=None) -> None:
    """
    One minimal round-trip conversation answering an informational question.
    The smallest of this codebase's three LLM loops (see
    agent_loop.run_discovery, composition_task.run_composition_task) -- no
    trail, no evidence table, no numeric target.

    on_event, if given, is called as on_event(event_type, payload) around
    each tool dispatch and the final plain-text reply -- same contract as
    agent_loop.run_discovery's hook, so agent/server.py's existing generic
    WebSocket relay (push_event) works unchanged for this path too.
    """
    def emit(event_type: str, payload: dict) -> None:
        if on_event is not None:
            on_event(event_type, payload)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
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
                print(f"[briefing] {message.content}")
                emit("agent_message", {"text": message.content})
            break

        messages.append(message)
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            call_id = f"call_{time.time_ns()}"
            emit("tool_call", {"call_id": call_id, "tool": name, "args": args, "status": "pending"})

            start = time.perf_counter()
            result = registry.call_tool(name, args)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)

            print(f"  [tool] {name}({args}) -> {result}")
            emit("tool_result", {"call_id": call_id, "tool": name, "result": result, "latency_ms": latency_ms, "status": "resolved"})

            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)}
            )
