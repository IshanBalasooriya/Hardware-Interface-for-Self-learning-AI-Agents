"""
llm_setup/step_b_test_tool_calling.py

Proves the tool-calling MECHANISM works, using a completely fake tool that
just prints to your terminal — no ESP32, no serial port, no real hardware
involved yet. The point is to confirm:

  1. Gemini correctly decides to call the tool (rather than just replying
     with text) when the goal implies it should.
  2. Gemini fills in the tool's arguments correctly.
  3. Your code correctly intercepts that request and "executes" it.

Later, the ONLY thing that changes is what set_led() actually does inside —
swap the print() for a real serial call to the ESP32, and everything above
it (the tool declaration, the model, the loop) stays exactly the same.

Setup: same as step_a (.env with GEMINI_API_KEY set, packages installed).
"""

from dotenv import load_dotenv
load_dotenv()

from google import genai
import json

client = genai.Client()

# --- Step 1: Define the function declaration (what the model sees) ---
set_led_declaration = {
    "type": "function", 
    "name": "set_led", # The identifier the model will refer to this tool with
    "description": "Turns an LED on or off.", # Plain english explaining what it does, which the LLM relies on to decide when to call it
    "parameters": {
        "type": "object",
        "properties": { # Describe the arguments the model can pass to the tool, including types and constraints
            "state": { # Name of the argument
                "type": "string",
                "enum": ["on", "off"], # Restriction on what values the model can pass
                "description": "Desired LED state.",
            }
        },
        "required": ["state"],
    },
}


# --- Step 2: The actual function of the tool your code will run (Completely simulated for now) ---
def set_led(state: str) -> dict:
    print(f"[simulation hardware v0.0] LED would now be turned {state.upper()}")
    return {"success": True, "state": state}


# --- Step 3: Ask the model to do something that should trigger the tool ---
interaction = client.interactions.create(
    model="gemini-3.5-flash",
    input="It's getting dark in here, please turn the LED on.",
    tools=[set_led_declaration], # List of tools the model can choose to call
)

# --- Step 4: Find the tool call the model wants to make, and execute it ---
fc_step = next((s for s in interaction.steps if s.type == "function_call"), None) # Find the first step that is a function call in .steps of interaction response object. This represents everything the model did in porducing its response (plain text, or toll calls through multiple steps)

if fc_step is None:
    print("[gemini] did not call a tool — check the prompt/tool description.")
else:
    print(f"[gemini] wants to call: {fc_step.name}({fc_step.arguments})")
    result = set_led(**fc_step.arguments) # Do the acttual tool call, passing the model's arguments to your function. 
    print(f"[result] {result}")

    # --- Step 5: Send the result back so the model can finish its reply ---
    final_interaction = client.interactions.create(
        model="gemini-3.5-flash",
        input=[
            {
                "type": "function_result",
                "name": fc_step.name,
                "call_id": fc_step.id,
                "result": [{"type": "text", "text": json.dumps(result)}], # 
            }
        ],
        tools=[set_led_declaration],
        previous_interaction_id=interaction.id, # Send the result referincing the og conversation so model has the full cintext.
    )
    print(f"[gemini] final reply: {final_interaction.output_text}")