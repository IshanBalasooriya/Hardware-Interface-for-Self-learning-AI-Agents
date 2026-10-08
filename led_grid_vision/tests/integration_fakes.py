"""Shared fakes for the stage 5 integration tests: a camera that follows the fake chip, and a calibration."""

import pytest

import config
from bridge.grid_model import Max7219Model
from vision.calibration import calibrate, save_calibration
from vision.fake_camera import FakeCamera
from vision.ledmap import effective_rows

HEART = ["00000000", "01100110", "11111111", "11111111", "01111110", "00111100", "00011000", "00000000"]
ROOM = dict(ambient=0.0, off_level=3.0)  # dark room, as on the real webcam (tests/test_reader.py)
COVERED = {(2, 1), (2, 2), (3, 1), (3, 2)}  # 4 lit cells of the heart


class ChipCamera(FakeCamera):
    """FakeCamera whose picture follows the fake device's real register state (its own chip model,
    fed from the bytes the device accepted), never GridStore. Logs every frame grab."""

    def __init__(self, device, log=None, **kw) -> None:
        super().__init__(**{**ROOM, **kw})
        self.device = device
        self.log = log if log is not None else []
        self.chip = Max7219Model()
        self._applied = 0

    def grab(self, n):
        for _pins, group, payload in self.device.sent[self._applied:]:
            self.chip.apply(payload, group)
        self._applied = len(self.device.sent)
        self.set_rows([r.replace("?", "0") for r in effective_rows(self.chip.picture())])
        self.log.append("grab")
        return super().grab(n)


def write_calibration(path):
    """Calibrate the dark-room fake quickly and save it to `path`."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "VISION_FLUSH_FRAMES", 0)
        mp.setattr(config, "VISION_AVG_FRAMES", 2)
        cam = FakeCamera(**ROOM)
        res = calibrate(cam, cam.set_rows, intensity=2, settle_ms=0)
    assert res.ok, res.reason
    save_calibration(res.calibration, path)
    return path
