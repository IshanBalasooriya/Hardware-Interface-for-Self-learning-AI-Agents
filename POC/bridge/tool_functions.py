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

import serial_transport


def set_gpio(pin: int, value: int) -> dict:
    """
    Tool: set a digital pin HIGH (1) or LOW (0).
    Generic -- works for any allowed pin, not just one hardcoded device.
    """
    response = serial_transport.send(f"SET_GPIO {pin} {value}") # Cmnd construction & transmission into the form the firmware's parser expects.
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


_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
_REQUIRED_SKILL_FIELDS = {"target", "tolerance", "brightness", "iterations", "final_error", "version"}


def save_skill(name: str, definition: dict) -> dict:
    """
    Tool: persist a discovered control policy to the skill library as JSON.
    Validates the definition carries the fields needed to reuse and evolve
    it (target, tolerance, brightness, iterations, final_error, version)
    before writing -- an incomplete skill is a validation error, not a
    silent write.
    """
    missing = _REQUIRED_SKILL_FIELDS - definition.keys()
    if missing:
        return {"success": False, "name": name, "error": f"missing fields: {sorted(missing)}", "raw_response": None}

    os.makedirs(_SKILLS_DIR, exist_ok=True)
    path = os.path.join(_SKILLS_DIR, f"{name}.json")
    payload = {"name": name, **definition}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

    return {"success": True, "name": name, "path": path, "raw_response": None}