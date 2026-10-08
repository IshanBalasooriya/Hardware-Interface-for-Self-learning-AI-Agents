"""Pending skill store: holds saves in memory until the audit approves them (agents master 7.3)."""

from __future__ import annotations  # the method named `list` shadows the builtin in annotations

import copy

from skills.store import SkillError, SkillStore, _check_name, _is_int, count_steps, validate


class PendingSkillStore:
    def __init__(self, real: SkillStore) -> None:
        self.real = real
        self._held: dict[str, dict] = {}  # insertion order = save order

    def _summary(self, name: str, definition: dict) -> dict:
        return {"name": name, "description": definition.get("description", ""),
                "steps": count_steps(definition), "version": definition.get("version"),
                "type": definition.get("type")}

    def list(self) -> list[dict]:
        skills = {entry["name"]: entry for entry in self.real.list()}
        for name, definition in self._held.items():
            skills[name] = self._summary(name, definition)
        return [skills[name] for name in sorted(skills)]

    def get(self, name: str) -> dict:
        if name in self._held:
            return copy.deepcopy(self._held[name])
        return self.real.get(name)

    def save(self, name: str, definition: dict) -> dict:
        _check_name(name)
        validate(definition)
        try:
            previous = self.real.get(name).get("version")
        except SkillError:
            previous = None
        version = previous + 1 if _is_int(previous) else 1
        held = {"type": definition["type"], "version": version}
        for key in ("description", "params"):
            if key in definition:
                held[key] = definition[key]
        held["actions"] = definition["actions"]
        self._held.pop(name, None)
        self._held[name] = copy.deepcopy(held)
        return {"name": name, "version": version}

    def held(self) -> list[tuple[str, dict]]:
        return [(name, copy.deepcopy(definition)) for name, definition in self._held.items()]

    def last(self) -> tuple[str, dict] | None:
        if not self._held:
            return None
        name = next(reversed(self._held))
        return name, copy.deepcopy(self._held[name])

    def commit(self) -> list[dict]:
        results = [self.real.save(name, definition) for name, definition in self._held.items()]
        self._held.clear()
        return results

    def discard(self) -> list[str]:
        names = list(self._held)
        self._held.clear()
        return names
