"""Pure-logic software copy of a MAX7219 chip driving an 8x8 LED matrix."""

ROW_COUNT = 8
FIELDS = ("shutdown", "intensity", "scan_limit", "decode_mode", "display_test")


def _known_or(value: int | None, default: int) -> int:
    return default if value is None else value


class Max7219Model:
    def __init__(self, msb_is_left: bool = True) -> None:
        self.msb_is_left = msb_is_left
        self.reset_unknown()

    def reset_unknown(self) -> None:
        self.rows: list[int | None] = [None] * ROW_COUNT
        self.shutdown: bool | None = None
        self.intensity: int | None = None
        self.scan_limit: int | None = None
        self.decode_mode: int | None = None
        self.display_test: bool | None = None

    def apply(self, data: bytes, group_size: int = 2) -> None:
        if group_size != 2 or len(data) % 2:
            self.reset_unknown()
            return
        for i in range(0, len(data), 2):
            reg, value = data[i] & 0x0F, data[i + 1]
            if 1 <= reg <= 8:
                self.rows[reg - 1] = value
            elif reg == 9:
                self.decode_mode = value
            elif reg == 10:
                self.intensity = value & 0x0F
            elif reg == 11:
                self.scan_limit = value & 0x07
            elif reg == 12:
                self.shutdown = not bool(value & 1)
            elif reg == 15:
                self.display_test = bool(value & 1)

    def picture(self) -> dict:
        return {
            "display": self._display(),
            "intensity": self.intensity,
            "rows": [self._row_text(i) for i in range(ROW_COUNT)],
            "warnings": ["decode_mode_nonzero"] if self.decode_mode else [],
        }

    def _display(self) -> str:
        if self.display_test is True:
            return "test"
        if any(getattr(self, name) is None for name in ("shutdown", "scan_limit", "decode_mode", "display_test")):
            return "unknown"
        if self.shutdown is True:
            return "shutdown"
        return "on"

    def _row_text(self, i: int) -> str:
        if self.scan_limit is not None and i > self.scan_limit:
            return "00000000"
        value = self.rows[i]
        if value is None:
            return "????????"
        bits = format(value, "08b")
        return bits if self.msb_is_left else bits[::-1]

    def resync_bytes(self, default_intensity: int = 2) -> bytes:
        messages = [
            (0x0F, 0x00),
            (0x09, _known_or(self.decode_mode, 0x00)),
            (0x0B, _known_or(self.scan_limit, 0x07)),
            (0x0A, _known_or(self.intensity, default_intensity)),
        ]
        messages += [(i + 1, _known_or(row, 0x00)) for i, row in enumerate(self.rows)]
        messages.append((0x0C, 0x00 if self.shutdown is True else 0x01))
        return bytes(b for message in messages for b in message)

    def to_dict(self) -> dict:
        d: dict = {"rows": list(self.rows)}
        d.update({name: getattr(self, name) for name in FIELDS})
        return d

    @classmethod
    def from_dict(cls, d: dict, msb_is_left: bool = True) -> "Max7219Model":
        model = cls(msb_is_left)
        model.rows = list(d["rows"])
        for name in FIELDS:
            setattr(model, name, d[name])
        return model
