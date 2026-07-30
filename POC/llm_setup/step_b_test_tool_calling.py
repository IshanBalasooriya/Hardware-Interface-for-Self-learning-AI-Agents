"""
llm_setup/step_b_test_tool_calling.py  (OpenAI-compatible / Codex-server version)

Proves the tool-calling MECHANISM works, using a completely fake tool that
just prints to your terminal -- no ESP32, no serial port, no real hardware
involved yet. The point is to confirm:

  1. The model correctly decides to call the tool (rather than just
     replying with text) when the goal implies it should.
  2. The model fills in the tool's arguments correctly.
  3. Your code correctly intercepts that request and "executes" it.

Later, the ONLY thing that changes is what set_led() actually does inside --
swap the print() for a real serial call to the ESP32, and everything above
it (the tool declaration, the model, the loop) stays exactly the same.

Note on this provider's shape (different from Gemini):
  - Tool declarations are nested: {"type": "function", "function": {...}}
  - Tool calls come back via response.choices[0].message.tool_calls
  - Each tool call's .function.arguments is a JSON STRING, not an
    already-parsed dict -- you must json.loads() it yourself.

Setup: same as step_a (.env with OPENAI_BASE_URL / OPENAI_API_KEY set,
local server running, packages installed).
"""

from dotenv import load_dotenv
load_dotenv()

import os
import json
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

# --- Step 1: Define the tool (note the nested "function" key) ---
set_led_tool = {
    "type": "function",
    "function": {
        "name": "set_led",
        "description": "Turns an LED on or off.",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["on", "off"],
                    "description": "Desired LED state.",
                }
            },
            "required": ["state"],
        },
    },
}


# --- Step 2: The actual function your code will run (FAKE for now) ---
def set_led(state: str) -> dict:
    print(f"[fake hardware] LED would now be turned {state.upper()}")
    return {"success": True, "state": state}


# --- Step 3: Ask the model to do something that should trigger the tool ---
messages = [
    {"role": "user", "content": "It's getting dark in here, please turn the LED on."}
]

response = client.chat.completions.create(
    model="gpt-5.5",
    messages=messages,
    tools=[set_led_tool],
)

message = response.choices[0].message

# --- Step 4: Check whether the model requested a tool call ---
if not message.tool_calls:
    print("[codex-server] did not call a tool -- check the prompt/tool description.")
else:
    tool_call = message.tool_calls[0]
    # arguments is a JSON string here -- must parse it ourselves
    args = json.loads(tool_call.function.arguments)
    print(f"[codex-server] wants to call: {tool_call.function.name}({args})")

    result = set_led(**args)
    print(f"[result] {result}")

    # --- Step 5: Send the result back so the model can finish its reply ---
    # The model's own tool-call message must be included first, followed by
    # a "tool" role message referencing it by tool_call_id.
    messages.append(message)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result),
        }
    )

    final_response = client.chat.completions.create(
        model="gpt-5.5",
        messages=messages,
        tools=[set_led_tool],
    )
    print(f"[codex-server] final reply: {final_response.choices[0].message.content}")