"""Gemini API wrapper with exponential backoff (max 3 retries)."""
import time
import json
import logging
import google.generativeai as genai
import config

logger = logging.getLogger(__name__)

genai.configure(api_key=config.GEMINI_API_KEY)
_model = genai.GenerativeModel("gemini-1.5-flash")


def call_gemini(prompt: str, expect_json: bool = False, max_retries: int = 3) -> str:
    """Call Gemini and return the text response.

    If expect_json=True the response is validated as JSON and markdown fences
    are stripped before returning.  Raises on final failure.
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            response = _model.generate_content(prompt)
            text = response.text.strip()
            if expect_json:
                # Strip ```json ... ``` wrappers that Gemini sometimes adds
                if text.startswith("```"):
                    inner = text.split("```", 2)
                    text = inner[2] if len(inner) > 2 else inner[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip().rstrip("`").strip()
                json.loads(text)  # validate — raises if malformed
            return text
        except Exception as exc:
            last_err = exc
            wait = 2 ** attempt
            logger.warning("Gemini attempt %d/%d failed: %s — retrying in %ds",
                           attempt + 1, max_retries, exc, wait)
            if attempt < max_retries - 1:
                time.sleep(wait)

    logger.error("Gemini failed after %d attempts: %s", max_retries, last_err)
    raise RuntimeError(f"Gemini unavailable: {last_err}") from last_err
