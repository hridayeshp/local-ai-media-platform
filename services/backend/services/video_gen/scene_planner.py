import requests
import json

OLLAMA_URL = "http://host.docker.internal:11434/api/generate"
MODEL = "llama3"

def generate_scene_plan(user_prompt: str):
    system_prompt = f"""
You are a professional film director and video editor.

From the user prompt below, create a SHORT cinematic video plan.

Rules:
- 3 to 5 scenes only
- Each scene must have:
  - scene (number)
  - duration (seconds, integer 2–6)
  - prompt (visual description for image generation)
  - style (cinematic style keywords)
- Return VALID JSON ONLY
- Do NOT add explanations or markdown

User prompt:
{user_prompt}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": system_prompt,
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()

    raw = response.json()["response"]

    try:
        return json.loads(raw)
    except Exception:
        # Hard fail → better than silent bugs
        raise ValueError("LLM returned invalid JSON:\n" + raw)
