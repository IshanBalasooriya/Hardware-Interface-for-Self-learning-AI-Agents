"""All constants for the LED grid system (master section 6.1)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_PIN = 25
CLOCK_PIN = 26
LATCH_PIN = 27
GROUP_SIZE = 2
MSB_IS_LEFT = True  # calibrated in stage 3, see docs/WIRING.md

BAUD = 115200
SERIAL_TIMEOUT_S = 2.0
SERIAL_PORT = os.getenv("SERIAL_PORT")

DEFAULT_INTENSITY = 2
MAX_SHIFT_BYTES = 64
MAX_WAIT_MS = 10000
MAX_HISTORY = 30
MAX_TURNS = 20
SKILL_TIME_CAP_S = 30

AGENT_MODE = os.getenv("AGENT_MODE", "multi")   # "multi" | "single"
MAX_ROUNDS = 3
RUN_TIME_CAP_S = 300
MAX_REPLAY_SKILLS = 8
MAX_REPLAY_HOLD_MS = 5000
MAX_PLAN_STAGES = 8
MAX_EVIDENCE_FRAMES = 80

LOG_DIR = BASE_DIR / "logs"
STATE_FILE = LOG_DIR / "shift_state.json"
FRAMES_LOG = LOG_DIR / "shift_frames.jsonl"
EVENTS_LOG = LOG_DIR / "sample_events.jsonl"
SKILLS_DIR = BASE_DIR / "skills" / "library"

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:18080/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "dummy")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")

WAKE_HEX = "0F0009000B070A020C01"
CLEAR_HEX = "01000200030004000500060007000800"
