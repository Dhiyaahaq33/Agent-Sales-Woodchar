"""LLM Router — Gemini → Groq → NVIDIA dengan auto-switching.

Semua agen harus memanggil llm_router.call() bukan call_gemini() langsung.
Routing logic:
  1. Coba Gemini 2.0 Flash (15 RPM free)
  2. Jika 429/error → switch ke Groq llama-3.3-70b (30 RPM free)
  3. Jika Groq juga fail → switch ke NVIDIA Nemotron-49B (jika NVIDIA_API_KEY di-set)
  4. Jika semua fail → exponential backoff lalu RuntimeError
"""
import json
import logging
import time

import config

logger = logging.getLogger(__name__)

# ── Lazy-init clients ─────────────────────────────────────────────────────────
_gemini_client = None
_groq_client   = None
_nvidia_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


def _get_groq():
    global _groq_client
    if _groq_client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not set — cannot use Groq fallback.")
        from groq import Groq
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


def _get_nvidia():
    global _nvidia_client
    if _nvidia_client is None:
        if not config.NVIDIA_API_KEY:
            raise RuntimeError("NVIDIA_API_KEY not set — cannot use NVIDIA fallback.")
        from openai import OpenAI
        _nvidia_client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=config.NVIDIA_API_KEY,
        )
    return _nvidia_client


# ── Internal callers ──────────────────────────────────────────────────────────

def _call_gemini_raw(prompt: str) -> str:
    """Call Gemini and return raw text. Raises on any error."""
    client = _get_gemini()
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
    )
    return response.text.strip()


def _call_groq_raw(prompt: str) -> str:
    """Call Groq llama-3.3-70b and return raw text. Raises on any error."""
    client = _get_groq()
    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1024,
    )
    return completion.choices[0].message.content.strip()


def _call_nvidia_raw(prompt: str) -> str:
    """Call NVIDIA Nemotron-49B via OpenAI-compatible endpoint."""
    client = _get_nvidia()
    completion = client.chat.completions.create(
        model=config.NVIDIA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        top_p=0.95,
        max_tokens=4096,
        frequency_penalty=0,
        presence_penalty=0,
    )
    return completion.choices[0].message.content.strip()


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers Gemini/Groq sometimes add."""
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[2] if len(parts) > 2 else parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return text


# ── Public API ────────────────────────────────────────────────────────────────

def call(prompt: str, expect_json: bool = False, max_retries: int = 3) -> str:
    """Route LLM call: Gemini → Groq → NVIDIA → exponential backoff.

    Args:
        prompt: Full prompt string.
        expect_json: Validate and strip fences from JSON response.
        max_retries: Max total attempts across all providers.

    Returns:
        Text response from whichever provider succeeded.

    Raises:
        RuntimeError: If all providers and retries are exhausted.
    """
    providers = [
        ("Gemini", _call_gemini_raw),
        ("Groq",   _call_groq_raw),
        ("NVIDIA", _call_nvidia_raw),
    ]

    last_err = None
    attempt  = 0

    for provider_name, provider_fn in providers:
        for retry in range(max_retries):
            try:
                text = provider_fn(prompt)

                if expect_json:
                    text = _strip_json_fences(text)
                    json.loads(text)  # validate

                if provider_name != "Gemini":
                    logger.info("LLM routed to %s (Gemini unavailable)", provider_name)

                return text

            except Exception as exc:
                last_err = exc
                err_str  = str(exc).lower()
                is_rate_limit = "429" in err_str or "rate" in err_str or "limit" in err_str

                if is_rate_limit:
                    # Rate-limit: switch provider immediately
                    logger.warning(
                        "%s rate-limited (attempt %d) — switching provider: %s",
                        provider_name, retry + 1, exc,
                    )
                    break  # try next provider
                else:
                    # Other error: exponential backoff within same provider
                    wait = 2 ** retry
                    logger.warning(
                        "%s error (attempt %d/%d): %s — retry in %ds",
                        provider_name, retry + 1, max_retries, exc, wait,
                    )
                    if retry < max_retries - 1:
                        time.sleep(wait)
                    attempt += 1

    # All providers exhausted
    logger.error("All LLM providers failed. Last error: %s", last_err)
    raise RuntimeError(f"All LLM providers unavailable: {last_err}") from last_err
