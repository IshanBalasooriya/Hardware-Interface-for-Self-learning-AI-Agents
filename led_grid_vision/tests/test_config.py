import importlib
from pathlib import Path

import config

EXPECTED = {
    "BAUD": 115200, "SERIAL_TIMEOUT_S": 2.0,
    "DATA_PIN": 25, "CLOCK_PIN": 26, "LATCH_PIN": 27, "GROUP_SIZE": 2,
    "MSB_IS_LEFT": True,
    "WAKE_HEX": "0F0009000B070A020C01",
    "CLEAR_HEX": "01000200030004000500060007000800",
    "DEFAULT_INTENSITY": 2,
    "CAMERA_WIDTH": 1280, "CAMERA_HEIGHT": 720, "CAMERA_WARMUP_FRAMES": 15,
    "VISION_SETTLE_MS": 150, "VISION_FLUSH_FRAMES": 4, "VISION_AVG_FRAMES": 6,
    "VISION_REDUCE": "mean", "VISION_CHANNEL": "green",
    "VISION_SAMPLE_RADIUS_FRAC": 0.25, "VISION_MIN_PITCH_PX": 8.0,
    "VISION_MIN_SEPARATION": 25.0, "VISION_UNCERTAIN_BAND": 0.30,
    "VISION_MAX_UNCERTAIN": 4, "VISION_SCENE_TOLERANCE": 0.25,
    "VISION_VIEW_ZOOM": 6,
}


def _fresh(monkeypatch, **env):
    for name in ("SERIAL_PORT", "CAMERA_SOURCE", "CAMERA_BACKEND", "VISION_VIEW"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


def test_defaults(monkeypatch):
    cfg = _fresh(monkeypatch)
    try:
        for name, value in EXPECTED.items():
            assert getattr(cfg, name) == value, name
        assert cfg.SERIAL_PORT == "COM6"
        assert cfg.CAMERA_SOURCE == "0"
        assert cfg.CAMERA_BACKEND == "dshow"
        assert cfg.VISION_VIEW == "1"
        base = Path(cfg.__file__).resolve().parent
        assert cfg.CALIBRATION_FILE == base / "logs" / "vision_calibration.json"
        assert cfg.VISION_CAPTURE_DIR == base / "logs" / "vision" / "captures"
        assert cfg.VISION_DEBUG_DIR == base / "logs" / "vision" / "debug"
    finally:
        _fresh(monkeypatch)


def test_env_overrides(monkeypatch):
    cfg = _fresh(monkeypatch, SERIAL_PORT="fake", CAMERA_SOURCE="fake", CAMERA_BACKEND="msmf",
                 VISION_VIEW="0")
    try:
        assert cfg.SERIAL_PORT == "fake"
        assert cfg.CAMERA_SOURCE == "fake"
        assert cfg.CAMERA_BACKEND == "msmf"
        assert cfg.VISION_VIEW == "0"
    finally:
        _fresh(monkeypatch)
