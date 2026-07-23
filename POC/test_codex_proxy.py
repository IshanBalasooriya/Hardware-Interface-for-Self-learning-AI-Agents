from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18080/v1",
    api_key="dummy",
)

response = client.responses.create(
    model="gpt-5.5",
    input="Hello there who are you?",
    reasoning={"effort": "low"},
)

print(response.output_text)

