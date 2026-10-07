import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bridge.transport import FakeTransport, TransportError
from server.app import create_app
from skills.store import SkillStore

HEART_HEX = "0100026603FF04FF057E063C07180800"
HEART_ROWS = ["00000000", "01100110", "11111111", "11111111", "01111110", "00111100", "00011000", "00000000"]
MAP_FIELDS = {"seq", "timestamp", "display", "intensity", "rows", "bytes", "warnings"}
RUN_EVENTS = {"run_started", "agent_message", "tool_call", "tool_result", "skill_saved", "run_finished"}


def call(call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def shift(call_id: str, data_hex: str, **extra) -> SimpleNamespace:
    return call(call_id, "shift_out", {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                       "group_size": 2, "data_hex": data_hex, **extra})


class ScriptedClient:
    """Returns prepared replies in order; the last one repeats. Never calls a real LLM."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        self.calls += 1
        return self.replies[min(self.calls, len(self.replies)) - 1]


class FailingTransport(FakeTransport):
    def connect(self) -> None:
        raise TransportError("device unplugged")


HEART_RUN = [reply(None, [shift("c1", HEART_HEX)]), reply("A heart is shown.")]
SLOW_RUN = [reply(None, [call("c1", "wait", {"duration_ms": 10000})]), reply("Done.")]


@pytest.fixture
def make_server(tmp_path, monkeypatch):
    monkeypatch.delenv("RECORD_EVENTS", raising=False)

    def make(replies: list | None = None, transport=None) -> TestClient:
        app = create_app(port="fake", state_file=tmp_path / "shift_state.json",
                         frames_log=tmp_path / "shift_frames.jsonl", skills_dir=tmp_path / "library",
                         events_log=tmp_path / "sample_events.jsonl",
                         client=ScriptedClient(replies or [reply("Done.")]), transport=transport)
        return TestClient(app)
    return make


def read_run(ws) -> list[dict]:
    """Read events until the status that follows run_finished."""
    events: list[dict] = []
    while not (len(events) >= 2 and events[-2]["type"] == "run_finished" and events[-1]["type"] == "status"):
        events.append(ws.receive_json())
    return events


def test_status_connected_idle(make_server):
    with make_server() as client:
        assert client.get("/api/status").json() == {"connected": True, "busy": False, "run_id": None}


def test_shift_state_is_led_map(make_server):
    with make_server() as client:
        state = client.get("/api/shift_state").json()
    assert set(state) == MAP_FIELDS
    assert state["display"] == "on"
    assert len(state["rows"]) == 8


@pytest.mark.parametrize("prompt", ["", "   "])
def test_empty_prompt_400(make_server, prompt):
    with make_server() as client:
        response = client.post("/api/prompt", json={"prompt": prompt})
    assert response.status_code == 400
    assert response.json() == {"error": "empty_prompt"}


def test_prompt_too_long_400(make_server):
    with make_server() as client:
        response = client.post("/api/prompt", json={"prompt": "x" * 501})
    assert response.status_code == 400
    assert response.json() == {"error": "prompt_too_long"}


@pytest.mark.parametrize("intent", [None, "Draw the heart frame."])
def test_run_events_in_order(make_server, intent):
    extra = {"intent": intent} if intent else {}
    replies = [reply(None, [shift("c1", HEART_HEX, **extra)]), reply("A heart is shown.")]
    with make_server(replies) as client, client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        response = client.post("/api/prompt", json={"prompt": "  Draw a heart  "})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        events = [first] + read_run(ws)

    expected = ["status", "status", "run_started", "tool_call", "shift_state", "tool_result",
                "agent_message", "run_finished", "status"]
    if intent:
        expected.insert(3, "agent_message")
    assert [e["type"] for e in events] == expected
    assert run_id == "r_0001"
    assert all(isinstance(e["ts"], float) for e in events)
    assert all(e["run_id"] == run_id for e in events if e["type"] in RUN_EVENTS)
    assert events[0] == {"type": "status", "connected": True, "busy": False, "run_id": None, "ts": events[0]["ts"]}
    assert events[1]["busy"] is True and events[1]["run_id"] == run_id and events[1]["connected"] is True
    assert events[2]["prompt"] == "Draw a heart"
    if intent:
        assert events[3]["text"] == intent
    call_event = events[-6]
    assert call_event["tool"] == "shift_out" and "intent" not in call_event["args"]
    assert events[-5]["state"]["rows"] == HEART_ROWS
    assert events[-2]["status"] == "completed" and events[-2]["summary"] == "A heart is shown."
    assert events[-2]["error"] is None
    assert events[-1]["busy"] is False and events[-1]["run_id"] is None


def test_shift_state_and_frames_after_run(make_server):
    with make_server(HEART_RUN) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/prompt", json={"prompt": "Draw a heart"})
        read_run(ws)
        state = client.get("/api/shift_state").json()
        frames = client.get("/api/frames?count=1").json()["frames"]
        clamped = client.get("/api/frames?count=99").json()["frames"]
    assert state["rows"] == HEART_ROWS
    assert len(frames) == 1 and frames[0]["rows"] == HEART_ROWS
    assert frames[0]["seq"] == state["seq"]
    assert len(clamped) == 2  # start-up re-sync + heart


def test_second_prompt_while_busy_409(make_server):
    with make_server(SLOW_RUN) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post("/api/prompt", json={"prompt": "Wait"}).status_code == 202
        response = client.post("/api/prompt", json={"prompt": "Another"})
        assert response.status_code == 409
        assert response.json() == {"error": "run_in_progress"}
        assert client.get("/api/status").json()["busy"] is True
        client.post("/api/stop")
        read_run(ws)


def test_stop_during_slow_run(make_server):
    with make_server(SLOW_RUN) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/prompt", json={"prompt": "Wait"})
        while ws.receive_json()["type"] != "tool_call":
            pass
        assert client.post("/api/stop").json() == {"stopped": True}
        events = read_run(ws)
        assert client.get("/api/status").json()["busy"] is False
    finished = events[-2]
    assert finished["status"] == "stopped"
    assert finished["run_id"] == "r_0001"


def test_stop_when_idle(make_server):
    with make_server() as client:
        assert client.post("/api/stop").json() == {"stopped": True}


def test_skills_lists_saved_skill(make_server, tmp_path):
    with make_server() as client:
        SkillStore(tmp_path / "library").save("symbol_heart", {"type": "action_sequence", "actions": [
            {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                           "group_size": 2, "data_hex": HEART_HEX}}]})
        skills = client.get("/api/skills").json()["skills"]
    assert len(skills) == 1
    assert skills[0] == {"name": "symbol_heart", "description": "", "steps": 1, "version": 1,
                         "type": "action_sequence"}


def test_record_events(make_server, tmp_path, monkeypatch):
    monkeypatch.setenv("RECORD_EVENTS", "1")
    with make_server(HEART_RUN) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/prompt", json={"prompt": "Draw a heart"})
        events = read_run(ws)
    lines = (tmp_path / "sample_events.jsonl").read_text(encoding="utf-8").splitlines()
    recorded = [json.loads(line) for line in lines]
    assert recorded == events
    assert all("type" in e and "ts" in e for e in recorded)


def test_device_offline(make_server):
    with make_server(transport=FailingTransport()) as client:
        assert client.get("/api/status").json() == {"connected": False, "busy": False, "run_id": None}
        response = client.post("/api/prompt", json={"prompt": "Draw a heart"})
    assert response.status_code == 503
    assert response.json() == {"error": "device_offline"}


def test_placeholder_page(make_server):
    with make_server() as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard not installed" in response.text
    assert "/api/status" in response.text
