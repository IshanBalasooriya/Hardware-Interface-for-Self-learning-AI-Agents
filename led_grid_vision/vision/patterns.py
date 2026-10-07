"""Arithmetic test patterns as `rows` (8 strings of 8 '0'/'1'; rows[0] top, char 0 left)."""

import random

N = 8
CORNERS = {"tl": (0, 0), "tr": (0, 7), "bl": (7, 0), "br": (7, 7)}


def _build(on) -> list[str]:
    return ["".join("1" if on(r, c) else "0" for c in range(N)) for r in range(N)]


def all_off() -> list[str]:
    return _build(lambda r, c: False)


def all_on() -> list[str]:
    return _build(lambda r, c: True)


def single(row: int, col: int) -> list[str]:
    return _build(lambda r, c: (r, c) == (row, col))


def corner(name: str) -> list[str]:
    return single(*CORNERS[name])


def checker(phase: int) -> list[str]:
    return _build(lambda r, c: (r + c) % 2 == phase)


def row_only(row: int) -> list[str]:
    return _build(lambda r, c: r == row)


def col_only(col: int) -> list[str]:
    return _build(lambda r, c: c == col)


def random_frame(rng: random.Random, density: float = 0.5) -> list[str]:
    return _build(lambda r, c: rng.random() < density)


def standard_set() -> list[tuple[str, list[str]]]:
    items = [("all_off", all_off()), ("all_on", all_on())]
    items += [(f"corner_{k}", corner(k)) for k in ("tl", "tr", "bl", "br")]
    items += [("checker_0", checker(0)), ("checker_1", checker(1))]
    items += [(f"row_{r}", row_only(r)) for r in range(N)]
    items += [(f"col_{c}", col_only(c)) for c in range(N)]
    return items
