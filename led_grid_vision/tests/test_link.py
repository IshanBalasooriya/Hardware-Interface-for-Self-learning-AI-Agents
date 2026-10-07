import config
from link.serial_link import FakeLink, GridLink, build_shift_out_line, open_link, validate_hex


def test_command_line_format():
    assert build_shift_out_line("0f01") == "SHIFT_OUT 25 26 27 2 0F01\n"


def test_fake_link_records_and_fails():
    link = FakeLink()
    assert link.ping()
    assert link.shift_out(config.WAKE_HEX) == (True, "OK")
    link.fail_next(2)
    assert link.shift_out("0100") == (False, "ERR BAD_ARGS")
    assert link.shift_out("0100") == (False, "ERR BAD_ARGS")
    assert link.shift_out("0101") == (True, "OK")
    assert link.sent == [config.WAKE_HEX, "0101"]
    link.close()
    link.close()


def test_local_rejection():
    assert validate_hex("") is not None
    assert validate_hex("012") is not None
    assert validate_hex("01GG") is not None
    assert validate_hex("010203") is not None  # 3 bytes, group size 2
    assert validate_hex("00" * 66) is not None  # 66 bytes > 64
    assert validate_hex("00" * 64) is None
    link = FakeLink()
    ok, reply = link.shift_out("012")
    assert not ok and reply.startswith("REJECTED") and link.sent == []


def test_grid_link_rejects_without_port():
    # No serial port is opened: rejection happens before any I/O.
    link = GridLink("COM_NOT_USED", 115200, 1.0)
    ok, reply = link.shift_out("")
    assert not ok and reply.startswith("REJECTED")
    link.close()
    link.close()


def test_open_link_fake():
    assert isinstance(open_link(), FakeLink)
