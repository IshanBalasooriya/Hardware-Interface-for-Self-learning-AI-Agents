import pytest

from agent.tools import TOOL_SCHEMAS, AgentContext, call_tool
from bridge.bridge import Bridge
from bridge.grid_model import Max7219Model
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport
from skills.store import SkillStore

HEART_HEX = "0100026603FF04FF057E063C07180800"
CHECKER_HEX = "01AA025503AA045505AA065507AA0855"


def rows_of(data_hex: str) -> list[str]:
    model = Max7219Model()
    model.apply(bytes.fromhex(data_hex), 2)
    return model.picture()["rows"]


def frame(data_hex: str) -> dict:
    return {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                         "group_size": 2, "data_hex": data_hex}}


def shift_args(data_hex: str, data_pin: object = 25) -> dict:
    return {"data_pin": data_pin, "clock_pin": 26, "latch_pin": 27, "group_size": 2, "data_hex": data_hex}


@pytest.fixture
def ctx(tmp_path) -> AgentContext:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return AgentContext(bridge=bridge, skills=SkillStore(tmp_path / "library"), should_stop=lambda: False)


def test_schemas_match_tool_list():
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert names == ["shift_out", "wait", "read_shift_state", "read_recent_frames",
                     "list_skills", "get_skill", "save_skill", "reuse_skill"]
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"


def test_shift_out_success(ctx):
    result = call_tool("shift_out", shift_args(HEART_HEX), ctx)
    assert result["success"] is True
    assert result["decoded_state"]["rows"] == rows_of(HEART_HEX)


def test_shift_out_pin_not_allowed(ctx):
    seq = ctx.bridge.store.current()["seq"]
    result = call_tool("shift_out", shift_args(HEART_HEX, data_pin=4), ctx)
    assert result == {"success": False, "error": "ERR PIN_NOT_ALLOWED", "raw_response": "ERR PIN_NOT_ALLOWED"}
    assert ctx.bridge.store.current()["seq"] == seq


@pytest.mark.parametrize("args, detail", [
    ({k: v for k, v in shift_args(HEART_HEX).items() if k != "data_hex"}, "missing argument 'data_hex'"),
    (shift_args(HEART_HEX, data_pin="25"), "data_pin must be int"),
    (shift_args(HEART_HEX, data_pin=True), "data_pin must be int"),
    ({**shift_args(HEART_HEX), "extra": 1}, "unexpected argument 'extra'"),
])
def test_shift_out_bad_args(ctx, args, detail):
    assert call_tool("shift_out", args, ctx) == {"success": False, "error": f"bad_args: {detail}"}


def test_wait_success_and_stop(ctx):
    assert call_tool("wait", {"duration_ms": 10}, ctx) == {"success": True}
    ctx.should_stop = lambda: True
    assert call_tool("wait", {"duration_ms": 5000}, ctx) == {"success": True, "stopped": True}


def test_wait_bad_args(ctx):
    assert call_tool("wait", {"duration_ms": "100"}, ctx) == {"success": False,
                                                              "error": "bad_args: duration_ms must be int"}


def test_read_shift_state(ctx):
    call_tool("shift_out", shift_args(HEART_HEX), ctx)
    result = call_tool("read_shift_state", {}, ctx)
    assert result["success"] is True
    assert result["state"] == ctx.bridge.store.current()
    assert call_tool("read_shift_state", {"x": 1}, ctx)["error"] == "bad_args: unexpected argument 'x'"


def test_read_recent_frames(ctx):
    call_tool("shift_out", shift_args(HEART_HEX), ctx)
    call_tool("shift_out", shift_args(CHECKER_HEX), ctx)
    result = call_tool("read_recent_frames", {"count": 2}, ctx)
    assert result["success"] is True
    assert [f["rows"] for f in result["frames"]] == [rows_of(HEART_HEX), rows_of(CHECKER_HEX)]


@pytest.mark.parametrize("count", [0, 31, "2"])
def test_read_recent_frames_bad_count(ctx, count):
    result = call_tool("read_recent_frames", {"count": count}, ctx)
    assert result["success"] is False
    assert result["error"].startswith("bad_args:")


def test_list_skills(ctx):
    assert call_tool("list_skills", {}, ctx) == {"success": True, "skills": []}
    ctx.skills.save("symbol_heart", {"type": "action_sequence", "description": "Heart",
                                     "actions": [frame(HEART_HEX)]})
    skills = call_tool("list_skills", {}, ctx)["skills"]
    assert [(s["name"], s["description"], s["steps"], s["version"]) for s in skills] == \
        [("symbol_heart", "Heart", 1, 1)]


def test_list_skills_bad_args(ctx):
    assert call_tool("list_skills", None, ctx) == {"success": False,
                                                   "error": "bad_args: arguments must be an object"}


def test_get_skill(ctx):
    ctx.skills.save("symbol_heart", {"type": "action_sequence", "actions": [frame(HEART_HEX)]})
    result = call_tool("get_skill", {"name": "symbol_heart"}, ctx)
    assert result["success"] is True
    assert result["definition"]["actions"] == [frame(HEART_HEX)]


def test_get_skill_missing(ctx):
    result = call_tool("get_skill", {"name": "symbol_nothing"}, ctx)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_save_skill(ctx):
    definition = {"type": "action_sequence", "actions": [frame(HEART_HEX)]}
    assert call_tool("save_skill", {"name": "symbol_heart", "definition": definition}, ctx) == \
        {"success": True, "name": "symbol_heart", "version": 1}
    assert call_tool("save_skill", {"name": "symbol_heart", "definition": definition}, ctx)["version"] == 2


def test_save_skill_invalid(ctx):
    definition = {"type": "action_sequence", "actions": [{"tool": "reuse_skill", "args": {}}]}
    result = call_tool("save_skill", {"name": "symbol_heart", "definition": definition}, ctx)
    assert result["success"] is False
    assert "tool" in result["error"]
    assert ctx.skills.list() == []


def test_reuse_skill_plays_two_frames(ctx):
    ctx.skills.save("anim_two", {"type": "action_sequence",
                                 "actions": [frame(HEART_HEX), {"tool": "wait", "args": {"duration_ms": 1}},
                                             frame(CHECKER_HEX)]})
    seq = ctx.bridge.store.current()["seq"]
    result = call_tool("reuse_skill", {"skill_name": "anim_two"}, ctx)
    assert result["success"] is True
    assert result["steps_run"] == 3
    assert result["stopped"] is False and result["timed_out"] is False
    assert result["final_state"]["rows"] == rows_of(CHECKER_HEX)
    assert result["final_state"]["seq"] == seq + 2


def test_reuse_skill_with_params(ctx):
    ctx.skills.save("pattern_any", {"type": "action_sequence", "params": {"hex": HEART_HEX},
                                    "actions": [{"tool": "shift_out", "args": {
                                        "data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                        "group_size": 2, "data_hex": "$hex"}}]})
    result = call_tool("reuse_skill", {"skill_name": "pattern_any", "params": {"hex": CHECKER_HEX}}, ctx)
    assert result["final_state"]["rows"] == rows_of(CHECKER_HEX)


def test_reuse_skill_missing(ctx):
    result = call_tool("reuse_skill", {"skill_name": "anim_none"}, ctx)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_unknown_tool(ctx):
    assert call_tool("draw_text", {"text": "hi"}, ctx) == {"success": False, "error": "unknown_tool"}


def test_tool_that_raises_returns_error(ctx, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(ctx.bridge.store, "current", boom)
    assert call_tool("read_shift_state", {}, ctx) == {"success": False, "error": "RuntimeError: disk on fire"}
