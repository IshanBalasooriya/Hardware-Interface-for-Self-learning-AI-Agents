"""Stage 5 Part B: the agent with sight. Fake device, a camera that follows the fake chip, scripted LLM clients."""

import json

import pytest

import config
from agent.loop import run_agent
from agent.prompts import CAMERA_SECTION, build_system_prompt
from agent.tools import TOOL_SCHEMAS, AgentContext, call_tool, tool_schemas
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport
from skills.store import SkillStore
from tests.led_grid.test_loop import HEART_HEX, ScriptedClient, call, reply, shift
from tests.integration_fakes import COVERED, HEART, ROOM, ChipCamera, write_calibration
from vision.ledmap import hex_to_rows
from vision_service import VisionService

LED_MAP_KEYS = ["seq", "timestamp", "display", "intensity", "rows", "bytes", "warnings"]
CHECKER_HEX = "01AA025503AA045505AA065507AA0855"


@pytest.fixture(scope="module")
def cal_file(tmp_path_factory):
    return write_calibration(tmp_path_factory.mktemp("vision") / "calibration.json")


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(config, "VISION_SETTLE_MS", 0)
    monkeypatch.setattr(config, "VISION_FLUSH_FRAMES", 0)
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 2)
    monkeypatch.setattr(config, "VISION_REQUIRE_MATCH_FOR_SAVE", "1")


class Rig:
    def __init__(self, tmp_path, cal_file, enabled=True, camera_factory=None, **cam_kw) -> None:
        self.log: list[str] = []
        self.transport = FakeTransport()
        self.store = GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl")
        self.notified: list[dict] = []
        self.store.add_listener(self.notified.append)
        self.bridge = Bridge(self.transport, self.store)
        self.bridge.start()
        self.camera = ChipCamera(self.transport.device, self.log, **cam_kw)
        self.vision = VisionService(enabled, camera_factory or (lambda: self.camera), cal_file,
                                    debug_dir=tmp_path / "debug")
        self.vision.start()
        self.library = tmp_path / "library"
        self.ctx = AgentContext(bridge=self.bridge, skills=SkillStore(self.library), should_stop=lambda: False,
                                vision=self.vision)
        shift_out = self.bridge.shift_out

        def logged_shift_out(*a, **kw):
            self.log.append("shift")
            return shift_out(*a, **kw)

        self.bridge.shift_out = logged_shift_out

    def tool(self, name, args=None):
        return call_tool(name, args or {}, self.ctx)

    def show(self, data_hex):
        return self.tool("shift_out", {"data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2,
                                       "data_hex": data_hex})

    def save(self, name="symbol_heart"):
        return self.tool("save_skill", {"name": name, "definition": {
            "type": "action_sequence", "description": "Heart",
            "actions": [{"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                                       "group_size": 2, "data_hex": HEART_HEX}}]}})

    def saved_files(self):
        return sorted(p.name for p in self.library.glob("*.json")) if self.library.exists() else []


@pytest.fixture
def rig(tmp_path, cal_file):
    return Rig(tmp_path, cal_file)


# ---------------------------------------------------------------- VISION_ENABLED=0

def test_disabled_is_exactly_led_grid(tmp_path, cal_file):
    for vision in (None, VisionService(False)):
        r = Rig(tmp_path / str(vision is None), cal_file, enabled=False)
        r.ctx.vision = vision
        result = r.show(HEART_HEX)
        assert result["success"] and set(result) == {"success", "raw_response", "decoded_state"}
        assert tool_schemas(r.ctx) is TOOL_SCHEMAS
        assert r.tool("read_observed_state") == {"success": False, "error": "unknown_tool"}
        saved = r.save()
        assert saved["success"] and "vision_note" not in saved
        assert "grab" not in r.log
    assert CAMERA_SECTION not in build_system_prompt()
    assert build_system_prompt() == build_system_prompt(vision=False)


def test_vision_config_defaults():
    assert config.VISION_ENABLED in ("0", "1")
    assert config.VISION_REQUIRE_MATCH_FOR_SAVE == "1"


# ---------------------------------------------------------------- prompt and schemas

def test_prompt_section_only_with_vision(rig):
    prompt = build_system_prompt(vision=True)
    assert CAMERA_SECTION in prompt and prompt.index(CAMERA_SECTION) < prompt.index("## Finish")
    assert len(CAMERA_SECTION.splitlines()) <= 15
    for word in ("observed_state", "physical_check", "read_observed_state", "not camera-confirmed"):
        assert word in CAMERA_SECTION
    assert prompt.replace(CAMERA_SECTION + "\n\n", "") == build_system_prompt()
    names = [s["function"]["name"] for s in tool_schemas(rig.ctx)]
    assert names == [s["function"]["name"] for s in TOOL_SCHEMAS] + ["read_observed_state"]


# ---------------------------------------------------------------- the agent draws

def test_scripted_agent_draws_heart_match(rig):
    client = ScriptedClient([reply(None, [shift("c1", HEART_HEX)]), reply("A heart is shown.")])
    events = []
    result = run_agent("Draw a heart", rig.ctx, events.append, client=client, model="test-model")
    assert result["status"] == "completed"
    tool_result = next(e for e in events if e["type"] == "tool_result")["result"]
    assert tool_result["physical_check"]["result"] == "match"
    assert tool_result["observed_state"]["rows"] == HEART == tool_result["decoded_state"]["rows"]
    assert tool_result["observed_state"]["source"] == "camera"
    request = client.requests[0]
    assert CAMERA_SECTION in request["messages"][0]["content"]
    assert len(request["tools"]) == len(TOOL_SCHEMAS) + 1
    assert json.loads(client.requests[1]["messages"][-1]["content"])["physical_check"]["result"] == "match"


def test_match_then_save_is_plain(rig):
    assert rig.show(HEART_HEX)["physical_check"]["result"] == "match"
    saved = rig.save()
    assert saved["success"] and "vision_note" not in saved
    assert rig.saved_files() == ["symbol_heart.json"]


def test_failed_command_gets_no_observation(rig):
    bad = rig.tool("shift_out", {"data_pin": 4, "clock_pin": 26, "latch_pin": 27, "group_size": 2,
                                 "data_hex": HEART_HEX})
    assert not bad["success"] and "observed_state" not in bad and "physical_check" not in bad
    assert "grab" not in rig.log


# ---------------------------------------------------------------- faults

def test_occlusion_mismatch_and_save_refused(tmp_path, cal_file):
    r = Rig(tmp_path, cal_file, occluded=COVERED)
    result = r.show(HEART_HEX)
    check = result["physical_check"]
    assert result["decoded_state"]["rows"] == HEART
    assert check["result"] == "mismatch"
    assert {(m["row"], m["col"]) for m in check["mismatches"]} == COVERED
    assert all(m["commanded"] == "1" and m["observed"] == "0" for m in check["mismatches"])
    saved = r.save()
    assert not saved["success"] and saved["error"].startswith("save_refused") and "mismatch" in saved["error"]
    assert r.saved_files() == []


def test_blocked_not_visible_and_save_refused(tmp_path, cal_file):
    r = Rig(tmp_path, cal_file, blocked=True)
    assert r.show(HEART_HEX)["physical_check"]["result"] == "not_visible"
    saved = r.save()
    assert not saved["success"] and "not_visible" in saved["error"]
    assert r.saved_files() == []


def test_gate_can_be_turned_off(tmp_path, cal_file, monkeypatch):
    monkeypatch.setattr(config, "VISION_REQUIRE_MATCH_FOR_SAVE", "0")
    r = Rig(tmp_path, cal_file, occluded=COVERED)
    assert r.show(HEART_HEX)["physical_check"]["result"] == "mismatch"
    saved = r.save()
    assert saved["success"] and "not camera-confirmed" in saved["vision_note"]


def test_fixing_the_fault_unblocks_the_save(tmp_path, cal_file):
    r = Rig(tmp_path, cal_file, occluded=COVERED)
    assert r.show(HEART_HEX)["physical_check"]["result"] == "mismatch"
    r.camera.occluded = set()
    assert r.tool("read_observed_state")["physical_check"]["result"] == "match"
    assert r.save()["success"]


def test_clear_display_consistent_dark(rig):
    rig.show(HEART_HEX)
    result = rig.show(config.CLEAR_HEX)
    assert result["physical_check"]["result"] == "consistent_dark"
    assert result["observed_state"]["vision"]["status"] == "dark"


def test_camera_factory_raising(tmp_path, cal_file):
    def broken():
        raise RuntimeError("no camera")

    r = Rig(tmp_path, cal_file, camera_factory=broken)
    assert r.vision.status() == {"enabled": True, "camera": "error", "calibrated": False,
                                 "calibration_created": None, "last_status": None, "position_ok": None,
                                 "preview_active": False, "operation": None}
    result = r.show(HEART_HEX)
    assert result["success"] and result["decoded_state"]["rows"] == HEART
    assert result["physical_check"]["result"] == "unavailable"
    assert result["observed_state"]["vision"]["status"] == "camera_error"
    saved = r.save()
    assert saved["success"] and "not camera-confirmed" in saved["vision_note"]
    assert r.saved_files() == ["symbol_heart.json"]
    client = ScriptedClient([reply(None, [shift("c1", HEART_HEX)]), reply("Shown; camera unavailable.")])
    assert run_agent("Draw a heart", r.ctx, lambda e: None, client=client, model="m")["status"] == "completed"


def test_camera_failing_mid_run(rig):
    def fail(n):
        raise OSError("unplugged")

    rig.camera.grab = fail
    result = rig.show(HEART_HEX)
    assert result["success"] and result["physical_check"]["result"] == "unavailable"


def test_uncalibrated_is_unavailable(tmp_path):
    r = Rig(tmp_path, tmp_path / "missing.json")
    assert r.vision.status()["calibrated"] is False
    assert r.show(HEART_HEX)["physical_check"]["result"] == "unavailable"


# ---------------------------------------------------------------- reuse_skill and read_observed_state

def test_reuse_skill_one_observation_after_the_end(rig):
    frames = [{"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2,
                                             "data_hex": h}} for h in (CHECKER_HEX, HEART_HEX)]
    definition = {"type": "action_sequence", "description": "Checker then heart, twice", "actions": [
        {"tool": "repeat", "args": {"count": 2, "actions": [
            frames[0], {"tool": "wait", "args": {"duration_ms": 10}},
            frames[1], {"tool": "wait", "args": {"duration_ms": 10}}]}}]}
    rig.ctx.skills.save("anim_checker_heart", definition)
    rig.log.clear()
    observed = []
    observe = rig.vision.observe
    rig.vision.observe = lambda commanded: observed.append(commanded) or observe(commanded)
    result = rig.tool("reuse_skill", {"skill_name": "anim_checker_heart"})
    assert result["success"] and result["steps_run"] == 8
    assert len(observed) == 1 and observed[0] == result["final_state"]
    # The runner made no camera calls: every grab (one read = flush + grab) comes after the last shift_out
    assert rig.log[:4] == ["shift"] * 4 and rig.log[4:] and set(rig.log[4:]) == {"grab"}
    assert result["physical_check"]["result"] == "match" and result["observed_state"]["rows"] == HEART


def test_read_observed_state_is_read_only(rig):
    rig.show(HEART_HEX)
    before, frames_before = rig.store.current(), rig.store.recent(30)
    sent_before = len(rig.transport.device.sent)
    result = rig.tool("read_observed_state")
    assert result["success"] and result["physical_check"]["result"] == "match"
    assert result["observed_state"]["rows"] == HEART and "decoded_state" not in result
    assert rig.store.current() == before and rig.store.recent(30) == frames_before
    assert len(rig.transport.device.sent) == sent_before
    second = rig.tool("read_observed_state")
    assert second["observed_state"]["seq"] == result["observed_state"]["seq"] + 1  # a fresh read each time
    assert rig.tool("read_observed_state", {"x": 1})["error"].startswith("bad_args")


# ---------------------------------------------------------------- invariant

def test_grid_store_holds_only_commanded_states(tmp_path, cal_file):
    r = Rig(tmp_path, cal_file)
    steps = [
        reply(None, [shift("c1", HEART_HEX)]),
        reply(None, [call("c2", "read_observed_state", {})]),
        reply(None, [shift("c3", CHECKER_HEX)]),
        reply(None, [shift("c4", HEART_HEX)]),
        reply(None, [call("c5", "save_skill", {"name": "symbol_heart", "definition": {
            "type": "action_sequence", "actions": [{"tool": "shift_out", "args": {
                "data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2, "data_hex": HEART_HEX}}]}})]),
        reply(None, [shift("c6", config.CLEAR_HEX)]),
        reply("Done."),
    ]
    faults = {"c3": dict(occluded=COVERED), "c4": dict(blocked=True), "c6": dict(blocked=False, occluded=set())}
    events = []

    def on_event(e):
        events.append(e)
        if e["type"] == "tool_call" and e["call_id"] in faults:
            for k, v in faults[e["call_id"]].items():
                setattr(r.camera, k, set(v) if k == "occluded" else v)

    result = run_agent("Draw things", r.ctx, on_event, client=ScriptedClient(steps), model="m")
    assert result["status"] == "completed"
    checks = [e["result"]["physical_check"]["result"] for e in events
              if e["type"] == "tool_result" and "physical_check" in e["result"]]
    assert checks == ["match", "match", "mismatch", "not_visible", "consistent_dark"]
    save = next(e["result"] for e in events if e["type"] == "tool_result" and e["tool"] == "save_skill")
    assert not save["success"] and r.saved_files() == []

    # Every map GridStore produced (current, frame log, listeners) is a commanded state:
    # led_grid's seven keys only, and rows equal to the bytes the device accepted.
    frames = r.store.recent(30)
    assert frames and r.notified
    rows = None
    replay = []
    for _pins, _group, payload in r.transport.device.sent:
        rows = hex_to_rows(payload.hex(), rows)
        replay.append(list(rows))
    for m in frames + r.notified + [r.store.current()]:
        assert list(m) == LED_MAP_KEYS and "source" not in m and "vision" not in m
    assert [f["rows"] for f in frames] == replay[-len(frames):]
    assert r.store.current()["rows"] == ["00000000"] * 8
    state = json.loads((tmp_path / "shift_state.json").read_text(encoding="utf-8"))
    assert "source" not in json.dumps(state) and "camera" not in json.dumps(state)


# ---------------------------------------------------------------- VisionService

def _show(r):
    from vision.ledmap import rows_to_hex

    def show(rows):
        result = r.bridge.shift_out(25, 26, 27, 2, rows_to_hex(rows))
        if not result["success"]:
            raise RuntimeError(result["error"])
    return show


def test_service_calibrate_saves_and_reloads(tmp_path):
    path = tmp_path / "cal" / "calibration.json"
    r = Rig(tmp_path, path)
    assert r.vision.status()["calibrated"] is False
    result = r.vision.calibrate(_show(r))
    assert result["success"] and result["summary"].startswith("Calibration OK"), result
    assert path.exists()
    status = r.vision.status()
    assert status["calibrated"] and status["camera"] == "ok" and status["calibration_created"]
    r.bridge.resync()
    r.show(HEART_HEX)
    assert r.vision.observe(r.store.current())["physical_check"]["result"] == "match"
    assert r.vision.status()["last_status"] == "ok"
    position = r.vision.check_position(_show(r))
    assert position["ok"] and position["max_corner_shift_px"] < 1.0


def test_service_without_camera_never_raises(tmp_path, cal_file):
    def broken():
        raise RuntimeError("no camera")

    v = VisionService(True, broken, cal_file)
    v.start()
    assert v.calibrate(lambda rows: None)["success"] is False
    assert v.check_position(lambda rows: None)["ok"] is False
    assert v.observe(None)["physical_check"]["result"] == "unavailable"
    v.stop()
    off = VisionService(False, broken, cal_file)
    off.start()
    assert off.status()["camera"] == "off"
    assert off.observe({"rows": HEART, "display": "on"})["observed_state"]["vision"]["status"] == "disabled"


def test_service_stop_closes_camera(rig):
    closed = []
    rig.camera.close = lambda: closed.append(True)
    rig.vision.stop()
    assert closed and rig.vision.status()["camera"] == "off"
    assert rig.vision.observe(None)["physical_check"]["result"] == "unavailable"


def test_service_serialises_camera_operations(rig):
    import threading

    active, overlap = [], []
    grab = rig.camera.grab

    def slow_grab(n):
        if active:
            overlap.append(True)
        active.append(1)
        try:
            return grab(n)
        finally:
            active.pop()

    rig.camera.grab = slow_grab
    threads = [threading.Thread(target=rig.vision.observe, args=(rig.store.current(),)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not overlap
