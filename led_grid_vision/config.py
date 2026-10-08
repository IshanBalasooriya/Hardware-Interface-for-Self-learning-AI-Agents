"""All constants for the vision pipeline (VISION_MASTER.md section 8).

Hardware values confirmed against ..\\led_grid\\config.py on 2026-10-07.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Serial link
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM6")  # "fake" selects FakeLink
BAUD = 115200
SERIAL_TIMEOUT_S = 2.0
DATA_PIN = 25
CLOCK_PIN = 26
LATCH_PIN = 27
GROUP_SIZE = 2
MAX_SHIFT_BYTES = 64
MSB_IS_LEFT = True
WAKE_HEX = "0F0009000B070A020C01"
CLEAR_HEX = "01000200030004000500060007000800"
DEFAULT_INTENSITY = 2

# Camera
CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "0")  # index, stream URL, or "fake"
CAMERA_BACKEND = os.getenv("CAMERA_BACKEND", "dshow")  # dshow, msmf, any
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_WARMUP_FRAMES = 15

# Vision
VISION_SETTLE_MS = 150
VISION_FLUSH_FRAMES = 4
VISION_AVG_FRAMES = 6
VISION_REDUCE = "mean"
VISION_CHANNEL = "green"  # max, red, green, gray. Green: red glow bleeds onto unlit neighbours (stage 2)
VISION_SAMPLE_RADIUS_FRAC = 0.25
VISION_MIN_PITCH_PX = 8.0
VISION_MIN_SEPARATION = 25.0
VISION_UNCERTAIN_BAND = 0.30
VISION_MAX_UNCERTAIN = 4
VISION_SCENE_TOLERANCE = 0.25
VISION_SCENE_MIN_DELTA = 12.0  # absolute change the scene reference must also exceed (baseline can be 0)
VISION_LEVEL_TOLERANCE = 0.2  # class-wide drift from the calibrated off/on levels (share of gap) -> lighting_changed
VISION_MOVE_TOLERANCE_FRAC = 0.15  # median lit-cell centroid offset (fraction of pitch) -> grid_moved. Plan: 0.2; tuned on real runs (stage 3)
VISION_MOVE_MIN_CELLS = 3  # lit cells needed before the centroid test is applied

# Viewfinder (master section 9.1)
VISION_VIEW = os.getenv("VISION_VIEW", "1")  # "1" shows the window in live scripts; "0" hides it
VISION_VIEW_ZOOM = 6

# Files
CALIBRATION_FILE = BASE_DIR / os.getenv("CALIBRATION_FILE", "logs/vision_calibration.json")  # relative -> BASE_DIR
VISION_CAPTURE_DIR = BASE_DIR / "logs" / "vision" / "captures"
VISION_DEBUG_DIR = BASE_DIR / "logs" / "vision" / "debug"
