"""Shared helpers for the agents tests: scripted LLM client, reply builders, fake-device context."""

import json
from types import SimpleNamespace

from agent.tools import AgentContext
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport
from config import CLOCK_PIN, DATA_PIN, GROUP_SIZE, LATCH_PIN, MSB_IS_LEFT
from skills.store import SkillStore


class ScriptedClient:
    """Returns prepared replies in order; the last one repeats. An Exception in the list is raised.
    Records every request (keyword arguments, with messages copied) in .requests."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        item = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def _reply(content: str | None, tool_calls: list | None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def text_reply(content: str) -> SimpleNamespace:
    return _reply(content, None)


def tool_reply(call_id: str, name: str, args) -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return _reply(None, [call])


def shift_reply(call_id: str, data_hex: str) -> SimpleNamespace:
    return tool_reply(call_id, "shift_out", {"data_pin": DATA_PIN, "clock_pin": CLOCK_PIN, "latch_pin": LATCH_PIN,
                                             "group_size": GROUP_SIZE, "data_hex": data_hex})


def plan_reply(**plan) -> SimpleNamespace:
    return tool_reply("plan_1", "submit_plan", plan)


def verdict_reply(**verdict) -> SimpleNamespace:
    return tool_reply("verdict_1", "submit_verdict", verdict)


def make_bridge(tmp_path) -> Bridge:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return bridge


def make_ctx(tmp_path, should_stop=None) -> AgentContext:
    return AgentContext(bridge=make_bridge(tmp_path), skills=SkillStore(tmp_path / "library"),
                        should_stop=should_stop if should_stop is not None else (lambda: False))


def frame_hex(rows: list[str]) -> str:
    """Full-frame hex (registers 01..08 in order) for 8 row strings, '1' = lit, character 0 = leftmost."""
    return "".join(f"{reg:02X}{int(row if MSB_IS_LEFT else row[::-1], 2):02X}" for reg, row in enumerate(rows, 1))
