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
# Default model - Using DeepSeek (reliable free tier)
# For other options see https://openrouter.ai/models
DEFAULT_MODEL = "deepseek/deepseek-chat:free"


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
    max_tokens: int = 1100,
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
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"OpenRouter returned no choices (model={model}). "
                "Try --model deepseek/deepseek-v3-base:free or check https://openrouter.ai/models"
            )
        content = choices[0].get("message", {}).get("content") or ""
        return content.strip() if isinstance(content, str) else ""
    except requests.HTTPError as e:
        msg = (
            f"OpenRouter API error: {e.response.status_code} {e.response.reason}. "
            "If 404, the model ID may be invalid — try --model deepseek/deepseek-v3-base:free or see https://openrouter.ai/models"
        )
        raise RuntimeError(msg) from e
    except Exception as e:
        print(f"[OpenRouter error] {e}")
        raise


def generate_script(
    topic: str,
    script_prompt_template: str,
    model: str = DEFAULT_MODEL,
) -> Optional[Dict]:
    """Generate structured script (title + list of sections). Raises on API or parse errors."""
    prompt = script_prompt_template.replace("{{topic}}", topic.strip())
    raw = query_ollama(prompt, model=model)

    if not raw:
        raise RuntimeError(
            "OpenRouter returned no content. "
            "1) Check OPENROUTER_API_KEY is set in .env or environment. "
            "2) Try --model deepseek/deepseek-v3-base:free (the model may return empty). "
            "3) See https://openrouter.ai/models for available models."
        )

    # Try to extract JSON — many models wrap it in ```json ... ```
    to_parse = raw
    if "```json" in to_parse:
        to_parse = to_parse.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in to_parse:
        parts = to_parse.split("```")
        if len(parts) >= 2:
            to_parse = parts[1].strip()

    try:
        data = json.loads(to_parse)
    except json.JSONDecodeError as e:
        snippet = (to_parse or raw)[:600].replace("\n", " ")
        raise RuntimeError(
            f"Script response is not valid JSON: {e}. "
            f"Raw (first 600 chars): {snippet!r}"
        ) from e

    if not isinstance(data, dict) or "sections" not in data:
        raise RuntimeError(
            "Script JSON must include a 'sections' list. "
            f"Got keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    return data