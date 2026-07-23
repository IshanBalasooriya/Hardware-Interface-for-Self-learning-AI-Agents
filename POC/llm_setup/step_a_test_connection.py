"""
llm_setup/step_a_test_connection.py

The smallest possible test: confirms your Gemini API key and environment
work at all, with zero other complexity (no tools, no hardware, nothing).

Setup:
  1. Get a free API key: https://aistudio.google.com/apikey
  2. pip install google-genai python-dotenv
  3. Copy .env.example to .env (in the POC/ root) and paste your key in
  4. python llm_setup/step_a_test_connection.py

Expected output: a short text reply from the model, proving the connection works.
"""

from dotenv import load_dotenv
load_dotenv()  # reads POC/.env and loads GEMINI_API_KEY into the environment

from google import genai

client = genai.Client()  # picks up GEMINI_API_KEY automatically

# interaction object contains the model's sturcture response object (reply + metadata)
interaction = client.interactions.create( # intercations fn sends the request to the model
    model="gemini-3.5-flash", # The Model
    input="Reply with exactly one short sentence confirming you're connected.", # The prompt
    #input="Hi there", 

)

print("[gemini] ", interaction.output_text) # Prints the model's plain text of the reply 
