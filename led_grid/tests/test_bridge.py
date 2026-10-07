import time

import pytest

from bridge.bridge import Bridge
from bridge.fake_device import FakeDevice
from bridge.grid_model import Max7219Model
from bridge.grid_store import GridStore
from bridge.transport import FakeTransport, TransportError

HEART_HEX = "0100026603FF04FF057E063C07180800"
HEART_ROWS = ["00000000", "01100110", "11111111", "11111111",
              "01111110", "00111100", "00011000", "00000000"]


def make_store(tmp_path) -> GridStore:
    return GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl")


def make_bridge(tmp_path, device: FakeDevice | None = None) -> Bridge:
    return Bridge(FakeTransport(device), make_store(tmp_path))


def replay_rows(sent) -> list[str]:
    model = Max7219Model()
    for _pins, group, data in sent:
        model.apply(data, group)
    return model.picture()["rows"]


def test_start_on_fresh_store(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.start()
    state = bridge.store.current()
    assert bridge.connected
    assert state["display"] == "on"
    assert state["rows"] == ["00000000"] * 8
    assert state["seq"] == 1


def test_shift_out_heart(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.start()
    result = bridge.shift_out(25, 26, 27, 2, HEART_HEX.lower())
    assert result["success"] is True
    assert result["raw_response"] == "OK"
    assert result["decoded_state"]["rows"] == HEART_ROWS
    assert bridge.transport.device.sent[-1] == ((25, 26, 27), 2, bytes.fromhex(HEART_HEX))


def test_disallowed_pin(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.start()
    result = bridge.shift_out(5, 26, 27, 2, HEART_HEX)
    assert result["success"] is False
    assert "PIN_NOT_ALLOWED" in result["error"]
    assert bridge.store.current()["seq"] == 1


@pytest.mark.parametrize("data_hex", ["", "018", "01G0", "00" * 65, 123])
def test_bad_hex_sends_nothing(tmp_path, data_hex):
    bridge = make_bridge(tmp_path)
    bridge.start()
    sent_before = len(bridge.transport.device.sent)
    assert bridge.shift_out(25, 26, 27, 2, data_hex) == {"success": False, "error": "bad_args"}
    assert len(bridge.transport.device.sent) == sent_before


def test_other_pins_not_recorded(tmp_path):
    bridge = make_bridge(tmp_path, FakeDevice(allowed_pins=(25, 26, 27, 4, 5, 18)))
    bridge.start()
    result = bridge.shift_out(4, 5, 18, 1, "A5")
    assert result["success"] is True
    assert "decoded_state" not in result
    assert bridge.store.current()["seq"] == 1


def test_restart_restores_saved_picture(tmp_path):
    first = make_bridge(tmp_path)
    first.start()
    first.shift_out(25, 26, 27, 2, HEART_HEX)
    first.close()

    second = make_bridge(tmp_path)
    second.start()
    assert replay_rows(second.transport.device.sent[-1:]) == HEART_ROWS
    assert second.store.current()["rows"] == HEART_ROWS


def test_reboot_triggers_resync(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.start()
    bridge.transport.simulate_reboot()
    sent_before = len(bridge.transport.device.sent)
    result = bridge.shift_out(25, 26, 27, 2, HEART_HEX)
    assert result["success"] is True
    assert result["resynced"] is True
    after = bridge.transport.device.sent[sent_before:]
    assert len(after) == 2
    assert replay_rows(after[1:]) == bridge.store.current()["rows"] == HEART_ROWS


def test_wait_duration_and_stop(tmp_path):
    bridge = make_bridge(tmp_path)
    t0 = time.monotonic()
    assert bridge.wait(200) == {"success": True}
    assert 0.18 <= time.monotonic() - t0 < 0.4

    t0 = time.monotonic()
    assert bridge.wait(5000, should_stop=lambda: True) == {"success": True, "stopped": True}
    assert time.monotonic() - t0 < 0.1


class RaisingTransport(FakeTransport):
    def send(self, line: str) -> str:
        raise TransportError("simulated timeout")


def test_transport_error(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.start()
    bridge.shift_out(25, 26, 27, 2, HEART_HEX)
    before = bridge.store.current()

    bridge.transport = RaisingTransport(bridge.transport.device)
    result = bridge.shift_out(25, 26, 27, 2, "01FF")
    assert result == {"success": False, "error": "timeout"}
    assert bridge.connected is False
    assert bridge.store.current() == before
