from bridge.grid_model import Max7219Model
from config import CLEAR_HEX, WAKE_HEX

HEART_HEX = "0100026603FF04FF057E063C07180800"
HEART_ROWS = ["00000000", "01100110", "11111111", "11111111",
              "01111110", "00111100", "00011000", "00000000"]


def model_after(*hex_parts: str, msb_is_left: bool = True) -> Max7219Model:
    model = Max7219Model(msb_is_left)
    for part in hex_parts:
        model.apply(bytes.fromhex(part))
    return model


def test_fresh_model_is_unknown():
    pic = Max7219Model().picture()
    assert pic["display"] == "unknown"
    assert pic["rows"] == ["????????"] * 8
    assert pic["intensity"] is None


def test_wake_and_clear():
    pic = model_after(WAKE_HEX, CLEAR_HEX).picture()
    assert pic["display"] == "on"
    assert pic["intensity"] == 2
    assert pic["rows"] == ["00000000"] * 8


def test_heart_after_wake():
    assert model_after(WAKE_HEX, HEART_HEX).picture()["rows"] == HEART_ROWS


def test_single_row_message():
    model = model_after(WAKE_HEX, HEART_HEX, "057C")
    expected = list(HEART_ROWS)
    expected[4] = "01111100"
    assert model.picture()["rows"] == expected


def test_msb_is_right():
    model = model_after(WAKE_HEX, CLEAR_HEX, "0180", msb_is_left=False)
    assert model.picture()["rows"][0] == "00000001"


def test_shutdown_keeps_rows():
    pic = model_after(WAKE_HEX, HEART_HEX, "0C00").picture()
    assert pic["display"] == "shutdown"
    assert pic["rows"] == HEART_ROWS


def test_display_test_and_back():
    model = model_after(WAKE_HEX, HEART_HEX)
    before = model.picture()
    model.apply(bytes.fromhex("0F01"))
    assert model.picture()["display"] == "test"
    model.apply(bytes.fromhex("0F00"))
    assert model.picture() == before


def test_scan_limit_blanks_rows():
    model = model_after(WAKE_HEX, "01FF02FF03FF04FF05FF06FF07FF08FF", "0B03")
    assert model.picture()["rows"] == ["11111111"] * 4 + ["00000000"] * 4


def test_decode_mode_warning():
    assert model_after(WAKE_HEX, "0901").picture()["warnings"] == ["decode_mode_nonzero"]
    assert model_after(WAKE_HEX).picture()["warnings"] == []


def test_bad_data_resets_to_unknown():
    fresh = Max7219Model().picture()
    odd = model_after(WAKE_HEX, HEART_HEX)
    odd.apply(bytes.fromhex("0102FF"))
    assert odd.picture() == fresh
    grouped = model_after(WAKE_HEX, HEART_HEX)
    grouped.apply(bytes.fromhex("0102"), group_size=1)
    assert grouped.picture() == fresh


def test_address_high_nibble_ignored():
    assert model_after(WAKE_HEX, CLEAR_HEX, "F180").picture() == \
        model_after(WAKE_HEX, CLEAR_HEX, "0180").picture()


def test_dict_round_trip():
    for model in (Max7219Model(), model_after(WAKE_HEX, HEART_HEX, "0B05")):
        assert Max7219Model.from_dict(model.to_dict()).picture() == model.picture()


def resynced(model: Max7219Model) -> Max7219Model:
    data = model.resync_bytes()
    assert len(data) == 26
    fresh = Max7219Model()
    fresh.apply(data)
    return fresh


def test_resync_fresh_model():
    pic = resynced(Max7219Model()).picture()
    assert pic["display"] != "unknown"
    assert all("?" not in row for row in pic["rows"])


def test_resync_known_models_match():
    cases = [
        model_after(WAKE_HEX, HEART_HEX, "0A05"),
        model_after(WAKE_HEX, HEART_HEX, "0B030C00"),
        model_after(WAKE_HEX, HEART_HEX, "0B000A00"),
        model_after(WAKE_HEX, HEART_HEX, "0903"),
    ]
    for model in cases:
        pic = resynced(model).picture()
        assert pic["display"] != "unknown"
        assert all("?" not in row for row in pic["rows"])
        assert pic == model.picture()
