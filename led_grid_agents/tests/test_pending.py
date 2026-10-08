import pytest

from agent.tools import AgentContext, call_tool
from skills.pending import PendingSkillStore
from skills.store import SkillError, SkillStore
from tests.helpers import frame_hex, make_bridge

HEART_ROWS = ["00000000", "01100110", "11111111", "11111111",
              "01111110", "00111100", "00011000", "00000000"]
HEART_HEX = frame_hex(HEART_ROWS)
DOT_HEX = frame_hex(["10000000"] + ["00000000"] * 7)


def frame(data_hex: str) -> dict:
    return {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                         "group_size": 2, "data_hex": data_hex}}


def skill(data_hex: str = HEART_HEX, description: str = "Heart") -> dict:
    return {"type": "action_sequence", "description": description, "actions": [frame(data_hex)]}


@pytest.fixture
def real(tmp_path) -> SkillStore:
    return SkillStore(tmp_path / "library")


def files(store: SkillStore) -> list[str]:
    return sorted(p.name for p in store.directory.iterdir())


def test_save_writes_nothing_but_get_and_list_show_it(real):
    pending = PendingSkillStore(real)
    assert pending.save("symbol_heart", skill()) == {"name": "symbol_heart", "version": 1}
    assert files(real) == []
    assert pending.get("symbol_heart")["actions"] == [frame(HEART_HEX)]
    assert pending.list() == [{"name": "symbol_heart", "description": "Heart", "steps": 1,
                               "version": 1, "type": "action_sequence"}]
    assert real.list() == []


def test_invalid_definition_holds_nothing(real):
    pending = PendingSkillStore(real)
    with pytest.raises(SkillError):
        pending.save("symbol_bad", {"type": "action_sequence", "actions": []})
    assert pending.held() == []
    assert files(real) == []


@pytest.mark.parametrize("name", ["Bad", "", "a" * 41, "../x", 5])
def test_bad_name_holds_nothing(real, name):
    pending = PendingSkillStore(real)
    with pytest.raises(SkillError):
        pending.save(name, skill())
    assert pending.held() == []


def test_version_new_and_existing(real):
    real.save("symbol_heart", skill())
    real.save("symbol_heart", skill())
    pending = PendingSkillStore(real)
    assert pending.save("symbol_heart", skill(description="New heart")) == {"name": "symbol_heart", "version": 3}
    assert pending.save("symbol_dot", skill(DOT_HEX)) == {"name": "symbol_dot", "version": 1}
    results = pending.commit()
    assert results == [{"name": "symbol_heart", "version": 3}, {"name": "symbol_dot", "version": 1}]
    assert real.get("symbol_heart")["version"] == 3
    assert real.get("symbol_heart")["description"] == "New heart"
    assert real.get("symbol_dot")["version"] == 1


def test_saving_held_name_again_keeps_newer_last(real):
    pending = PendingSkillStore(real)
    pending.save("symbol_heart", skill())
    pending.save("symbol_dot", skill(DOT_HEX))
    pending.save("symbol_heart", skill(description="Second heart"))
    held = pending.held()
    assert [name for name, _ in held] == ["symbol_dot", "symbol_heart"]
    assert held[-1][1]["description"] == "Second heart"
    assert pending.last()[0] == "symbol_heart"


def test_list_held_replaces_real_once(real):
    real.save("symbol_heart", skill(description="Old heart"))
    real.save("symbol_dot", skill(DOT_HEX, "Dot"))
    pending = PendingSkillStore(real)
    pending.save("symbol_heart", skill(description="Held heart"))
    listing = pending.list()
    assert [entry["name"] for entry in listing] == ["symbol_dot", "symbol_heart"]
    heart = [entry for entry in listing if entry["name"] == "symbol_heart"]
    assert len(heart) == 1
    assert heart[0]["description"] == "Held heart"
    assert heart[0]["version"] == 2


def test_commit_writes_in_order_and_empties(real):
    pending = PendingSkillStore(real)
    pending.save("symbol_heart", skill())
    pending.save("symbol_dot", skill(DOT_HEX))
    assert pending.commit() == [{"name": "symbol_heart", "version": 1}, {"name": "symbol_dot", "version": 1}]
    assert files(real) == ["symbol_dot.json", "symbol_heart.json"]
    assert pending.held() == []
    assert pending.last() is None
    before = {p.name: p.read_bytes() for p in real.directory.iterdir()}
    assert pending.commit() == []
    assert {p.name: p.read_bytes() for p in real.directory.iterdir()} == before


def test_discard_writes_nothing(real):
    pending = PendingSkillStore(real)
    pending.save("symbol_heart", skill())
    pending.save("symbol_dot", skill(DOT_HEX))
    assert pending.discard() == ["symbol_heart", "symbol_dot"]
    assert pending.held() == []
    assert files(real) == []
    assert pending.list() == []


def test_tools_work_on_pending_store(tmp_path, real):
    bridge = make_bridge(tmp_path)
    ctx = AgentContext(bridge, PendingSkillStore(real), lambda: False)
    saved = call_tool("save_skill", {"name": "symbol_heart", "definition": skill()}, ctx)
    assert saved == {"success": True, "name": "symbol_heart", "version": 1}
    listed = call_tool("list_skills", {}, ctx)
    assert [entry["name"] for entry in listed["skills"]] == ["symbol_heart"]
    got = call_tool("get_skill", {"name": "symbol_heart"}, ctx)
    assert got["success"] and got["definition"]["actions"] == [frame(HEART_HEX)]
    sent_before = len(bridge.transport.device.sent)
    played = call_tool("reuse_skill", {"skill_name": "symbol_heart"}, ctx)
    assert played["success"] is True
    assert played["final_state"]["rows"] == HEART_ROWS
    assert [data.hex().upper() for _p, _g, data in bridge.transport.device.sent[sent_before:]] == [HEART_HEX]
    assert files(real) == []


def test_held_definition_is_a_copy(real):
    pending = PendingSkillStore(real)
    definition = skill()
    pending.save("symbol_heart", definition)
    definition["actions"][0]["args"]["data_hex"] = DOT_HEX
    definition["description"] = "Changed"
    held = pending.get("symbol_heart")
    assert held["actions"][0]["args"]["data_hex"] == HEART_HEX
    assert held["description"] == "Heart"
    held["description"] = "Changed via get"
    assert pending.get("symbol_heart")["description"] == "Heart"
