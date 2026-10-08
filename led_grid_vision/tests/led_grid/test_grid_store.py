import json
import threading

from bridge.grid_store import GridStore
from config import CLEAR_HEX, MAX_HISTORY, WAKE_HEX

HEART_HEX = "0100026603FF04FF057E063C07180800"
HEART_ROWS = ["00000000", "01100110", "11111111", "11111111",
              "01111110", "00111100", "00011000", "00000000"]
MAP_FIELDS = {"seq", "timestamp", "display", "intensity", "rows", "bytes", "warnings"}


def make_store(tmp_path) -> GridStore:
    return GridStore(tmp_path / "logs" / "state.json", tmp_path / "logs" / "frames.jsonl")


def draw_heart(store: GridStore) -> None:
    store.record(bytes.fromhex(WAKE_HEX + CLEAR_HEX))
    store.record(bytes.fromhex(HEART_HEX))


def log_lines(store: GridStore) -> list[str]:
    return store.log_path.read_text(encoding="utf-8").splitlines()


def test_new_store_is_unknown(tmp_path):
    state = make_store(tmp_path).current()
    assert state["seq"] == 0
    assert state["display"] == "unknown"
    assert state["rows"] == ["????????"] * 8


def test_record_heart(tmp_path):
    store = make_store(tmp_path)
    draw_heart(store)
    state = store.current()
    assert state["seq"] == 2
    assert state["rows"] == HEART_ROWS
    assert state["bytes"] == HEART_HEX


def test_state_file_written_atomically(tmp_path):
    store = make_store(tmp_path)
    store.record(bytes.fromhex(WAKE_HEX))
    assert store.state_path.exists()
    assert not store.state_path.with_name(store.state_path.name + ".tmp").exists()


def test_load_restores_state(tmp_path):
    store = make_store(tmp_path)
    draw_heart(store)
    reloaded = make_store(tmp_path)
    assert reloaded.load() is True
    before, after = store.current(), reloaded.current()
    for key in ("rows", "display", "intensity", "seq"):
        assert after[key] == before[key]


def test_frame_log_one_line_per_record(tmp_path):
    store = make_store(tmp_path)
    draw_heart(store)
    store.record(bytes.fromhex("057C"))
    lines = log_lines(store)
    assert len(lines) == 3
    for line in lines:
        assert set(json.loads(line)) == MAP_FIELDS


def test_recent(tmp_path):
    store = make_store(tmp_path)
    for _ in range(MAX_HISTORY + 5):
        store.record(bytes.fromhex(HEART_HEX))
    last_two = store.recent(2)
    assert [frame["seq"] for frame in last_two] == [MAX_HISTORY + 4, MAX_HISTORY + 5]
    assert len(store.recent(999)) == MAX_HISTORY


def test_listeners(tmp_path):
    store = make_store(tmp_path)
    received = []

    def broken(_state: dict) -> None:
        raise RuntimeError("listener failure")

    store.add_listener(broken)
    store.add_listener(received.append)
    draw_heart(store)
    assert [state["seq"] for state in received] == [1, 2]
    assert received[-1]["rows"] == HEART_ROWS


def test_current_is_a_copy(tmp_path):
    store = make_store(tmp_path)
    draw_heart(store)
    state = store.current()
    state["rows"][0] = "11111111"
    state["seq"] = 99
    assert store.current()["rows"] == HEART_ROWS
    assert store.current()["seq"] == 2


def test_corrupt_state_file(tmp_path):
    store = make_store(tmp_path)
    store.state_path.write_text("{not json", encoding="utf-8")
    assert store.load() is False
    assert not store.state_path.exists()
    assert store.state_path.with_name(store.state_path.name + ".corrupt").exists()
    assert store.record(bytes.fromhex(WAKE_HEX))["seq"] == 1


def test_concurrent_records(tmp_path):
    store = make_store(tmp_path)

    def worker() -> None:
        for _ in range(20):
            store.record(bytes.fromhex(HEART_HEX))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.current()["seq"] == 200
    lines = log_lines(store)
    assert len(lines) == 200
    assert all(json.loads(line) for line in lines)
