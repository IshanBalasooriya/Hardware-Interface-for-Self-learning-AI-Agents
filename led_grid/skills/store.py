"""Skill store: validated, versioned JSON action sequences on disk (master section 6.6)."""

import json
import os
import re
from pathlib import Path

from config import MAX_WAIT_MS

NAME_RE = re.compile(r"[a-z0-9_]{1,40}")
HEX_RE = re.compile(r"(?:[0-9A-Fa-f]{2})+")
TOP_LEVEL_KEYS = {"type", "version", "description", "params", "actions"}
SHIFT_OUT_INTS = ("data_pin", "clock_pin", "latch_pin", "group_size")
ARG_KEYS = {
    "shift_out": set(SHIFT_OUT_INTS) | {"data_hex"},
    "wait": {"duration_ms"},
    "repeat": {"count", "actions"},
}
MAX_DESCRIPTION = 200
MAX_REPEAT_COUNT = 1000
MAX_REPEAT_DEPTH = 3


class SkillError(Exception):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_name(name: object) -> None:
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        raise SkillError(f"name must match ^[a-z0-9_]{{1,40}}$, got {name!r}")


def _is_param(value: object, params: dict | None, where: str) -> bool:
    """True if value is a `$name` placeholder. params=None means placeholders are not allowed."""
    if not (isinstance(value, str) and value.startswith("$")):
        return False
    if params is None or value[1:] not in params:
        raise SkillError(f"{where}: parameter {value!r} has no default in 'params'")
    return True


def _check_int(value: object, params: dict | None, where: str, low: int | None = None,
               high: int | None = None) -> None:
    if _is_param(value, params, where):
        return
    if not _is_int(value):
        raise SkillError(f"{where}: must be an integer, got {value!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise SkillError(f"{where}: must be {low}..{high}, got {value}")


def _validate_actions(actions: object, params: dict | None, where: str, depth: int) -> None:
    if not isinstance(actions, list) or not actions:
        raise SkillError(f"{where}: must be a non-empty list")
    for i, action in enumerate(actions):
        at = f"{where}[{i}]"
        if not isinstance(action, dict) or set(action) != {"tool", "args"}:
            raise SkillError(f"{at}: must be an object with exactly 'tool' and 'args'")
        tool, args = action["tool"], action["args"]
        if tool not in ARG_KEYS:
            raise SkillError(f"{at}.tool: must be one of shift_out, wait, repeat, got {tool!r}")
        if not isinstance(args, dict) or set(args) != ARG_KEYS[tool]:
            raise SkillError(f"{at}.args: {tool} needs exactly {sorted(ARG_KEYS[tool])}")
        if tool == "shift_out":
            for key in SHIFT_OUT_INTS:
                _check_int(args[key], params, f"{at}.args.{key}")
            data_hex = args["data_hex"]
            if not _is_param(data_hex, params, f"{at}.args.data_hex") and (
                    not isinstance(data_hex, str) or HEX_RE.fullmatch(data_hex) is None):
                raise SkillError(f"{at}.args.data_hex: must be a non-empty even-length hex string")
        elif tool == "wait":
            _check_int(args["duration_ms"], params, f"{at}.args.duration_ms", 0, MAX_WAIT_MS)
        else:
            if depth >= MAX_REPEAT_DEPTH:
                raise SkillError(f"{at}: repeat nesting deeper than {MAX_REPEAT_DEPTH}")
            _check_int(args["count"], params, f"{at}.args.count", 1, MAX_REPEAT_COUNT)
            _validate_actions(args["actions"], params, f"{at}.args.actions", depth + 1)


def validate(definition: dict) -> None:
    if not isinstance(definition, dict):
        raise SkillError("definition must be an object")
    extra = set(definition) - TOP_LEVEL_KEYS
    if extra:
        raise SkillError(f"unknown keys: {sorted(extra)}")
    if definition.get("type") != "action_sequence":
        raise SkillError("type must be 'action_sequence'")
    description = definition.get("description", "")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description must be a string of at most {MAX_DESCRIPTION} characters")
    params = definition.get("params", {})
    if not isinstance(params, dict) or not all(
            isinstance(k, str) and (_is_int(v) or isinstance(v, str)) for k, v in params.items()):
        raise SkillError("params must be an object of name -> integer or string")
    _validate_actions(definition.get("actions"), params, "actions", 0)


def count_steps(definition: dict) -> int:
    actions = definition.get("actions")
    return len(actions) if isinstance(actions, list) else 0


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        _check_name(name)
        return self.directory / f"{name}.json"

    def list(self) -> list[dict]:
        skills = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(definition, dict):
                continue
            skills.append({
                "name": path.stem,
                "description": definition.get("description", ""),
                "steps": count_steps(definition),
                "version": definition.get("version"),
                "type": definition.get("type"),
            })
        return skills

    def get(self, name: str) -> dict:
        path = self._path(name)
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SkillError(f"skill {name!r} not found") from None
        except (OSError, ValueError) as e:
            raise SkillError(f"skill {name!r} could not be read: {e}") from e
        if not isinstance(definition, dict):
            raise SkillError(f"skill {name!r} is not an object")
        return definition

    def save(self, name: str, definition: dict) -> dict:
        path = self._path(name)
        validate(definition)
        try:
            previous = self.get(name).get("version")
        except SkillError:
            previous = None
        version = previous + 1 if _is_int(previous) else 1
        stored = {"type": definition["type"], "version": version}
        for key in ("description", "params"):
            if key in definition:
                stored[key] = definition[key]
        stored["actions"] = definition["actions"]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return {"name": name, "version": version}
