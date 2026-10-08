"""Skill runner: replays a validated skill through the Bridge. No LLM, no generated code."""

import time
from typing import Callable

from bridge.bridge import Bridge
from config import SKILL_TIME_CAP_S
from skills.store import SkillError, _validate_actions, validate


class _MissingParam(Exception):
    pass


def _substitute(actions: list, values: dict) -> list:
    resolved = []
    for action in actions:
        args = {}
        for key, value in action["args"].items():
            if key == "actions":
                value = _substitute(value, values)
            elif isinstance(value, str) and value.startswith("$"):
                if value[1:] not in values:
                    raise _MissingParam(value)
                value = values[value[1:]]
            args[key] = value
        resolved.append({"tool": action["tool"], "args": args})
    return resolved


def run_skill(definition: dict, bridge: Bridge, params: dict | None = None,
              should_stop: Callable[[], bool] | None = None,
              time_cap_s: float = SKILL_TIME_CAP_S) -> dict:
    try:
        validate(definition)
    except SkillError as e:
        return {"success": False, "error": str(e)}
    values = {**definition.get("params", {}), **(params or {})}
    try:
        actions = _substitute(definition["actions"], values)
        _validate_actions(actions, None, "actions", 0)
    except (_MissingParam, SkillError):
        return {"success": False, "error": "bad_params"}

    deadline = time.monotonic() + time_cap_s
    state = {"steps_run": 0, "stopped": False, "timed_out": False, "error": None}

    def interrupted() -> bool:
        if should_stop is not None and should_stop():
            state["stopped"] = True
        elif time.monotonic() >= deadline:
            state["timed_out"] = True
        return state["stopped"] or state["timed_out"]

    def run(block: list) -> bool:
        """Run actions in order. Returns False when the run must end."""
        for action in block:
            if interrupted():
                return False
            tool, args = action["tool"], action["args"]
            if tool == "shift_out":
                result = bridge.shift_out(**args)
                if not result["success"]:
                    state["error"] = result["error"]
                    return False
                state["steps_run"] += 1
            elif tool == "wait":
                bridge.wait(args["duration_ms"], interrupted)
                state["steps_run"] += 1
            elif tool == "repeat":
                for _ in range(args["count"]):
                    if not run(args["actions"]):
                        return False
        return True

    run(actions)
    if state["error"] is not None:
        return {"success": False, "error": state["error"], "steps_run": state["steps_run"],
                "final_state": bridge.store.current()}
    return {"success": True, "steps_run": state["steps_run"], "stopped": state["stopped"],
            "timed_out": state["timed_out"], "final_state": bridge.store.current()}
