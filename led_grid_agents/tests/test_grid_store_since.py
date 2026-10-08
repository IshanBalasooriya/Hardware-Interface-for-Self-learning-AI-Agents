import json

from bridge.grid_store import GridStore
from config import CLEAR_HEX, MAX_HISTORY, WAKE_HEX


def make_store(tmp_path) -> GridStore:
    return GridStore(tmp_path / "logs" / "state.json", tmp_path / "logs" / "frames.jsonl")


def record_n(store: GridStore, n: int) -> None:
    for _ in range(n):
        store.record(bytes.fromhex(CLEAR_HEX))


def test_since_returns_newer_frames_oldest_first(tmp_path):
    store = make_store(tmp_path)
    store.record(bytes.fromhex(WAKE_HEX))
    record_n(store, 4)
    frames = store.since(2)
    assert [f["seq"] for f in frames] == [3, 4, 5]


def test_since_is_not_capped(tmp_path):
    store = make_store(tmp_path)
    record_n(store, 45)
    frames = store.since(5)
    assert len(frames) == 40 > MAX_HISTORY
    assert [f["seq"] for f in frames] == list(range(6, 46))


def test_since_skips_corrupt_and_seqless_lines(tmp_path):
    store = make_store(tmp_path)
    record_n(store, 2)
    with store.log_path.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write(json.dumps({"rows": [], "display": "on"}) + "\n")
        f.write(json.dumps({"seq": "7"}) + "\n")
        f.write(json.dumps({"seq": True}) + "\n")
        f.write("[1, 2]\n")
    record_n(store, 1)
    assert [f["seq"] for f in store.since(0)] == [1, 2, 3]


def test_since_missing_log_is_empty(tmp_path):
    assert make_store(tmp_path).since(0) == []


def test_since_current_seq_is_empty(tmp_path):
    store = make_store(tmp_path)
    record_n(store, 3)
    assert store.since(store.current()["seq"]) == []
