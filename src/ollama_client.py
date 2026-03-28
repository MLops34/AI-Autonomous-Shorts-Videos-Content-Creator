# src/ollama_client.py
# LLM backend: OpenRouter (default DeepSeek) or Gemini when model id starts with gemini-.

import importlib
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
import requests


load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
# Standard DeepSeek V3 chat route (uses account credits). :free slugs often have "No endpoints found".
_DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-chat"
DEFAULT_MODEL = (os.getenv("OPENROUTER_MODEL", "").strip() or _DEFAULT_OPENROUTER_MODEL)


def _openrouter_model_candidates(preferred: str) -> list[str]:
    """Models to try in order when OpenRouter returns 404 / no endpoints."""
    seen: set[str] = set()
    out: list[str] = []
    raw = os.getenv("OPENROUTER_MODEL_FALLBACKS", "").strip()
    if raw:
        extras = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        extras = [
            "deepseek/deepseek-v3.2",
            "deepseek/deepseek-chat-v3-0324",
            "deepseek/deepseek-r1",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-2-9b-it:free",
        ]
    for m in [preferred] + extras:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _is_gemini_model(model: str) -> bool:
    return model.strip().lower().startswith("gemini-")


def _get_openrouter_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Create an API key on OpenRouter and set it before using a non-Gemini model."
        )
    return key


def _get_gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
            "Get a key at https://aistudio.google.com/apikey and add it to .env for Gemini models."
        )
    return key


def _gemini_response_text(response) -> str:
    text = getattr(response, "text", None)
    if text and isinstance(text, str):
        return text.strip()
    cands = getattr(response, "candidates", None) or []
    if not cands:
        return ""
    parts = getattr(cands[0].content, "parts", None) or []
    chunks: list[str] = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            chunks.append(t)
    return "".join(chunks).strip()


# Script JSON (6 sections + tall keys) needs a high ceiling; Mermaid stays at default max_tokens.
SCRIPT_MAX_TOKENS = 8192


class ScriptSection(BaseModel):
    """One script beat; matches config/prompts/01_scripts.txt output shape."""

    model_config = {"extra": "ignore"}

    heading: str
    text: str
    duration_sec: float
    word_count: int


class ScriptPayload(BaseModel):
    """Full script JSON; Gemini uses this as response_schema so strings stay valid JSON."""

    model_config = {"extra": "ignore"}

    title: str
    sections: list[ScriptSection] = Field(
        ...,
        min_length=6,
        max_length=6,
    )


def _query_gemini(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.35,
    max_tokens: int = 1100,
    response_mime_type: Optional[str] = None,
    response_schema: Any = None,
) -> str:
    try:
        genai: Any = importlib.import_module("google.genai")
        gtypes: Any = importlib.import_module("google.genai.types")
    except ImportError as e:
        raise RuntimeError(
            "Gemini requires the SDK. Install with: pip install google-genai"
        ) from e

    client = genai.Client(api_key=_get_gemini_api_key())
    config_kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system_prompt:
        config_kwargs["system_instruction"] = system_prompt
    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema
    config = gtypes.GenerateContentConfig(**config_kwargs)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )
    return _gemini_response_text(response)


def _query_openrouter(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.35,
    max_tokens: int = 1100,
) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {_get_openrouter_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://openrouter.ai/",
        "X-Title": "AI-Autonomous-Shorts-Videos-Content-Creator",
    }

    candidates = _openrouter_model_candidates(model)
    last_404_msg = ""
    for candidate in candidates:
        body = {
            "model": candidate,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=body, timeout=120)
        if resp.status_code == 404:
            try:
                err = resp.json().get("error") or {}
                last_404_msg = str(err.get("message") or resp.text[:400])
            except Exception:
                last_404_msg = (resp.text or "")[:400]
            continue
        if not resp.ok:
            resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"OpenRouter returned no choices (model={candidate}). "
                "Check https://openrouter.ai/models for valid model ids."
            )
        content = choices[0].get("message", {}).get("content") or ""
        text = content.strip() if isinstance(content, str) else ""
        if candidate != model:
            print(
                f"[OpenRouter] Requested {model!r} has no endpoint; "
                f"using {candidate!r} instead."
            )
        return text

    raise RuntimeError(
        "OpenRouter returned 404 / no endpoints for every model tried: "
        f"{candidates}. "
        "Free models often have zero providers; paid routes need credits on your OpenRouter account. "
        "Open https://openrouter.ai/models , choose a model that lists active providers, "
        "then set OPENROUTER_MODEL=<exact id> in .env (optional comma-list in OPENROUTER_MODEL_FALLBACKS). "
        f"Last error: {last_404_msg}"
    )


def query_ollama(  # kept name for compatibility with existing imports
    prompt: str,
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    temperature: float = 0.35,
    max_tokens: int = 1100,
    response_mime_type: Optional[str] = None,
    response_schema: Any = None,
) -> str:
    """Call Gemini or OpenRouter and return the assistant text."""
    if _is_gemini_model(model):
        try:
            return _query_gemini(
                prompt,
                model=model,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_mime_type=response_mime_type,
                response_schema=response_schema,
            )
        except Exception as e:
            print(f"[Gemini error] {e}")
            raise

    try:
        return _query_openrouter(
            prompt,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        code = getattr(resp, "status_code", "?") if resp is not None else "?"
        reason = getattr(resp, "reason", "") if resp is not None else ""
        detail = ""
        if resp is not None:
            try:
                detail = (resp.text or "").strip()[:1200]
            except Exception:
                pass
        msg = (
            f"OpenRouter API error: {code} {reason} (model={model!r}). "
            f"If 404, pick a current id from https://openrouter.ai/models — "
            f"defaults to {_DEFAULT_OPENROUTER_MODEL!r}; set OPENROUTER_MODEL in .env to override."
        )
        if detail:
            msg += f" API detail: {detail}"
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
    # Large token budget: 6 sections + narration often exceeded 1100 tokens (truncated JSON).
    use_gemini = _is_gemini_model(model)
    mime = "application/json" if use_gemini else None
    schema = ScriptPayload if use_gemini else None
    raw = query_ollama(
        prompt,
        model=model,
        max_tokens=SCRIPT_MAX_TOKENS,
        response_mime_type=mime,
        response_schema=schema,
    )

    if not raw:
        hint = (
            "1) OpenRouter: OPENROUTER_API_KEY + credits for paid models; "
            "set OPENROUTER_MODEL to an id from openrouter.ai/models if defaults fail. "
            "2) Gemini: GEMINI_API_KEY + --model gemini-2.5-flash."
        )
        raise RuntimeError(f"The LLM returned no content. {hint}")

    to_parse = raw
    if "```json" in to_parse:
        to_parse = to_parse.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in to_parse:
        parts = to_parse.split("```")
        if len(parts) >= 2:
            to_parse = parts[1].strip()

    try:
        payload = ScriptPayload.model_validate_json(to_parse)
    except (json.JSONDecodeError, ValidationError) as e:
        snippet = (to_parse or raw)[:800].replace("\n", " ")
        raise RuntimeError(
            f"Script response is not valid structured JSON: {e}. "
            f"Raw (first 800 chars): {snippet!r}"
        ) from e

    return payload.model_dump()
