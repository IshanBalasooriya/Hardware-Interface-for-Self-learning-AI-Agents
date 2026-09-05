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
import time
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

Before composing anything from scratch, always call list_skills() first, then get_skill() on any \
skill that looks relevant, and reason about what you find in one of three ways:

1. An existing skill's "actions" already contain a {{"tool": "repeat", "args": {{"count": ..., \
"actions": [...]}}}} step -- it's already generalized and handles this kind of request for ANY \
count. Resolve its parameter (substitute the requested count for "repeat"'s "count") and execute \
the resulting concrete tool calls directly. Do NOT save anything -- you are reusing an existing \
skill, not composing a new one.

2. An existing skill is a FIXED, non-generalized version of the same kind of action but for a \
DIFFERENT parameter (e.g. you're asked to blink 3 times and you already have a skill that blinks \
some other fixed number of times, like 2). This is the same situation as #1 except the existing \
skill hasn't been generalized yet -- recognize the structural similarity yourself, right now, \
without waiting to be explicitly told to "generalize": rewrite its fixed "actions" into a \
parameterized {{"tool": "repeat", "args": {{"count": <n>, "actions": [...]}}}} form, execute it for \
the count actually requested, and save the generalized version back under that EXISTING skill's \
name (get_skill first for its current version, save with version = existing + 1). Do not also save \
a second, separate skill for this specific count -- the whole point is ending up with one \
generalized skill covering both the original request and this one, not two fixed ones.

3. Nothing existing -- generalized or fixed-but-similar -- covers this request. Only in this case do \
you compose fresh from primitives and save a new skill (see below).

When you DO compose and carry out a genuinely new action, always save it as a skill via save_skill -- \
even a single-step action like turning the LED on or off, not just multi-step sequences. Give it a \
short, descriptive snake_case name based on what you actually did (e.g. "led_on", "led_off", \
"blink_twice") unless the prompt already told you exactly what name to save it under, in which case \
use that name instead of inventing one. The "actions" list should be the exact tool calls you just \
made, in order. save_skill silently overwrites whatever is already saved under that name, so if you \
are reusing a name (e.g. re-running "turn the LED on"), first call get_skill(name) to check for an \
existing version and save with version = (existing version + 1) instead of always saving version 1.

When asked to generalize a skill, first call list_skills() and get_skill(...) on the relevant saved \
skill to inspect what you actually saved -- reason from that real, persisted data, not from what you \
remember saying earlier in this conversation. Save the generalized version back under that SAME \
skill's name (call get_skill first to get its current version, save with version = existing + 1) -- \
do NOT invent a different name for it. Generalizing exists to reduce the number of skills saved, not \
to keep both the original specific skill and a new general one around side by side; overwriting the \
original with its generalized form is the entire point. Only save under a different name if the \
request explicitly tells you what different name to use.

Once you have completed the requested action -- whether by reusing an existing generalized skill or \
by composing and saving a new one -- reply with one short plain-text sentence summarizing what you \
did (and say explicitly if you reused an existing skill rather than creating a new one), and call no \
further tools.
"""


def run_composition_task(goal: str, on_event=None) -> None:
    """
    Runs one LLM-driven composition/generalization attempt end to end.
    Structurally similar to agent_loop.run_discovery()'s round-trip loop
    (send messages, handle tool calls, repeat until the model stops
    calling tools), but with no trail/error tracking -- this task has no
    numeric convergence target.

    on_event, if given, is called as on_event(event_type, payload) around
    each tool dispatch and the final plain-text reply -- same optional,
    additive contract as agent_loop.run_discovery's hook (added later for
    the dashboard) and briefing_task.run_briefing's. The existing CLI
    __main__ below never passes one, so this stays a no-op for that call
    path -- nothing about Stage 2/3/4's CLI behavior changes.
    """
    def emit(event_type: str, payload: dict) -> None:
        if on_event is not None:
            on_event(event_type, payload)

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


# The three composition/generalization goals from
# mid_evaluation_demo_plan_revised.md Section 6, Stages 2-4.
_STAGE_GOALS = {
    "2": "Turn the LED on.",
    "3": "Blink the LED twice, then save this behaviour as a skill called blink_twice.",
    "4": (
        "Look at your saved skills, and if you have one for blinking a fixed number of times, "
        "generalize it into a reusable skill that can blink the LED any number of times. "
        "Save the generalized version."
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
