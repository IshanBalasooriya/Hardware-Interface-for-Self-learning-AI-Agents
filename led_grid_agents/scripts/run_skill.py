"""Replay a saved skill on the real board: python -m scripts.run_skill <name> [key=value ...]"""

import argparse
import sys

from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import TransportError, open_transport
from config import FRAMES_LOG, MSB_IS_LEFT, SERIAL_PORT, SKILLS_DIR, STATE_FILE
from skills.runner import run_skill
from skills.store import SkillError, SkillStore


def parse_params(pairs: list[str], defaults: dict) -> dict:
    """key=value pairs; a value becomes an int when the skill's default for that key is an int."""
    params = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"expected key=value, got {pair!r}")
        params[key] = int(value) if isinstance(defaults.get(key), int) else value
    return params


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="skill name in skills/library/")
    parser.add_argument("params", nargs="*", help="parameter overrides as key=value")
    args = parser.parse_args()

    try:
        definition = SkillStore(SKILLS_DIR).get(args.name)
        params = parse_params(args.params, definition.get("params", {}))
    except (SkillError, ValueError) as e:
        print(f"Error: {e}")
        return 1
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
        result = run_skill(definition, bridge, params)
    finally:
        bridge.close()

    print(f"success={result['success']} steps_run={result.get('steps_run')} "
          f"stopped={result.get('stopped')} timed_out={result.get('timed_out')}")
    if "error" in result:
        print(f"error: {result['error']}")
    if "final_state" in result:
        print(f"final seq={result['final_state']['seq']}")
        print("\n".join(result["final_state"]["rows"]))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
