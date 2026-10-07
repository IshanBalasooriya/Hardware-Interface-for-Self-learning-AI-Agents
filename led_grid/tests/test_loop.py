import json
from types import SimpleNamespace

import pytest

from agent.loop import run_agent
from agent.tools import AgentContext
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport
from skills.store import SkillStore

HEART_HEX = "0100026603FF04FF057E063C07180800"
CHECKER_HEX = "01AA025503AA045505AA065507AA0855"


def call(call_id: str, name: str, args) -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def reply(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def shift(call_id: str, data_hex: str) -> SimpleNamespace:
    return call(call_id, "shift_out", {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                       "group_size": 2, "data_hex": data_hex})


class ScriptedClient:
    """Returns prepared replies in order; the last one repeats. Records every request."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def ctx(tmp_path) -> AgentContext:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return AgentContext(bridge=bridge, skills=SkillStore(tmp_path / "library"), should_stop=lambda: False)


def run(ctx, client, **kwargs):
    events: list[dict] = []
    result = run_agent("Draw a heart", ctx, events.append, client=client, model="test-model", **kwargs)
    return result, events


def test_text_only_reply_completes(ctx):
    client = ScriptedClient([reply("Nothing to do.")])
    result, events = run(ctx, client)
    assert result == {"status": "completed", "summary": "Nothing to do.", "error": None}
    assert events == [{"type": "agent_message", "text": "Nothing to do."}]
    request = client.requests[0]
    assert request["model"] == "test-model"
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert len(request["tools"]) == 8


def test_shift_out_then_text(ctx):
    client = ScriptedClient([reply(None, [shift("c1", HEART_HEX)]), reply("A heart is shown.")])
    result, events = run(ctx, client)
    assert result["status"] == "completed"
    assert result["summary"] == "A heart is shown."
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "agent_message"]
    assert events[0] == {"type": "tool_call", "call_id": "c1", "tool": "shift_out",
                         "args": json.loads(shift("c1", HEART_HEX).function.arguments)}
    assert events[1]["call_id"] == "c1"
    assert events[1]["result"]["success"] is True
    assert isinstance(events[1]["duration_ms"], int)
    second = client.requests[1]["messages"]
    assert second[2]["role"] == "assistant"
    assert second[2]["tool_calls"][0]["id"] == "c1"
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "c1"
    assert json.loads(second[3]["content"]) == events[1]["result"]


def test_two_tool_calls_in_one_message_run_in_order(ctx):
    client = ScriptedClient([reply("Drawing.", [shift("c1", HEART_HEX), shift("c2", CHECKER_HEX)]),
                             reply("Done.")])
    result, events = run(ctx, client)
    assert result["status"] == "completed"
    assert [(e["type"], e.get("call_id")) for e in events] == [
        ("agent_message", None), ("tool_call", "c1"), ("tool_result", "c1"),
        ("tool_call", "c2"), ("tool_result", "c2"), ("agent_message", None)]
    sent = [data.hex().upper() for _pins, _group, data in ctx.bridge.transport.device.sent[-2:]]
    assert sent == [HEART_HEX, CHECKER_HEX]


def test_save_skill_emits_skill_saved(ctx):
    definition = {"type": "action_sequence", "actions": [
        {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                       "group_size": 2, "data_hex": HEART_HEX}}]}
    client = ScriptedClient([reply(None, [call("c1", "save_skill", {"name": "symbol_heart",
                                                                   "definition": definition})]),
                             reply("Saved.")])
    result, events = run(ctx, client)
    assert result["status"] == "completed"
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "skill_saved", "agent_message"]
    assert events[2] == {"type": "skill_saved", "name": "symbol_heart", "version": 1}


def test_invalid_json_arguments_not_executed(ctx):
    sent_before = len(ctx.bridge.transport.device.sent)
    client = ScriptedClient([reply(None, [call("c1", "shift_out", '{"data_pin": 25,')]), reply("Sorry.")])
    result, events = run(ctx, client)
    assert result["status"] == "completed"
    assert len(ctx.bridge.transport.device.sent) == sent_before
    assert events[1]["result"]["success"] is False
    assert events[1]["result"]["error"].startswith("bad_args:")
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["error"].startswith("bad_args:")


def test_stop_before_second_turn(ctx):
    client = ScriptedClient([reply("Working.", [shift("c1", HEART_HEX)]), reply("Done.")])
    events: list[dict] = []
    ctx.should_stop = lambda: any(e["type"] == "tool_result" for e in events)
    result = run_agent("Draw a heart", ctx, events.append, client=client, model="m")
    assert result == {"status": "stopped", "summary": "Working.", "error": None}
    assert len(client.requests) == 1


def test_max_turns(ctx):
    client = ScriptedClient([reply(None, [call("c1", "read_shift_state", {})])])
    result, _events = run(ctx, client, max_turns=3)
    assert result["status"] == "max_turns"
    assert len(client.requests) == 3


def test_client_raises_returns_error(ctx):
    client = ScriptedClient([ConnectionError("proxy down")])
    result, events = run(ctx, client)
    assert result == {"status": "error", "summary": "", "error": "ConnectionError: proxy down"}
    assert events == []


def test_on_event_exceptions_are_ignored(ctx):
    def bad_listener(_event: dict) -> None:
        raise RuntimeError("listener broke")

    client = ScriptedClient([reply(None, [shift("c1", HEART_HEX)]), reply("Done.")])
    result = run_agent("Draw a heart", ctx, bad_listener, client=client, model="m")
    assert result["status"] == "completed"
