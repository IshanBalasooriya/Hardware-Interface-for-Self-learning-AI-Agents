import json

from vision import patterns as P
from vision.ledmap import build_led_map, failed_led_map, rows_to_hex

HEART = ["00000000", "01100110", "11111111", "11111111",
         "01111110", "00111100", "00011000", "00000000"]
KEYS = ["seq", "timestamp", "display", "intensity", "rows", "bytes", "warnings", "source", "vision"]


def test_shape_and_types():
    m = build_led_map(17, 1791283698.61, HEART, [], "ok", 310.4)
    assert list(m) == KEYS
    assert m == {"seq": 17, "timestamp": 1791283698.61, "display": "on", "intensity": None, "rows": HEART,
                 "bytes": "0100026603FF04FF057E063C07180800", "warnings": [], "source": "camera",
                 "vision": {"status": "ok", "uncertain": 0, "read_ms": 310}}
    assert list(m["vision"]) == ["status", "uncertain", "read_ms"]
    assert json.loads(json.dumps(m)) == m


def test_numpy_inputs_become_plain_types():
    import numpy as np

    m = build_led_map(np.int64(3), np.float64(1.5), P.all_on(), [], "ok", np.float32(12.6))
    assert type(m["seq"]) is int and type(m["timestamp"]) is float and type(m["vision"]["read_ms"]) is int
    json.dumps(m)


def test_display_and_bytes():
    off = build_led_map(1, 0.0, P.all_off(), [], "ok", 0)
    assert off["display"] == "unknown" and off["bytes"] == rows_to_hex(P.all_off())
    unsure = P.single(2, 5)
    unsure[4] = "000?0000"
    m = build_led_map(1, 0.0, unsure, ["too_many_uncertain"], "unreliable", 0)
    assert m["display"] == "on" and m["bytes"] is None and m["vision"]["uncertain"] == 1
    assert m["warnings"] == ["too_many_uncertain"]


def test_failed_map():
    m = failed_led_map(4, 2.0, "uncalibrated", ["camera_settings_changed"], 7)
    assert list(m) == KEYS
    assert m["rows"] == ["????????"] * 8 and m["bytes"] is None and m["display"] == "unknown"
    assert m["vision"] == {"status": "uncalibrated", "uncertain": 64, "read_ms": 7}
    assert m["intensity"] is None and m["source"] == "camera"
