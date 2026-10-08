"""HUMAN check for stage 4: show five patterns on the real board and print the LED map for each."""

import argparse
import sys

from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import TransportError, open_transport
from config import CLOCK_PIN, DATA_PIN, FRAMES_LOG, LATCH_PIN, MSB_IS_LEFT, SERIAL_PORT, STATE_FILE

PAUSE_MS = 2000


def frame(row_values: list[int]) -> str:
    return "".join(f"{i + 1:02X}{value:02X}" for i, value in enumerate(row_values))


PATTERNS = [
    ("single corner LED (top-left)", frame([0x80, 0, 0, 0, 0, 0, 0, 0])),
    ("full top row", frame([0xFF, 0, 0, 0, 0, 0, 0, 0])),
    ("left column", frame([0x80] * 8)),
    ("checkerboard", frame([0xAA, 0x55] * 4)),
    ("heart", frame([0x00, 0x66, 0xFF, 0xFF, 0x7E, 0x3C, 0x18, 0x00])),
]


def print_rows(title: str, result: dict) -> None:
    print(f"\n{title}: success={result['success']}")
    if "decoded_state" in result:
        print("\n".join(result["decoded_state"]["rows"]))
    else:
        print(f"error: {result.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resync-only", action="store_true", help="connect, re-sync the saved picture, exit")
    args = parser.parse_args()

    if not SERIAL_PORT:
        print("SERIAL_PORT is not set in .env")
        return 1
    bridge = Bridge(open_transport(SERIAL_PORT), GridStore(STATE_FILE, FRAMES_LOG, MSB_IS_LEFT))
    try:
        bridge.start()
    except TransportError as e:
        print(f"Could not connect: {e}")
        return 1
    try:
        if args.resync_only:
            print_rows("re-synced picture", {"success": True, "decoded_state": bridge.store.current()})
            return 0
        for i, (name, data_hex) in enumerate(PATTERNS, start=1):
            print_rows(f"{i}. {name}", bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, 2, data_hex))
            bridge.wait(PAUSE_MS)
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
