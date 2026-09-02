import os
import requests

api_key = os.environ["FEATHERLESS_API_KEY"]

response = requests.post(
    "https://api.featherless.ai/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": "zai-org/GLM-5.2",
        "messages": [
            {
                "role": "user",
                "content": "Say hello and confirm that Featherless is working."
            }
        ],
    },
)

print("Status:", response.status_code)
print(response.text)