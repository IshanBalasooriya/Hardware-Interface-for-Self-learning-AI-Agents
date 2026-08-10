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


if __name__ == "__main__":
    load_dotenv()
    port = os.environ.get("SERIAL_PORT")
    if not port:
        raise SystemExit("Set SERIAL_PORT in your .env (e.g. SERIAL_PORT=COM5) before running the skill runner.")
    serial_transport.connect(port)

    skill_name = sys.argv[1] if len(sys.argv) > 1 else "maintain_light"
    outcome = execute(skill_name)
    print_evidence_table(outcome["trail"])
    status = "converged" if outcome.get("converged") else "did not converge"
    print(
        f"\n[skill_runner] {skill_name}: {status} at duty={outcome['duty']} "
        f"reading={outcome['reading']} error={outcome['error']} "
        f"in {outcome['iterations']} iteration(s)"
    )
