"""
Layer: Agent
Component: Tool Declarations -- OpenAI-format function-calling schemas for
the tools in agent/registry.py. Kept deliberately generic (pin numbers only,
no device names) so the LLM does the reasoning step of mapping a goal to the
right primitive + pin; which pin means what is domain context supplied
separately in the system prompt, not baked into the tool name or schema.

Shape note (this provider): tool declarations are nested under
{"type": "function", "function": {...}}, per llm_setup/step_b_test_tool_calling.py.
"""

TOOL_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Check whether the hardware is connected and responding.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_gpio",
            "description": "Set a digital pin HIGH (1) or LOW (0). Only works on pins the firmware allowlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "integer", "description": "GPIO pin number."},
                    "value": {"type": "integer", "enum": [0, 1], "description": "1 for HIGH, 0 for LOW."},
                },
                "required": ["pin", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_gpio",
            "description": "Read a digital pin's current state (0 or 1). Only works on pins the firmware allowlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "integer", "description": "GPIO pin number."},
                },
                "required": ["pin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_analog",
            "description": "Read an analog-capable pin's raw ADC value (0-4095). Only works on pins the firmware allowlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "integer", "description": "Analog-capable pin number."},
                },
                "required": ["pin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_pwm",
            "description": "Set a PWM-capable pin's duty cycle. Only works on pins the firmware allowlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "integer", "description": "PWM-capable pin number."},
                    "duty": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Duty cycle, 0 (off) to 255 (full)."},
                },
                "required": ["pin", "duty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_skill",
            "description": (
                "Persist a discovered control policy to the skill library so it can be replayed later "
                "without an LLM call. The definition must include: target, tolerance, brightness, "
                "iterations, final_error, version."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name, e.g. 'maintain_light' or 'maintain_light_v2'."},
                    "definition": {
                        "type": "object",
                        "description": "The policy details: target, tolerance, brightness, iterations, final_error, version.",
                        "properties": {
                            "target": {"type": "integer"},
                            "tolerance": {"type": "integer"},
                            "brightness": {"type": "integer"},
                            "iterations": {"type": "integer"},
                            "final_error": {"type": "integer"},
                            "version": {"type": "integer"},
                        },
                        "required": ["target", "tolerance", "brightness", "iterations", "final_error", "version"],
                    },
                },
                "required": ["name", "definition"],
            },
        },
    },
]
