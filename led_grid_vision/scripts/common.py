"""Script wiring: open the link and camera from config, and show pictures on the grid."""

import config
from link.serial_link import open_link
from vision.camera import open_camera
from vision.fake_camera import FakeCamera
from vision.ledmap import rows_to_hex


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


def make_show(link, camera):
    def show(rows: list[str]) -> None:
        _send(link, rows_to_hex(rows), "show")
        if isinstance(camera, FakeCamera):
            camera.set_rows(rows)

    return show
