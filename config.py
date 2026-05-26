"""Configuration — loads all .env variables with startup validation."""
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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", "credentials.json")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

COMPANY_NAME = os.getenv("COMPANY_NAME", "PT Ekspor Arang")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "")
SENDER_NAME = os.getenv("SENDER_NAME", "")

DB_PATH = os.getenv("DB_PATH", str(DB_DIR / "crm.db"))

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

MAX_EMAILS_PER_DAY = 200
EMAIL_DELAY_MIN = 30
EMAIL_DELAY_MAX = 90
SCRAPER_DELAY_MIN = 2
SCRAPER_DELAY_MAX = 5
LEAD_SCORE_THRESHOLD = 40
MAX_TOUCHES = 4
FOLLOWUP_DELAYS_DAYS = [3, 5, 7]  # delay after previous touch per touch number

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
    for d in (LOGS_DIR, PROPOSALS_DIR):
        d.mkdir(exist_ok=True)
    logging.info("Configuration validated OK.")
