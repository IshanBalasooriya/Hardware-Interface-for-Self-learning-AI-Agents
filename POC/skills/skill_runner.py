"""
Layer: Skills
Component: Skill Runner -- deterministic replay of a saved skill via the
tool registry, with NO LLM call involved (CLAUDE.md 2.6 / demo Stage 4).
Given a skill saved by agent_loop.py (target, tolerance, brightness, ...),
this starts from the skill's stored brightness instead of guessing from
zero, and uses a fixed proportional nudge -- not model reasoning -- to
fine-tune if the environment has drifted slightly. This is intentionally
dumber than the LLM: it has no way to handle a genuinely new situation,
which is why Stage 5 routes back to the LLM-driven loop when a disturbance
is large enough that this doesn't converge.
"""

import json
import os
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SKILLS_DIR.parent / "agent"))
sys.path.insert(0, str(_SKILLS_DIR.parent / "bridge"))

from dotenv import load_dotenv

import registry
import serial_transport

SENSOR_PIN = 34
LED_PIN = 5
MAX_ITERATIONS = 5  # mirrors the fixed constant in CLAUDE.md / agent_loop.py

# Fixed deterministic gain for the fallback nudge -- not learned or tuned
# per skill. If this isn't enough to reconverge, that's the signal to fall
# back to the LLM-driven discovery loop rather than iterate this further.
_STEP_GAIN = 0.3


def load_skill(name: str) -> dict:
    path = _SKILLS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"No saved skill named {name!r} at {path}")
    with open(path) as f:
        return json.load(f)


def execute(name: str) -> dict:
    """
    Replays a saved skill deterministically, starting from its stored
    brightness. Returns the outcome plus an evidence trail (duty/reading/
    error per attempt) matching the table agent_loop.py logs.
    """
    skill = load_skill(name)
    target = skill["target"]
    tolerance = skill["tolerance"]
    duty = skill["brightness"]

    trail = []
    for _ in range(MAX_ITERATIONS):
        set_result = registry.call_tool("set_pwm", {"pin": LED_PIN, "duty": duty})
        read_result = registry.call_tool("read_analog", {"pin": SENSOR_PIN})

        if not set_result.get("success") or not read_result.get("success"):
            return {"success": False, "error": "hardware call failed", "trail": trail}

        reading = read_result["value"]
        error = target - reading
        trail.append({"duty": duty, "reading": reading, "error": error})

        if abs(error) <= tolerance:
            return {
                "success": True,
                "converged": True,
                "duty": duty,
                "reading": reading,
                "error": error,
                "iterations": len(trail),
                "trail": trail,
            }

        duty = max(0, min(255, duty + int(_STEP_GAIN * error)))

    return {
        "success": True,
        "converged": False,
        "duty": duty,
        "reading": trail[-1]["reading"],
        "error": trail[-1]["error"],
        "iterations": len(trail),
        "trail": trail,
    }


def print_evidence_table(trail: list) -> None:
    print("\nTrial | Duty | Reading | Error")
    print("------|------|---------|------")
    for i, row in enumerate(trail, start=1):
        print(f"{i:5} | {row['duty']:4} | {row['reading']:7} | {row['error']:5}")


def execute_action_sequence(name: str, **params) -> dict:
    """
    Deterministic replay of an action_sequence skill (e.g. blink_twice,
    blink_n_times) via the same registry.call_tool() interface as execute()
    above -- no LLM call involved. This is the optional executor flagged
    (but not built) in CLAUDE_addendum_composition_stages.md section 5;
    purely additive -- execute() and its call path are untouched.

    A step whose "tool" is the literal "repeat" isn't a real registry tool --
    it's the parameterized-loop shape the model itself invented when
    generalizing blink_twice into blink_n_times (see skills/blink_n_times.json):
    {"tool": "repeat", "args": {"count": <placeholder-or-literal>, "actions":
    [...]}}. If "count" is a string, it's resolved against **params (e.g.
    execute_action_sequence("blink_n_times", n=5) resolves a "count": "n"
    placeholder to 5); an integer count is used as-is.
    """
    skill = load_skill(name)
    if skill.get("type") != "action_sequence":
        return {"success": False, "name": name, "error": f"{name!r} is not an action_sequence skill", "trail": []}

    trail = []
    ok = [True]  # mutable flag so the nested closure below can signal an abort across recursion

    def run_steps(steps):
        for step in steps:
            if not ok[0]:
                return
            tool = step.get("tool")
            args = step.get("args", {})
            if tool == "repeat":
                count = args.get("count")
                if isinstance(count, str):
                    count = params.get(count)
                if count is None:
                    ok[0] = False
                    trail.append({"tool": "repeat", "error": f"unresolved 'count' parameter -- pass it as a keyword argument"})
                    return
                for _ in range(int(count)):
                    if not ok[0]:
                        return
                    run_steps(args.get("actions", []))
            else:
                result = registry.call_tool(tool, args)
                trail.append({"tool": tool, "args": args, "result": result})
                if not result.get("success"):
                    ok[0] = False
                    return

    run_steps(skill.get("actions", []))
    return {"success": ok[0], "name": name, "steps_executed": len(trail), "trail": trail}


if __name__ == "__main__":
    load_dotenv()
    port = os.environ.get("SERIAL_PORT")
    if not port:
        raise SystemExit("Set SERIAL_PORT in your .env (e.g. SERIAL_PORT=COM5) before running the skill runner.")
    serial_transport.connect(port)

    skill_name = sys.argv[1] if len(sys.argv) > 1 else "maintain_light"
    extra_args = sys.argv[2:]

    skill = load_skill(skill_name)
    if skill.get("type") == "action_sequence":
        params = {}
        for arg in extra_args:
            if "=" not in arg:
                raise SystemExit(f"Expected key=value params for an action_sequence skill (e.g. n=5), got: {arg!r}")
            key, value = arg.split("=", 1)
            params[key] = int(value) if value.lstrip("-").isdigit() else value

        outcome = execute_action_sequence(skill_name, **params)
        status = "succeeded" if outcome["success"] else "failed"
        print(f"\n[skill_runner] {skill_name}: {status}, {outcome['steps_executed']} step(s) executed")
        for row in outcome["trail"]:
            print(f"  {row}")
    else:
        outcome = execute(skill_name)
        print_evidence_table(outcome["trail"])
        status = "converged" if outcome.get("converged") else "did not converge"
        print(
            f"\n[skill_runner] {skill_name}: {status} at duty={outcome['duty']} "
            f"reading={outcome['reading']} error={outcome['error']} "
            f"in {outcome['iterations']} iteration(s)"
        )
