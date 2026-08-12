"""
Layer: Agent
Component: Tool Registry -- the single place that knows the mapping from a
tool NAME (as an LLM would name it in a tool call) to the real callable in
bridge/tool_functions.py. The agent loop and the deterministic skill runner
both go through call_tool() so neither one hardcodes which function backs
which name.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))

import tool_functions

# Mapping between the LLm anme for a tool and the actual callable function in tool_functions.py. 
TOOLS = {
    "set_gpio": tool_functions.set_gpio,
    "read_gpio": tool_functions.read_gpio,
    "ping": tool_functions.ping,
    "read_analog": tool_functions.read_analog,
    "set_pwm": tool_functions.set_pwm,
    "save_skill": tool_functions.save_skill,
    "wait": tool_functions.wait,
    "list_skills": tool_functions.list_skills,
    "get_skill": tool_functions.get_skill,
}


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool call by name. Unknown tools fail closed, not silently."""
    if name not in TOOLS: # tool doesnt exsist
        return {"success": False, "error": f"unknown tool: {name}", "raw_response": None}
    return TOOLS[name](**arguments)
