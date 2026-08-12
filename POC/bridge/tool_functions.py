"""
Layer: Bridge
Component #2: Tool Functions -- provider-agnostic, real, callable Python
functions. These are what a tool call from ANY LLM provider eventually
resolves to and what the agent ends up calling. Uses the generic SET_GPIO/READ_GPIO
primitives, so adding a new simple digital device later means adding a new
function HERE, not new firmware.

Every function returns a consistently structured dict object with at least a "success" key (True/False) and a "raw_response" key (the raw text reply from the firmware). Other keys are tool-specific. Once there is many tools - anything consuking these results can rely on this shape inseatd of being tool specific.

save_skill() is the one exception -- it never talks to the firmware, so its
"raw_response" is always None; it still carries the key for shape consistency.
"""

import json
import os
import time

import serial_transport


def set_gpio(pin: int, value: int) -> dict:
    """
    Tool: set a digital pin HIGH (1) or LOW (0).
    Generic -- works for any allowed pin, not just one hardcoded device.
    """
    response = serial_transport.send(f"SET_GPIO {pin} {value}") # Cmnd construction & transmission into the form the firmware's parser(esp32_generic_functions/src/main.cpp) expects.
    success = response.startswith("OK") # True / False if the firmware's response starts with "OK".
    return {"success": success, "pin": pin, "value": value, "raw_response": response} # Returns as a dict object because donwstream agent loop and skill runner works with structred data


def read_gpio(pin: int) -> dict:
    """
    Tool: read a digital pin's current state.
    """
    response = serial_transport.send(f"READ_GPIO {pin}")
    success = response.startswith("OK")
    value = None
    if success and "VALUE=" in response: # Guardrail to ensure malformed or error reply wont crash this parse.
        value = int(response.split("VALUE=")[1]) # Extracts from the firmware reponse text
    return {"success": success, "pin": pin, "value": value, "raw_response": response} 


def ping() -> dict:
    """Tool/utility: basic connectivity check."""
    response = serial_transport.send("PING")
    return {"success": response.startswith("OK"), "raw_response": response}


def read_analog(pin: int) -> dict:
    """
    Tool: read an analog-capable pin's raw ADC value (0-4095 on ESP32).
    Generic -- works for any allowed analog pin, not just the light sensor.
    """
    response = serial_transport.send(f"READ_ADC {pin}")
    success = response.startswith("OK")
    value = None
    if success and "VALUE=" in response:
        value = int(response.split("VALUE=")[1])
    return {"success": success, "pin": pin, "value": value, "raw_response": response}


def set_pwm(pin: int, duty: int) -> dict:
    """
    Tool: set a PWM-capable pin's duty cycle (0-255).
    Generic -- works for any allowed PWM pin, not just the LED.
    """
    response = serial_transport.send(f"SET_PWM {pin} {duty}")
    success = response.startswith("OK")
    return {"success": success, "pin": pin, "duty": duty, "raw_response": response}


def wait(duration_ms: int) -> dict:
    """
    Tool: pure-Python delay -- no firmware round trip.
    Lets the agent compose a visibly-timed action sequence (e.g. a blink:
    set_gpio on -> wait -> set_gpio off -> wait) purely from round-trip
    primitive calls, with no new firmware command needed.
    """
    time.sleep(duration_ms / 1000)
    return {"success": True, "duration_ms": duration_ms}


def list_skills() -> dict:
    """
    Tool: list the names (without .json) of skills currently saved in the
    skill library. Lets the agent inspect what it has already saved.
    """
    os.makedirs(_SKILLS_DIR, exist_ok=True)
    names = sorted(f[:-5] for f in os.listdir(_SKILLS_DIR) if f.endswith(".json"))
    return {"success": True, "skills": names}


def get_skill(name: str) -> dict:
    """
    Tool: load a saved skill's full definition by name. This is what makes
    generalization genuine -- the agent inspects real, previously-persisted
    data rather than relying on conversation memory.
    """
    path = os.path.join(_SKILLS_DIR, f"{name}.json")
    if not os.path.exists(path):
        return {"success": False, "name": name, "error": "not found"}
    with open(path) as f:
        definition = json.load(f)
    return {"success": True, "name": name, "definition": definition}


_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills") # .abspath(_file_) gives the absolute path to the current file, dirname gives the name of the directory of the current file (one layer up the file hierarchy)

# Required fields per skill "type". "light_policy" is the original shape
# (target/tolerance/brightness/... from the self-tuning light task) and is
# also the default when a definition omits "type" entirely, so every
# existing caller (agent_loop.py's Stage 5-8 flow, which never sets
# "type") keeps validating exactly as before. "action_sequence" is the new
# shape used by the composition/generalization stages (blink_twice,
# blink_n_times): an ordered list of tool-call steps plus a version.
_REQUIRED_FIELDS_BY_TYPE = {
    "light_policy": {"target", "tolerance", "brightness", "iterations", "final_error", "version"},
    "action_sequence": {"actions", "version"},
}

# dict object is handed by LLM to create a new skill, and this function validates the dict has all the required fields before writing it to disk as JSON.
def save_skill(name: str, definition: dict) -> dict:
    """
    Tool: persist a discovered or composed skill to the skill library as
    JSON. The required fields depend on definition["type"] (defaults to
    "light_policy" if omitted, for backward compatibility) -- an
    incomplete or unrecognized-type skill is a validation error, not a
    silent write.
    """
    skill_type = definition.get("type", "light_policy")
    if skill_type not in _REQUIRED_FIELDS_BY_TYPE:
        return {
            "success": False,
            "name": name,
            "error": f"unknown skill type: {skill_type!r} (expected one of {sorted(_REQUIRED_FIELDS_BY_TYPE)})",
            "raw_response": None,
        }

    required_fields = _REQUIRED_FIELDS_BY_TYPE[skill_type]
    missing = required_fields - definition.keys() # Set difference to find any required fields that are not present in the provided definition.
    if missing:
        return {"success": False, "name": name, "error": f"missing fields: {sorted(missing)}", "raw_response": None}

    os.makedirs(_SKILLS_DIR, exist_ok=True) # Mke sure the skills directory exists before trying to write a file into it. exist_ok=True means it won't raise an error if the directory already exists.
    path = os.path.join(_SKILLS_DIR, f"{name}.json") # Creating thr skill name
    payload = {"name": name, **definition} # key value from definition ** (unpacked) and filled to required fields
    with open(path, "w") as f:
        json.dump(payload, f, indent=2) # Serializes the payload dict to JSON and writes it to the file with indentation for readability.

    return {"success": True, "name": name, "path": path, "raw_response": None}