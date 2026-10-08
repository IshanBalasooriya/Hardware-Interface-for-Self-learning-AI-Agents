import json
import re
import time

from agent.evidence import build_catalogue, frame_digest, skill_outline
from skills.store import SkillStore
from tests.helpers import frame_hex

HEART_ROWS = ["00000000", "01100110", "11111111", "11111111",
              "01111110", "00111100", "00011000", "00000000"]
OFF_ROWS = ["00000000"] * 8
HEART_HEX = frame_hex(HEART_ROWS)
SMALL_HEX = frame_hex(["00000000", "00000000", "00100100", "01111110",
                       "00111100", "00011000", "00000000", "00000000"])
HEX_RUN = re.compile(r"[0-9A-Fa-f]{8,}")


def frame(data_hex: str) -> dict:
    return {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                         "group_size": 2, "data_hex": data_hex}}


def wait(ms: object) -> dict:
    return {"tool": "wait", "args": {"duration_ms": ms}}


def repeat(count: object, actions: list) -> dict:
    return {"tool": "repeat", "args": {"count": count, "actions": actions}}


def skill(actions: list, **extra) -> dict:
    return {"type": "action_sequence", "actions": actions, **extra}


HEARTBEAT = skill([repeat(6, [frame(HEART_HEX), wait(250), frame(SMALL_HEX), wait(250)])], description="Beat")


def test_outline_single_frame():
    outline = skill_outline(skill([frame(HEART_HEX)]))
    assert outline["frames_sent"] == 1
    assert outline["distinct_frames"] == 1
    assert outline["duration_ms"] == 0
    assert outline["params"] == {}
    assert outline["structure"] == "frame A"


def test_outline_repeat():
    outline = skill_outline(HEARTBEAT)
    assert outline["frames_sent"] == 12
    assert outline["distinct_frames"] == 2
    assert outline["duration_ms"] == 3000
    assert "repeat 6" in outline["structure"]
    assert outline["structure"] == "repeat 6 x [frame A, wait 250, frame B, wait 250]"


def test_outline_nested_repeats_multiply_without_unrolling():
    definition = skill([repeat(1000, [repeat(1000, [repeat(1000, [frame(HEART_HEX), wait(10)])])])])
    started = time.monotonic()
    outline = skill_outline(definition)
    assert time.monotonic() - started < 0.5
    assert outline["frames_sent"] == 1000 ** 3
    assert outline["duration_ms"] == 10 * 1000 ** 3
    assert outline["distinct_frames"] == 1
    small = skill_outline(skill([repeat(2, [repeat(3, [frame(HEART_HEX)]), frame(SMALL_HEX), wait(100)])]))
    assert small["frames_sent"] == 8
    assert small["duration_ms"] == 200


def test_outline_param_count_uses_default():
    definition = skill([repeat("$n", [frame(HEART_HEX), wait("$ms"), frame(SMALL_HEX), wait("$ms")])],
                       params={"n": 4, "ms": 300})
    outline = skill_outline(definition)
    assert outline["frames_sent"] == 8
    assert outline["duration_ms"] == 2400
    assert outline["params"] == {"n": 4, "ms": 300}
    assert "$n" in outline["structure"]
    assert "$ms" in outline["structure"]


def test_outline_has_no_hex():
    definitions = [HEARTBEAT, skill([frame("$pic")], params={"pic": HEART_HEX})]
    for definition in definitions:
        outline = skill_outline(definition)
        assert not HEX_RUN.search(json.dumps(outline)), outline


def test_build_catalogue(tmp_path):
    store = SkillStore(tmp_path / "library")
    store.save("symbol_heart", skill([frame(HEART_HEX)], description="Heart"))
    store.save("anim_heartbeat", HEARTBEAT)
    (tmp_path / "library" / "broken.json").write_text("{not json", encoding="utf-8")
    catalogue = build_catalogue(store)
    assert [entry["name"] for entry in catalogue] == ["anim_heartbeat", "symbol_heart"]
    keys = {"name", "description", "version", "params", "frames_sent", "distinct_frames", "duration_ms", "structure"}
    for entry in catalogue:
        assert set(entry) == keys
    assert catalogue[0]["description"] == "Beat"
    assert catalogue[0]["version"] == 1
    assert catalogue[0]["frames_sent"] == 12


def frames_of(rows_list: list[list[str]], gap_s: float = 0.25, display: str = "on", start: float = 1000.0) -> list[dict]:
    return [{"seq": i + 1, "timestamp": start + i * gap_s, "display": display, "intensity": 2,
             "rows": rows, "bytes": "", "warnings": []} for i, rows in enumerate(rows_list)]


def test_digest_on_off_cycles():
    digest = frame_digest(frames_of([HEART_ROWS, OFF_ROWS] * 6, gap_s=0.3))
    assert digest["total"] == 12
    assert digest["truncated"] is False
    assert set(digest["pictures"]) == {"P1", "P2"}
    assert digest["pictures"]["P1"] == {"rows": HEART_ROWS, "lit": 32, "display": "on"}
    assert digest["pictures"]["P2"]["lit"] == 0
    assert digest["sequence"] == ["P1", "P2"] * 6
    assert digest["counts"] == {"P1": 6, "P2": 6}
    assert digest["gaps_ms"] == [300] * 11


def test_digest_truncation():
    digest = frame_digest(frames_of([HEART_ROWS, OFF_ROWS] * 50), max_frames=80)
    assert len(digest["sequence"]) == 80
    assert digest["truncated"] is True
    assert digest["total"] == 100
    assert sum(digest["counts"].values()) == 100
    assert len(digest["gaps_ms"]) == 79


def test_digest_gap_rounding_and_empty():
    frames = frames_of([HEART_ROWS, OFF_ROWS, HEART_ROWS])
    frames[1]["timestamp"] = frames[0]["timestamp"] + 0.2468
    frames[2]["timestamp"] = frames[1]["timestamp"] + 0.0031
    assert frame_digest(frames)["gaps_ms"] == [250, 0]
    assert frame_digest([]) == {"total": 0, "truncated": False, "pictures": {}, "sequence": [],
                                "gaps_ms": [], "counts": {}}


def test_digest_display_separates_pictures():
    frames = frames_of([HEART_ROWS]) + frames_of([HEART_ROWS], display="off", start=1001.0)
    digest = frame_digest(frames)
    assert digest["sequence"] == ["P1", "P2"]
    assert digest["pictures"]["P2"]["display"] == "off"
