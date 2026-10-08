"""physical_check: one test per row of the VSTAGE_5 B2 table, plus the shutdown and test display modes."""

import json

from vision import patterns as P
from vision.ledmap import build_led_map, failed_led_map, physical_check

HEART = ["00000000", "01100110", "11111111", "11111111",
         "01111110", "00111100", "00011000", "00000000"]


def commanded(rows, display="on"):
    return {"seq": 5, "timestamp": 1.0, "display": display, "intensity": 2, "rows": rows,
            "bytes": "", "warnings": []}


def observed(rows, status="ok", warnings=()):
    return build_led_map(1, 2.0, rows, list(warnings), status, 500)


def check(cmd, obs):
    result = physical_check(cmd, obs)
    assert list(result) == ["result", "mismatches", "warnings", "summary"]
    assert json.loads(json.dumps(result)) == result
    return result


def test_match():
    r = check(commanded(HEART), observed(HEART))
    assert r["result"] == "match" and r["mismatches"] == []
    assert r["summary"] == "Camera confirms the picture (64/64 LEDs)."


def test_match_ignores_unknown_cells():
    seen = list(HEART)
    seen[0] = "0?000000"
    r = check(commanded(HEART), observed(seen))
    assert r["result"] == "match" and r["summary"] == "Camera confirms the picture (63/64 LEDs)."


def test_mismatch_lists_every_differing_cell():
    seen = list(HEART)
    seen[2] = "10011111"
    seen[3] = "10011111"
    r = check(commanded(HEART), observed(seen))
    assert r["result"] == "mismatch"
    assert r["mismatches"] == [
        {"row": 2, "col": 1, "commanded": "1", "observed": "0"},
        {"row": 2, "col": 2, "commanded": "1", "observed": "0"},
        {"row": 3, "col": 1, "commanded": "1", "observed": "0"},
        {"row": 3, "col": 2, "commanded": "1", "observed": "0"},
    ]
    assert r["summary"] == "Camera sees 4 LEDs differ from the command (row 2 cols 1-2, row 3 cols 1-2)."


def test_not_visible():
    r = check(commanded(HEART), observed(P.all_off(), "dark"))
    assert r["result"] == "not_visible" and r["mismatches"] == []
    assert "32 should be lit" in r["summary"]


def test_consistent_dark():
    r = check(commanded(P.all_off()), observed(P.all_off(), "dark"))
    assert r["result"] == "consistent_dark"


def test_uncertain_carries_warnings():
    seen = list(HEART)
    seen[1] = "0??00110"
    r = check(commanded(HEART), observed(seen, "unreliable", ["lighting_changed"]))
    assert r["result"] == "uncertain" and r["warnings"] == ["lighting_changed"]
    assert "lighting_changed" in r["summary"]


def test_unavailable():
    for status in ("uncalibrated", "camera_error", "disabled"):
        r = check(commanded(HEART), failed_led_map(1, 0.0, status, [], 0))
        assert r["result"] == "unavailable", status
    assert check(commanded(HEART), None)["result"] == "unavailable"


def test_shutdown_means_all_off():
    off = commanded(HEART, "shutdown")
    assert check(off, observed(P.all_off(), "dark"))["result"] == "consistent_dark"
    r = check(off, observed(HEART))
    assert r["result"] == "mismatch" and len(r["mismatches"]) == 32


def test_display_test_means_all_on():
    on = commanded(HEART, "test")
    assert check(on, observed(P.all_on()))["result"] == "match"
    assert check(on, observed(HEART))["result"] == "mismatch"
    assert check(on, observed(P.all_off(), "dark"))["result"] == "not_visible"


def test_unknown_commanded_picture_is_not_confirmed():
    r = check(commanded(["????????"] * 8, "unknown"), observed(HEART))
    assert r["result"] == "uncertain"
