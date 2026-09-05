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
            "name": "read_pwm",
            "description": "Read a PWM-capable pin's actual current duty cycle (0-255) from hardware. Only works on pins the firmware allowlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "integer", "description": "PWM-capable pin number."},
                },
                "required": ["pin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_skill",
            "description": (
                "Persist a discovered or composed skill to the skill library so it can be replayed "
                "later without an LLM call. Two skill types are supported, chosen via definition.type: "
                "'light_policy' (the default if type is omitted) needs target, tolerance, brightness, "
                "iterations, final_error, version. 'action_sequence' needs an ordered 'actions' list "
                "of {tool, args} tool-call steps, plus version."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name, e.g. 'maintain_light' or 'blink_twice'."},
                    "definition": {
                        "type": "object",
                        "description": (
                            "The skill's contents. Set 'type' to 'light_policy' or 'action_sequence' -- "
                            "omitting 'type' defaults to 'light_policy'."
                        ),
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["light_policy", "action_sequence"],
                                "description": "Which skill shape this is. Defaults to 'light_policy' if omitted.",
                            },
                            "target": {"type": "integer", "description": "light_policy only."},
                            "tolerance": {"type": "integer", "description": "light_policy only."},
                            "brightness": {"type": "integer", "description": "light_policy only."},
                            "iterations": {"type": "integer", "description": "light_policy only."},
                            "final_error": {"type": "integer", "description": "light_policy only."},
                            "actions": {
                                "type": "array",
                                "description": "action_sequence only -- ordered list of tool-call steps.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "tool": {"type": "string", "description": "Tool name, e.g. 'set_gpio' or 'wait'."},
                                        "args": {"type": "object", "description": "Arguments for that tool call."},
                                    },
                                    "required": ["tool", "args"],
                                },
                            },
                            "version": {"type": "integer", "description": "Required for both types."},
                        },
                        "required": ["version"],
                    },
                },
                "required": ["name", "definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": (
                "Pause for a duration in milliseconds. Pure software delay -- no hardware round trip. "
                "Use this to space out on/off calls so a sequence like a blink is visibly timed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_ms": {"type": "integer", "description": "Milliseconds to wait."},
                },
                "required": ["duration_ms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List the names of skills currently saved in the skill library.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": (
                "Load a previously saved skill's full definition by name, so you can inspect exactly "
                "what it contains instead of relying on what you remember saying earlier."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name, e.g. 'blink_twice'."},
                },
                "required": ["name"],
            },
        },
    },
]
