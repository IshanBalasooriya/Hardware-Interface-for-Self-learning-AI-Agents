import random

import pytest

from vision import patterns as P
from vision.ledmap import compare, hex_to_rows, rows_to_hex

HEART = ["00000000", "01100110", "11111111", "11111111",
         "01111110", "00111100", "00011000", "00000000"]
HEART_HEX = "0100026603FF04FF057E063C07180800"

NAMES = (["all_off", "all_on", "corner_tl", "corner_tr", "corner_bl", "corner_br",
          "checker_0", "checker_1"] + [f"row_{i}" for i in range(8)] + [f"col_{i}" for i in range(8)])


def _ok(rows):
    return len(rows) == 8 and all(len(r) == 8 and set(r) <= {"0", "1"} for r in rows)


def test_standard_set_names_and_shapes():
    items = P.standard_set()
    assert [n for n, _ in items] == NAMES
    assert all(_ok(rows) for _, rows in items)


def test_basic_patterns():
    assert P.all_off() == ["0" * 8] * 8
    assert P.all_on() == ["1" * 8] * 8
    assert P.row_only(3)[3] == "1" * 8 and sum(r.count("1") for r in P.row_only(3)) == 8
    assert all(r[5] == "1" and r.count("1") == 1 for r in P.col_only(5))


def test_checkers_are_complements():
    a, b = P.checker(0), P.checker(1)
    assert all(x != y for ra, rb in zip(a, b) for x, y in zip(ra, rb))
    assert a[0][0] == "1"


@pytest.mark.parametrize("name,pos", [("tl", (0, 0)), ("tr", (0, 7)), ("bl", (7, 0)), ("br", (7, 7))])
def test_corners(name, pos):
    rows = P.corner(name)
    on = [(r, c) for r in range(8) for c in range(8) if rows[r][c] == "1"]
    assert on == [pos]


def test_random_frame_reproducible():
    a = P.random_frame(random.Random(7), 0.4)
    b = P.random_frame(random.Random(7), 0.4)
    assert a == b and _ok(a)
    assert P.random_frame(random.Random(8), 0.4) != a


def test_rows_to_hex_heart():
    assert rows_to_hex(HEART) == HEART_HEX
    assert rows_to_hex(["0" * 8, "01100110"] + ["0" * 8] * 6)[4:8] == "0266"


def test_rows_to_hex_bit_reversed():
    rows = ["10000000"] + ["0" * 8] * 7
    assert rows_to_hex(rows, msb_is_left=True).startswith("0180")
    assert rows_to_hex(rows, msb_is_left=False).startswith("0101")


def test_rows_to_hex_rejects_unknown():
    with pytest.raises(ValueError):
        rows_to_hex(["????????"] + ["0" * 8] * 7)


def test_hex_to_rows_inverts():
    assert hex_to_rows(HEART_HEX) == HEART
    for _, rows in P.standard_set():
        assert hex_to_rows(rows_to_hex(rows)) == rows
    rows = P.checker(1)
    assert hex_to_rows(rows_to_hex(rows, msb_is_left=False), msb_is_left=False) == rows


def test_hex_to_rows_ignores_other_addresses():
    base = P.all_on()
    assert hex_to_rows("0F0009000A020C01", base) == base
    assert hex_to_rows("0C010300", base)[2] == "0" * 8


def test_compare():
    exp = P.all_off()
    obs = ["1" + "0" * 7, "0" * 7 + "1", "?" + "0" * 7] + ["0" * 8] * 5
    exp2 = list(exp)
    exp2[3] = "?" + "0" * 7
    obs[3] = "1" + "0" * 7
    assert compare(exp2, obs) == [
        {"row": 0, "col": 0, "expected": "0", "observed": "1"},
        {"row": 1, "col": 7, "expected": "0", "observed": "1"},
    ]
