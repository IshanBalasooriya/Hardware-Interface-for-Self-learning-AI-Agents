"""Run one prompt through the agent from the terminal: python -m agent.cli "Draw a heart" """

import argparse
import json
import logging
import signal
import sys
import threading

from agent.loop import run_agent
from agent.tools import AgentContext
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import TransportError, open_transport
from config import FRAMES_LOG, MSB_IS_LEFT, SERIAL_PORT, SKILLS_DIR, STATE_FILE
from skills.store import SkillStore


def print_event(event: dict) -> None:
    kind = event["type"]
    if kind == "agent_message":
        print(f"[agent] {event['text']}")
    elif kind == "tool_call":
        print(f"[call {event['call_id']}] {event['tool']} {json.dumps(event['args'])}")
    elif kind == "tool_result":
        result = event["result"]
        shown = {k: v for k, v in result.items() if k != "decoded_state"}
        print(f"[result {event['call_id']}] {event['tool']} {event['duration_ms']}ms {json.dumps(shown)}")
        if "decoded_state" in result:
            state = result["decoded_state"]
            print(f"  seq={state['seq']} display={state['display']}")
            print("\n".join(f"  {row}" for row in state["rows"]))
    elif kind == "skill_saved":
        print(f"[skill_saved] {event['name']} v{event['version']}")
    else:
        print(json.dumps(event))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="what the agent should do")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not SERIAL_PORT:
        print("SERIAL_PORT is not set in .env")
        return 1
    bridge = Bridge(open_transport(SERIAL_PORT), GridStore(STATE_FILE, FRAMES_LOG, MSB_IS_LEFT))
    try:
        bridge.start()
    except TransportError as e:
        print(f"Could not connect: {e}")
        return 1

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    ctx = AgentContext(bridge=bridge, skills=SkillStore(SKILLS_DIR), should_stop=stop.is_set)
    try:
        result = run_agent(args.prompt, ctx, print_event)
    finally:
        bridge.close()

    print(f"status={result['status']}")
    print(f"summary: {result['summary']}")
    if result["error"]:
        print(f"error: {result['error']}")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
