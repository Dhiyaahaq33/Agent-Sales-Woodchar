"""Configuration v2.1 — loads all .env variables with startup validation.

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
GMAIL_USER             = os.getenv("GMAIL_USER", GMAIL_ADDRESS)   # alias
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", "credentials.json")
GMAIL_ACCOUNTS         = os.getenv("GMAIL_ACCOUNTS", "")           # v2.1: multi-account rotation

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Company ───────────────────────────────────────────────────────────────────
COMPANY_NAME     = os.getenv("COMPANY_NAME", "PT Cahaya Woodchar International")
COMPANY_EMAIL    = os.getenv("COMPANY_EMAIL", "johanneshaq@indonesiancharcoalsupplier.com")
COMPANY_WEBSITE  = os.getenv("COMPANY_WEBSITE", "https://indonesiancharcoalsupplier.com")
SENDER_NAME      = os.getenv("SENDER_NAME", "Muhammad Yahya")
COO_NAME         = os.getenv("COO_NAME", "Muhammad Yahya")
COO_EMAIL        = os.getenv("COO_EMAIL", COMPANY_EMAIL)
COMPANY_LOCATION = os.getenv("COMPANY_LOCATION", "Lampung Utara, Lampung, Indonesia")
WA_NUMBER_1      = os.getenv("WA_NUMBER_1", "+62 895 0481 5988")
WA_NUMBER_2      = os.getenv("WA_NUMBER_2", "+62 812 2564 6585")
WA_NUMBER_3      = os.getenv("WA_NUMBER_3", "+62 812 2464 6585")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", str(DB_DIR / "crm.db"))

# ── External APIs ─────────────────────────────────────────────────────────────
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "")
YOUTUBE_API_KEY      = os.getenv("YOUTUBE_API_KEY", "")        # v2.1: YouTube Data API
BITLY_ACCESS_TOKEN   = os.getenv("BITLY_ACCESS_TOKEN", "")     # v2.1: link tracking
TIKTOK_MS_TOKEN      = os.getenv("TIKTOK_MS_TOKEN", "")        # v2.1: TikTok session token

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

# ── Feature Flags (v2.1) ──────────────────────────────────────────────────────
WARMUP_MODE              = os.getenv("WARMUP_MODE", "false").lower() == "true"
WARMUP_START_DATE        = os.getenv("WARMUP_START_DATE", "")
SOCIAL_SCRAPING_ENABLED  = os.getenv("SOCIAL_SCRAPING_ENABLED", "false").lower() == "true"
SOCIAL_YOUTUBE_ENABLED   = os.getenv("SOCIAL_YOUTUBE_ENABLED", "true").lower() == "true"
SOCIAL_INSTAGRAM_ENABLED = os.getenv("SOCIAL_INSTAGRAM_ENABLED", "false").lower() == "true"
SOCIAL_FACEBOOK_ENABLED  = os.getenv("SOCIAL_FACEBOOK_ENABLED", "false").lower() == "true"
SOCIAL_TIKTOK_ENABLED    = os.getenv("SOCIAL_TIKTOK_ENABLED", "false").lower() == "true"
SOCIAL_TWITTER_ENABLED   = os.getenv("SOCIAL_TWITTER_ENABLED", "false").lower() == "true"
SOCIAL_LINKEDIN_ENABLED  = os.getenv("SOCIAL_LINKEDIN_ENABLED", "false").lower() == "true"
OFAC_CHECK_ENABLED       = os.getenv("OFAC_CHECK_ENABLED", "true").lower() == "true"
DRIVE_BACKUP_ENABLED     = os.getenv("DRIVE_BACKUP_ENABLED", "false").lower() == "true"
SAMPLE_FOLLOWUP_ENABLED  = os.getenv("SAMPLE_FOLLOWUP_ENABLED", "true").lower() == "true"
REORDER_SEQUENCE_ENABLED = os.getenv("REORDER_SEQUENCE_ENABLED", "true").lower() == "true"
HEALTH_CHECK_PORT        = int(os.getenv("HEALTH_CHECK_PORT", "8080"))

# ── Social Platform Credentials ───────────────────────────────────────────────
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
LINKEDIN_EMAIL     = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD  = os.getenv("LINKEDIN_PASSWORD", "")
FACEBOOK_PROXY     = os.getenv("FACEBOOK_PROXY", "")

# ── Required vars for startup validation ──────────────────────────────────────
_REQUIRED = [
    "GEMINI_API_KEY",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


# ── v3.0: Advanced Intelligence ───────────────────────────────────────────────
VECTOR_MEMORY_ENABLED    = os.getenv("VECTOR_MEMORY_ENABLED", "true").lower() == "true"
EMAIL_TRACKING_ENABLED   = os.getenv("EMAIL_TRACKING_ENABLED", "false").lower() == "true"
TRACKING_SERVER_URL      = os.getenv("TRACKING_SERVER_URL", "")   # e.g. https://yourserver.ngrok.io
CHURN_DETECTION_ENABLED  = os.getenv("CHURN_DETECTION_ENABLED", "true").lower() == "true"
COMPETITOR_MONITOR_ENABLED = os.getenv("COMPETITOR_MONITOR_ENABLED", "true").lower() == "true"
PRICING_ENGINE_ENABLED   = os.getenv("PRICING_ENGINE_ENABLED", "true").lower() == "true"
SEND_TIME_OPTIMIZE       = os.getenv("SEND_TIME_OPTIMIZE", "true").lower() == "true"
REVENUE_FORECAST_ENABLED = os.getenv("REVENUE_FORECAST_ENABLED", "true").lower() == "true"


def validate_config() -> None:
    """Exit with error if any required env var is missing."""
    missing = [v for v in _REQUIRED if not globals().get(v)]
    if missing:
        logging.critical("Missing required environment variables: %s", ", ".join(missing))
        logging.critical("Copy .env.example to .env and fill in all values.")
        sys.exit(1)
    for d in (LOGS_DIR, PROPOSALS_DIR, DASHBOARD_DIR):
        d.mkdir(exist_ok=True)
    logging.info("Configuration v3.0 validated OK — %s", COMPANY_NAME)
