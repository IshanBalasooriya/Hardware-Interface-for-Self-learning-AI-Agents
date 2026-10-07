"""Tool registry: the only path from the LLM to hardware and files (master sections 6.4, 6.5)."""

from dataclasses import dataclass
from typing import Callable

from bridge.bridge import Bridge
from config import MAX_HISTORY, MAX_SHIFT_BYTES, MAX_WAIT_MS
from skills.runner import run_skill
from skills.store import SkillError, SkillStore

MAX_ERROR_LEN = 200
SKILL_NAME_PATTERN = "^[a-z0-9_]{1,40}$"


@dataclass
class AgentContext:
    bridge: Bridge
    skills: SkillStore
    should_stop: Callable[[], bool]


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required if required is not None else list(properties),
                       "additionalProperties": False},
    }}


_PIN = {"type": "integer", "minimum": 0, "maximum": 39}

TOOL_SCHEMAS: list[dict] = [
    _schema("shift_out",
            "Shift bytes out MSB first on a data/clock pin pair; after every group_size bytes the latch pin "
            f"is pulsed so that group takes effect. At most {MAX_SHIFT_BYTES} bytes per call, "
            "a multiple of group_size.",
            {"data_pin": _PIN, "clock_pin": _PIN, "latch_pin": _PIN,
             "group_size": {"type": "integer", "minimum": 1, "maximum": MAX_SHIFT_BYTES},
             "data_hex": {"type": "string", "pattern": f"^([0-9A-Fa-f]{{2}}){{1,{MAX_SHIFT_BYTES}}}$",
                          "description": "Bytes to send as hex, e.g. '0C01'."}}),
    _schema("wait", "Pause for a number of milliseconds without sending anything.",
            {"duration_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS}}),
    _schema("read_shift_state",
            "Return the current state of the bound shift device, as confirmed by the device's replies.", {}),
    _schema("read_recent_frames", "Return the most recent confirmed states of the bound shift device, oldest first.",
            {"count": {"type": "integer", "minimum": 1, "maximum": MAX_HISTORY}}),
    _schema("list_skills", "List saved skills with name, description, step count and version.", {}),
    _schema("get_skill", "Return the full JSON definition of a saved skill.",
            {"name": {"type": "string", "pattern": SKILL_NAME_PATTERN}}),
    _schema("save_skill",
            "Save a skill: a JSON action sequence of shift_out, wait and repeat steps. "
            "Saving an existing name creates a new version.",
            {"name": {"type": "string", "pattern": SKILL_NAME_PATTERN},
             "definition": {"type": "object", "description": "Skill JSON with type 'action_sequence', "
                            "optional description and params, and a non-empty actions list."}}),
    _schema("reuse_skill", "Replay a saved skill on the hardware, optionally overriding its params.",
            {"skill_name": {"type": "string", "pattern": SKILL_NAME_PATTERN},
             "params": {"type": "object", "description": "Values for the skill's $name placeholders."}},
            required=["skill_name"]),
]

# tool -> (required {arg: type}, optional {arg: type})
_ARGS: dict[str, tuple[dict, dict]] = {
    "shift_out": ({"data_pin": int, "clock_pin": int, "latch_pin": int, "group_size": int, "data_hex": str}, {}),
    "wait": ({"duration_ms": int}, {}),
    "read_shift_state": ({}, {}),
    "read_recent_frames": ({"count": int}, {}),
    "list_skills": ({}, {}),
    "get_skill": ({"name": str}, {}),
    "save_skill": ({"name": str, "definition": dict}, {}),
    "reuse_skill": ({"skill_name": str}, {"params": dict}),
}


def _has_type(value: object, expected: type) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _check_args(name: str, args: object) -> str | None:
    """Return a bad_args detail, or None if the arguments are acceptable."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    required, optional = _ARGS[name]
    allowed = {**required, **optional}
    for key in args:
        if key not in allowed:
            return f"unexpected argument {key!r}"
    for key, expected in allowed.items():
        if key not in args:
            if key in required:
                return f"missing argument {key!r}"
        elif not _has_type(args[key], expected):
            return f"{key} must be {expected.__name__}"
    if name == "read_recent_frames" and not 1 <= args["count"] <= MAX_HISTORY:
        return f"count must be 1..{MAX_HISTORY}"
    return None


def _run(name: str, args: dict, ctx: AgentContext) -> dict:
    if name == "shift_out":
        return ctx.bridge.shift_out(**args)
    if name == "wait":
        return ctx.bridge.wait(args["duration_ms"], ctx.should_stop)
    if name == "read_shift_state":
        return {"success": True, "state": ctx.bridge.store.current()}
    if name == "read_recent_frames":
        return {"success": True, "frames": ctx.bridge.store.recent(args["count"])}
    if name == "list_skills":
        return {"success": True, "skills": ctx.skills.list()}
    if name == "get_skill":
        return {"success": True, "definition": ctx.skills.get(args["name"])}
    if name == "save_skill":
        return {"success": True, **ctx.skills.save(args["name"], args["definition"])}
    definition = ctx.skills.get(args["skill_name"])
    return run_skill(definition, ctx.bridge, args.get("params"), ctx.should_stop)


def call_tool(name: str, args: dict, ctx: AgentContext) -> dict:
    if name not in _ARGS:
        return {"success": False, "error": "unknown_tool"}
    problem = _check_args(name, args)
    if problem is not None:
        return {"success": False, "error": f"bad_args: {problem}"}
    try:
        return _run(name, args, ctx)
    except SkillError as e:
        return {"success": False, "error": str(e)[:MAX_ERROR_LEN]}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]}
