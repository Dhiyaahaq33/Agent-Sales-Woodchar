"""Configuration v2.0 — loads all .env variables with startup validation.

PT Cahaya Woodchar International — AI Sales Agent System
"""
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
DB_DIR = BASE_DIR / "db"
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
PROPOSALS_DIR = BASE_DIR / "proposals"
DASHBOARD_DIR = BASE_DIR / "dashboard"

# ── LLM ───────────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Gmail ─────────────────────────────────────────────────────────────────────
GMAIL_ADDRESS          = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD     = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", "credentials.json")

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Company ───────────────────────────────────────────────────────────────────
COMPANY_NAME  = os.getenv("COMPANY_NAME", "PT Cahaya Woodchar International")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "")
SENDER_NAME   = os.getenv("SENDER_NAME", "Muhammad Yahya")
COMPANY_LOCATION = os.getenv("COMPANY_LOCATION", "Lampung Utara, Lampung, Indonesia")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", str(DB_DIR / "crm.db"))

# ── External APIs ─────────────────────────────────────────────────────────────
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "")

# ── Gmail SMTP ────────────────────────────────────────────────────────────────
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ── Operational Limits ────────────────────────────────────────────────────────
MAX_EMAILS_PER_DAY      = int(os.getenv("MAX_EMAILS_PER_DAY", "200"))
EMAIL_DELAY_MIN         = 30
EMAIL_DELAY_MAX         = 90
SCRAPER_DELAY_MIN       = 2
SCRAPER_DELAY_MAX       = 5
LEAD_SCORE_THRESHOLD    = 40
MAX_TOUCHES             = int(os.getenv("MAX_TOUCHES", "6"))        # v2: 6 touches
FOLLOWUP_DELAYS_DAYS    = [3, 5, 8, 12, 17, 21]

# ── Feature Flags (v2) ────────────────────────────────────────────────────────
WHATSAPP_ENABLED         = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
LINKEDIN_ENABLED         = os.getenv("LINKEDIN_ENABLED", "false").lower() == "true"
NEGOTIATION_AUTO_APPROVE = os.getenv("NEGOTIATION_AUTO_APPROVE", "false").lower() == "true"
SEND_TIME_OPTIMIZE       = os.getenv("SEND_TIME_OPTIMIZE", "true").lower() == "true"
AB_TEST_PERCENTAGE       = int(os.getenv("AB_TEST_PERCENTAGE", "20"))
MAX_EMAILS_PER_DOMAIN    = int(os.getenv("MAX_EMAILS_PER_DOMAIN", "3"))
BOUNCE_RATE_THRESHOLD    = float(os.getenv("BOUNCE_RATE_THRESHOLD", "5.0"))

# ── Required vars for startup validation ──────────────────────────────────────
_REQUIRED = [
    "GEMINI_API_KEY",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def validate_config() -> None:
    """Exit with error if any required env var is missing."""
    missing = [v for v in _REQUIRED if not globals().get(v)]
    if missing:
        logging.critical("Missing required environment variables: %s", ", ".join(missing))
        logging.critical("Copy .env.example to .env and fill in all values.")
        sys.exit(1)
    for d in (LOGS_DIR, PROPOSALS_DIR, DASHBOARD_DIR):
        d.mkdir(exist_ok=True)
    logging.info("Configuration v2.0 validated OK — %s", COMPANY_NAME)
