"""
llm_setup/step_a_test_connection.py  (OpenAI-compatible / Codex-server version)

The smallest possible test: confirms your local openai-api-server-via-codex
server is running and reachable, with zero other complexity (no tools, no
hardware, nothing).

Setup:
  1. Make sure the local server is running in a separate terminal:
       uvx openai-api-server-via-codex
     (leave that terminal open/running while you use this script)
  2. pip install openai python-dotenv
  3. In your .env file, set:
       OPENAI_BASE_URL=http://127.0.0.1:18080/v1
       OPENAI_API_KEY=dummy
     (the key is a placeholder the SDK requires -- the local server ignores
     it unless you configured --api-key when starting the server)
  4. python llm_setup/step_a_test_connection.py

Expected output: a short text reply, proving the connection through the
local Codex-backed server works.
"""

from dotenv import load_dotenv
load_dotenv()

import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

response = client.chat.completions.create(
    model="gpt-5.5",
    messages=[
        {"role": "user", "content": "Reply with exactly one short sentence confirming you're connected."}
    ],
)


print(response.model_dump_json(indent=2))

# print("[codex-server] ", response.choices[0].message.content)