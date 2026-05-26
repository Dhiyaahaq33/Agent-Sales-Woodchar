"""Telegram Bot notification helper."""
import logging
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a text message to the configured chat. Returns True on success."""
    try:
        resp = requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Telegram message sent (%d chars)", len(text))
        return True
    except Exception as exc:
        logger.error("Telegram sendMessage failed: %s", exc)
        return False


def send_document(file_path: str, caption: str = "") -> bool:
    """Send a file to the configured chat. Returns True on success."""
    try:
        with open(file_path, "rb") as fh:
            resp = requests.post(
                f"{_API}/sendDocument",
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": (Path(file_path).name, fh)},
                timeout=60,
            )
        resp.raise_for_status()
        logger.info("Telegram document sent: %s", file_path)
        return True
    except Exception as exc:
        logger.error("Telegram sendDocument failed: %s", exc)
        return False
