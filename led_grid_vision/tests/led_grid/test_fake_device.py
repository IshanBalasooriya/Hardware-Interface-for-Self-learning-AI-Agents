import pytest

from bridge.fake_device import FakeDevice
from config import WAKE_HEX


def shift(args: str) -> str:
    return f"SHIFT_OUT {args}"


def test_ping():
    assert FakeDevice().handle("PING") == "OK PONG"


def test_valid_frame_is_recorded():
    device = FakeDevice()
    assert device.handle(shift(f"25 26 27 2 {WAKE_HEX}") + "\r\n") == "OK"
    assert device.sent == [((25, 26, 27), 2, bytes.fromhex(WAKE_HEX))]


def test_unknown_command():
    assert FakeDevice().handle("HELLO") == "ERR UNKNOWN_COMMAND"


def test_empty_line_gets_no_reply():
    assert FakeDevice().handle("\r\n") is None


@pytest.mark.parametrize("line, reply", [
    (shift("25 26 27 2"), "ERR BAD_ARGS"),               # 1. not exactly 6 tokens
    (shift("25 26 x 2 0180"), "ERR BAD_ARGS"),           # 2. not a decimal integer
    (shift("5 26 27 2 0180"), "ERR PIN_NOT_ALLOWED"),    # 3. pin not allowlisted
    (shift("25 25 27 2 0180"), "ERR BAD_ARGS"),          # 4. pins not distinct
    (shift("25 26 27 0 0180"), "ERR BAD_ARGS"),          # 5. group size < 1
    (shift("25 26 27 2 018"), "ERR BAD_ARGS"),           # 6. odd hex length
    (shift("25 26 27 2 01G0"), "ERR BAD_ARGS"),          # 6. non-hex character
    (shift("25 26 27 2 01FFFF"), "ERR BAD_ARGS"),        # 7. not a multiple of group size
])
def test_validation_failures(line, reply):
    device = FakeDevice()
    assert device.handle(line) == reply
    assert device.sent == []


def test_64_bytes_accepted_65_rejected():
    device = FakeDevice()
    assert device.handle(shift("25 26 27 1 " + "AB" * 64)) == "OK"
    assert device.handle(shift("25 26 27 1 " + "AB" * 65)) == "ERR BAD_ARGS"
    assert len(device.sent) == 1


def test_line_longer_than_buffer_rejected():
    assert FakeDevice().handle("P" * 256) == "ERR BAD_ARGS"
