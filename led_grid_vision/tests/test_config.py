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
    "VISION_SCENE_MIN_DELTA": 12.0, "VISION_LEVEL_TOLERANCE": 0.2,
    "VISION_MOVE_TOLERANCE_FRAC": 0.15, "VISION_MOVE_MIN_CELLS": 3,
    "VISION_VIEW_ZOOM": 6,
}


def _fresh(monkeypatch, **env):
    for name in ("SERIAL_PORT", "CAMERA_SOURCE", "CAMERA_BACKEND", "VISION_VIEW", "CALIBRATION_FILE"):
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
                 VISION_VIEW="0", CALIBRATION_FILE="logs/fake_calibration.json")
    try:
        assert cfg.CALIBRATION_FILE == Path(cfg.__file__).resolve().parent / "logs" / "fake_calibration.json"
        assert cfg.SERIAL_PORT == "fake"
        assert cfg.CAMERA_SOURCE == "fake"
        assert cfg.CAMERA_BACKEND == "msmf"
        assert cfg.VISION_VIEW == "0"
    finally:
        _fresh(monkeypatch)


AGENT_EXPECTED = {  # copied from ..\led_grid\config.py (stage 5)
    "MAX_WAIT_MS": 10000, "MAX_HISTORY": 30, "MAX_TURNS": 20, "SKILL_TIME_CAP_S": 30,
}


def test_agent_defaults(monkeypatch):
    cfg = _fresh(monkeypatch)
    try:
        for name, value in AGENT_EXPECTED.items():
            assert getattr(cfg, name) == value, name
        base = Path(cfg.__file__).resolve().parent
        assert cfg.LOG_DIR == base / "logs"
        assert cfg.STATE_FILE == base / "logs" / "shift_state.json"
        assert cfg.FRAMES_LOG == base / "logs" / "shift_frames.jsonl"
        assert cfg.EVENTS_LOG == base / "logs" / "sample_events.jsonl"
        assert cfg.SKILLS_DIR == base / "skills" / "library"
    finally:
        _fresh(monkeypatch)


def test_every_path_inside_this_folder(monkeypatch):
    cfg = _fresh(monkeypatch)
    try:
        base = Path(cfg.__file__).resolve().parent
        paths = {name: value for name, value in vars(cfg).items() if name.isupper() and isinstance(value, Path)}
        assert len(paths) >= 9
        for name, value in paths.items():
            resolved = value.resolve()
            assert resolved == base or base in resolved.parents, name
            assert "led_grid" not in [p.name for p in resolved.parents] and resolved.name != "led_grid", name
    finally:
        _fresh(monkeypatch)
