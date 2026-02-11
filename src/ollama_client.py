# src/ollama_client.py
# NOTE: This module now talks to OpenRouter instead of Ollama so that you can use DeepSeek.

import json
import os
from typing import Dict, Optional

from dotenv import load_dotenv
import requests


# Load variables from .env (if present) before reading OPENROUTER_API_KEY
load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-r1-0528:free"  # adjust to your preferred DeepSeek model id


def _get_openrouter_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Create an API key on OpenRouter and set it before running the pipeline."
        )
    return key


def query_ollama(  # kept name for compatibility with existing imports
    prompt: str,
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    temperature: float = 0.35,
    max_tokens: int = 2048,
) -> str:
    """Call DeepSeek via OpenRouter and return the assistant message content."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {_get_openrouter_api_key()}",
        "Content-Type": "application/json",
        # Optional but recommended identifiers
        "HTTP-Referer": "http://localhost",
        "X-Title": "AI-Autonomous-Shorts-Videos-Content-Creator",
    }

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[OpenRouter / DeepSeek error] {e}")
        return ""


def generate_script(
    topic: str,
    script_prompt_template: str,
    model: str = DEFAULT_MODEL,
) -> Optional[Dict]:
    """Generate structured script (title + list of sections)."""
    prompt = script_prompt_template.replace("{{topic}}", topic.strip())
    raw = query_ollama(prompt, model=model)

    if not raw:
        return None

    # Try to extract JSON — many models wrap it in ```json ... ```
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].strip()

    try:
        data = json.loads(raw)
        return data
    except json.JSONDecodeError as e:
        print("[Script parse failed]", e)
        print("Raw output:\n", raw)
        return None