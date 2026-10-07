import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def fake_hardware(monkeypatch):
    """No test may ever touch the real port or camera, or open a viewfinder window."""
    import config

    monkeypatch.setattr(config, "SERIAL_PORT", "fake")
    monkeypatch.setattr(config, "CAMERA_SOURCE", "fake")
    monkeypatch.setattr(config, "VISION_VIEW", "0")
