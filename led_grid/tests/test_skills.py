import time

import pytest

from bridge.bridge import Bridge
from bridge.grid_model import Max7219Model
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport
from skills.runner import run_skill
from skills.store import SkillError, SkillStore, validate

HEART_HEX = "0100026603FF04FF057E063C07180800"
CHECKER_HEX = "01AA025503AA045505AA065507AA0855"
FULL_HEX = "01FF02FF03FF04FF05FF06FF07FF08FF"


def frame(data_hex: str, data_pin: object = 25) -> dict:
    return {"tool": "shift_out", "args": {"data_pin": data_pin, "clock_pin": 26, "latch_pin": 27,
                                         "group_size": 2, "data_hex": data_hex}}


def wait(ms: object) -> dict:
    return {"tool": "wait", "args": {"duration_ms": ms}}


def repeat(count: object, actions: list) -> dict:
    return {"tool": "repeat", "args": {"count": count, "actions": actions}}


def skill(actions: list, **extra) -> dict:
    return {"type": "action_sequence", "actions": actions, **extra}


def rows_of(data_hex: str) -> list[str]:
    model = Max7219Model()
    model.apply(bytes.fromhex(data_hex), 2)
    return model.picture()["rows"]


def make_bridge(tmp_path) -> Bridge:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return bridge


def sent_hex(bridge: Bridge, since: int) -> list[str]:
    return [data.hex().upper() for _pins, _group, data in bridge.transport.device.sent[since:]]


def log_lines(bridge: Bridge) -> int:
    return len(bridge.store.log_path.read_text(encoding="utf-8").splitlines())


def test_save_get_version(tmp_path):
    store = SkillStore(tmp_path / "library")
    definition = skill([frame(HEART_HEX)], description="Heart")
    assert store.save("symbol_heart", definition) == {"name": "symbol_heart", "version": 1}
    saved = store.get("symbol_heart")
    assert saved["version"] == 1
    assert saved["actions"] == definition["actions"]
    assert store.save("symbol_heart", definition) == {"name": "symbol_heart", "version": 2}
    assert store.get("symbol_heart")["version"] == 2
    with pytest.raises(SkillError):
        store.get("missing")


def test_list(tmp_path):
    store = SkillStore(tmp_path / "library")
    store.save("symbol_heart", skill([frame(HEART_HEX), wait(100), repeat(2, [frame(FULL_HEX)])],
                                     description="Heart"))
    (tmp_path / "library" / "broken.json").write_text("{not json", encoding="utf-8")
    assert store.list() == [{"name": "symbol_heart", "description": "Heart", "steps": 3,
                             "version": 1, "type": "action_sequence"}]


def nested(depth: int) -> list:
    actions = [frame(HEART_HEX)]
    for _ in range(depth):
        actions = [repeat(2, actions)]
    return actions


BAD_DEFINITIONS = {
    "wrong_type": skill([frame(HEART_HEX)]) | {"type": "script"},
    "missing_type": {"actions": [frame(HEART_HEX)]},
    "actions_empty": skill([]),
    "actions_not_list": skill(frame(HEART_HEX)),
    "action_missing_args": skill([{"tool": "wait"}]),
    "action_extra_key": skill([wait(10) | {"note": "x"}]),
    "args_not_dict": skill([{"tool": "wait", "args": [10]}]),
    "shift_out_missing_arg": skill([{"tool": "shift_out", "args": {"data_pin": 25, "data_hex": "0100"}}]),
    "shift_out_pin_not_int": skill([frame(HEART_HEX, data_pin="25")]),
    "shift_out_pin_bool": skill([frame(HEART_HEX, data_pin=True)]),
    "data_hex_odd": skill([frame("018")]),
    "data_hex_not_hex": skill([frame("01G0")]),
    "data_hex_empty": skill([frame("")]),
    "wait_negative": skill([wait(-1)]),
    "wait_too_long": skill([wait(10001)]),
    "wait_not_int": skill([wait(1.5)]),
    "repeat_count_zero": skill([repeat(0, [frame(HEART_HEX)])]),
    "repeat_count_too_big": skill([repeat(1001, [frame(HEART_HEX)])]),
    "repeat_body_empty": skill([repeat(2, [])]),
    "repeat_too_deep": skill(nested(4)),
    "description_too_long": skill([frame(HEART_HEX)], description="x" * 201),
    "description_not_string": skill([frame(HEART_HEX)], description=5),
    "params_not_dict": skill([frame(HEART_HEX)], params=[1]),
    "params_bad_value": skill([frame(HEART_HEX)], params={"n": 1.5}),
    "param_without_default": skill([repeat("$n", [frame(HEART_HEX)])]),
    "unknown_top_level_key": skill([frame(HEART_HEX)], code="print(1)"),
}


@pytest.mark.parametrize("definition", BAD_DEFINITIONS.values(), ids=BAD_DEFINITIONS.keys())
def test_validation_rejects(tmp_path, definition):
    with pytest.raises(SkillError):
        validate(definition)
    with pytest.raises(SkillError):
        SkillStore(tmp_path / "library").save("bad_skill", definition)
    assert SkillStore(tmp_path / "library").list() == []


@pytest.mark.parametrize("name", ["", "Heart", "has-dash", "a" * 41, "../escape"])
def test_bad_name_rejected(tmp_path, name):
    store = SkillStore(tmp_path / "library")
    with pytest.raises(SkillError):
        store.save(name, skill([frame(HEART_HEX)]))


def test_valid_edge_cases_accepted():
    validate(skill(nested(3), params={"n": 2, "hex": HEART_HEX}, description="x" * 200, version=7))
    validate(skill([frame("$hex"), wait("$ms"), repeat("$n", [wait(0)])],
                   params={"hex": HEART_HEX, "ms": 10, "n": 1}))


@pytest.mark.parametrize("tool", ["reuse_skill", "run_python", "read_shift_state"])
def test_other_tools_rejected(tool):
    with pytest.raises(SkillError):
        validate(skill([{"tool": tool, "args": {"skill_name": "symbol_heart"}}]))
    with pytest.raises(SkillError):
        validate(skill([repeat(2, [{"tool": tool, "args": {}}])]))


def test_three_frames_in_order(tmp_path):
    bridge = make_bridge(tmp_path)
    before = len(bridge.transport.device.sent)
    result = run_skill(skill([frame(HEART_HEX), frame(CHECKER_HEX), frame(FULL_HEX)]), bridge)
    assert result["success"] is True
    assert result["steps_run"] == 3
    assert result["stopped"] is False and result["timed_out"] is False
    assert sent_hex(bridge, before) == [HEART_HEX, CHECKER_HEX, FULL_HEX]
    assert result["final_state"]["rows"] == rows_of(FULL_HEX)


def test_repeat_with_param(tmp_path):
    bridge = make_bridge(tmp_path)
    definition = skill([repeat("$n", [frame(HEART_HEX)])], params={"n": 2})

    before = len(bridge.transport.device.sent)
    assert run_skill(definition, bridge, {"n": 4})["steps_run"] == 4
    assert sent_hex(bridge, before) == [HEART_HEX] * 4

    before = len(bridge.transport.device.sent)
    assert run_skill(definition, bridge)["steps_run"] == 2
    assert sent_hex(bridge, before) == [HEART_HEX] * 2

    before = len(bridge.transport.device.sent)
    for bad in ({"n": "x"}, {"n": 0}, {"n": True}):
        assert run_skill(definition, bridge, bad) == {"success": False, "error": "bad_params"}
    assert len(bridge.transport.device.sent) == before


def test_seq_and_frame_log(tmp_path):
    bridge = make_bridge(tmp_path)
    seq_before, lines_before = bridge.store.current()["seq"], log_lines(bridge)
    result = run_skill(skill([frame(HEART_HEX), wait(0), frame(CHECKER_HEX), frame(FULL_HEX)]), bridge)
    assert result["steps_run"] == 4
    assert result["final_state"]["seq"] == seq_before + 3
    assert log_lines(bridge) == lines_before + 3
    assert [f["seq"] for f in bridge.store.recent(3)] == [seq_before + 1, seq_before + 2, seq_before + 3]


def test_should_stop_mid_run(tmp_path):
    bridge = make_bridge(tmp_path)
    definition = skill([repeat(5, [frame(HEART_HEX), wait(10), frame(FULL_HEX), wait(10)])])
    full = run_skill(definition, bridge)["steps_run"]
    assert full == 20

    start_seq = bridge.store.current()["seq"]
    result = run_skill(definition, bridge, should_stop=lambda: bridge.store.current()["seq"] >= start_seq + 3)
    assert result["success"] is True
    assert result["stopped"] is True and result["timed_out"] is False
    assert result["steps_run"] < full
    assert result["final_state"]["seq"] == start_seq + 3


def test_time_cap(tmp_path):
    bridge = make_bridge(tmp_path)
    t0 = time.monotonic()
    result = run_skill(skill([wait(10000), wait(10000), wait(10000)]), bridge, time_cap_s=0.3)
    assert time.monotonic() - t0 < 1.0
    assert result["success"] is True
    assert result["timed_out"] is True and result["stopped"] is False
    assert result["steps_run"] < 3


def test_disallowed_pin_stops_run(tmp_path):
    bridge = make_bridge(tmp_path)
    definition = skill([frame(HEART_HEX), frame(FULL_HEX, data_pin=5), frame(CHECKER_HEX)])
    before = len(bridge.transport.device.sent)
    result = run_skill(definition, bridge)
    assert result["success"] is False
    assert "PIN_NOT_ALLOWED" in result["error"]
    assert result["steps_run"] == 1
    assert result["final_state"]["rows"] == rows_of(HEART_HEX)
    assert bridge.store.current()["rows"] == rows_of(HEART_HEX)
    assert len(bridge.transport.device.sent) == before + 1
