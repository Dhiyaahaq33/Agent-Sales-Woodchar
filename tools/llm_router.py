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
    """Call NVIDIA NIM via OpenAI-compatible endpoint.
    Tries the primary model first, then the fast/small model if that times out
    (large NIM models can have slow cold-starts on shared free-tier infra).
    """
    client = _get_nvidia()
    models_to_try = [config.NVIDIA_MODEL]
    if config.NVIDIA_MODEL_FAST and config.NVIDIA_MODEL_FAST != config.NVIDIA_MODEL:
        models_to_try.append(config.NVIDIA_MODEL_FAST)

    last_err = None
    for model in models_to_try:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.15,
                top_p=1.0,
                max_tokens=2048,
                frequency_penalty=0,
                presence_penalty=0,
                timeout=30,
            )
            return completion.choices[0].message.content.strip()
        except Exception as exc:
            last_err = exc
            logger.warning("NVIDIA model %s failed: %s — trying next", model, exc)
    raise last_err


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
    # Skip providers whose API key is not configured
    providers = []
    if config.GEMINI_API_KEY:
        providers.append(("Gemini", _call_gemini_raw))
    if config.GROQ_API_KEY:
        providers.append(("Groq", _call_groq_raw))
    if config.NVIDIA_API_KEY:
        providers.append(("NVIDIA", _call_nvidia_raw))

    if not providers:
        raise RuntimeError("No LLM provider configured — set at least GEMINI_API_KEY in .env")

    last_err = None

    for provider_name, provider_fn in providers:
        for retry in range(max_retries):
            try:
                text = provider_fn(prompt)

                if expect_json:
                    text = _strip_json_fences(text)
                    json.loads(text)  # validate

                if provider_name != providers[0][0]:
                    logger.info("LLM routed to %s (primary unavailable)", provider_name)

                return text

            except Exception as exc:
                last_err = exc
                err_str  = str(exc).lower()
                is_rate_limit = "429" in err_str or "rate" in err_str or "quota" in err_str

                if is_rate_limit:
                    logger.warning(
                        "%s rate-limited (attempt %d) — switching provider: %s",
                        provider_name, retry + 1, exc,
                    )
                    break  # skip to next provider immediately
                else:
                    wait = 2 ** retry
                    logger.warning(
                        "%s error (attempt %d/%d): %s — retry in %ds",
                        provider_name, retry + 1, max_retries, exc, wait,
                    )
                    if retry < max_retries - 1:
                        time.sleep(wait)

    logger.error("All LLM providers failed. Last error: %s", last_err)
    raise RuntimeError(f"All LLM providers unavailable: {last_err}") from last_err
