"""Script wiring: open the link and camera from config, and show pictures on the grid."""

import config
from link.serial_link import open_link
from vision.camera import open_camera
from vision.fake_camera import FakeCamera
from vision.ledmap import hex_to_rows, rows_to_hex


def open_all():
    """Open link and camera from config. Fake mode gives FakeLink and FakeCamera."""
    link = open_link()
    link.open()
    camera = open_camera()
    try:
        camera.open()
    except Exception:
        link.close()
        raise
    return link, camera


def _send(link, data_hex: str, what: str) -> None:
    ok, reply = link.shift_out(data_hex)
    if not ok:
        raise RuntimeError(f"{what} failed: {reply}")


def wake(link) -> None:
    _send(link, config.WAKE_HEX, "wake")


def clear(link) -> None:
    _send(link, config.CLEAR_HEX, "clear")


def set_intensity(link, value: int) -> None:
    """Intensity register 0A, 00..0F, as one 2-byte frame."""
    if not 0 <= value <= 15:
        raise ValueError(f"intensity must be 0..15, got {value}")
    _send(link, f"0A{value:02X}", "intensity")


def make_show(link, camera):
    def show(rows: list[str]) -> None:
        _send(link, rows_to_hex(rows), "show")
        if isinstance(camera, FakeCamera):
            camera.set_rows(rows)

    return show


def send_hex(link, camera, data_hex: str) -> list[str]:
    """Send a raw row frame as is; returns the rows it produces from a blank grid."""
    rows = hex_to_rows(data_hex)
    _send(link, data_hex, "show")
    if isinstance(camera, FakeCamera):
        camera.set_rows(rows)
    return rows
