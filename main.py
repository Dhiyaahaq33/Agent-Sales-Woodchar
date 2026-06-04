"""Entry point v2.1 — init DB, validate config, start Telegram listener, start scheduler.

PT Cahaya Woodchar International — AI Sales Agent System v2.1

Usage:
  python main.py           # Start full agent system
  python main.py --check   # Onboarding checklist (verify all config is set)
"""
import logging
import signal
import sys
from pathlib import Path

# ── Logging setup (must happen before any other import) ──────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import config
import db.database as db
from scheduler import build_scheduler
from tools.telegram_tool import send_message, start_command_listener


def _handle_sigint(sig, frame):
    logger.info("Shutdown signal received — stopping...")
    try:
        send_message("⛔ *AI Agent System STOPPED* — restart with `python main.py`")
    except Exception:
        pass
    sys.exit(0)


def _run_onboarding_check():
    """
    --check mode: print COO onboarding checklist showing what is configured
    and what still needs to be filled in.
    """
    print("\n" + "=" * 60)
    print("PT Cahaya Woodchar AI Agent v2.1 — ONBOARDING CHECKLIST")
    print("=" * 60)

    checks = [
        ("GEMINI_API_KEY",      config.GEMINI_API_KEY,      "Gemini AI (primary LLM)"),
        ("GROQ_API_KEY",        config.GROQ_API_KEY,        "Groq (fallback LLM)"),
        ("GMAIL_ADDRESS",       config.GMAIL_ADDRESS,       "Gmail sender address"),
        ("GMAIL_APP_PASSWORD",  config.GMAIL_APP_PASSWORD,  "Gmail app password"),
        ("TELEGRAM_BOT_TOKEN",  config.TELEGRAM_BOT_TOKEN,  "Telegram bot token"),
        ("TELEGRAM_CHAT_ID",    config.TELEGRAM_CHAT_ID,    "Telegram chat ID"),
        ("COMPANY_NAME",        config.COMPANY_NAME,        "Company name"),
        ("COO_NAME",            config.COO_NAME,            "COO name"),
        ("COMPANY_WEBSITE",     config.COMPANY_WEBSITE,     "Company website"),
        ("WA_NUMBER_1",         config.WA_NUMBER_1,         "WhatsApp number 1"),
        ("EXCHANGERATE_API_KEY",config.EXCHANGERATE_API_KEY,"Exchange rate API (optional)"),
        ("YOUTUBE_API_KEY",     config.YOUTUBE_API_KEY,     "YouTube API (optional, social)"),
        ("BITLY_ACCESS_TOKEN",  config.BITLY_ACCESS_TOKEN,  "Bit.ly tracking (optional)"),
    ]

    all_ok = True
    for key, value, desc in checks:
        filled = bool(value) and value not in ("FILL_BY_COO", "")
        icon = "✅" if filled else "❌"
        if not filled and key in ("GEMINI_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD",
                                   "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            all_ok = False
        print(f"  {icon}  {key:30s}  {desc}")

    print()

    # Data files
    data_checks = [
        ("data/product_catalog.json",   "Product catalog — prices filled?"),
        ("data/competitors.json",        "Competitors — names + URLs filled?"),
        ("credentials.json",             "Gmail OAuth credentials.json"),
    ]
    for fname, desc in data_checks:
        path = Path(fname)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if "FILL_BY_COO" in content:
                print(f"  ⚠️  {fname:35s}  {desc} ← FILL_BY_COO still present!")
            else:
                print(f"  ✅  {fname:35s}  {desc}")
        else:
            print(f"  ❌  {fname:35s}  {desc} ← FILE MISSING")

    print()
    if all_ok:
        print("✅ All required config set. Ready to run: python main.py")
    else:
        print("❌ Some required config is missing. Edit .env and try again.")
    print("=" * 60 + "\n")


def main():
    # Handle --check flag
    if "--check" in sys.argv:
        _run_onboarding_check()
        return

    logger.info("=" * 60)
    logger.info("PT Cahaya Woodchar International — AI Sales Agent v3.0")
    logger.info("=" * 60)

    # 1. Validate environment variables
    config.validate_config()

    # 2. Initialise database (creates tables if not exist)
    db.init_db()

    # 3. Check if migration v2 has been run
    from db.database import get_conn
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM buyer_memory LIMIT 1")
    except Exception:
        logger.warning("Database v2 schema not found — running migration v2...")
        from db.migrate_v2 import run_migration
        run_migration()

    # 4. Check if migration v2.1 has been run
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM sample_log LIMIT 1")
    except Exception:
        logger.warning("Database v2.1 schema not found — running migration v2.1...")
        from db.migrate_v21 import run_migration as run_migration_v21
        run_migration_v21()

    # 5. Check if migration v3.0 has been run
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM revenue_forecast LIMIT 1")
    except Exception:
        logger.warning("Database v3.0 schema not found — running migration v3.0...")
        from db.migrate_v3 import run_migration as run_migration_v3
        run_migration_v3()

    # 5b. Bulk-index existing leads into VectorMemory if enabled
    if config.VECTOR_MEMORY_ENABLED:
        try:
            from tools.vector_memory import get_vector_memory
            vm = get_vector_memory()
            if vm.get_stats().get("buyer_profiles", 0) == 0:
                logger.info("VectorMemory empty — bulk indexing existing leads...")
                vm.bulk_index_all_leads()
        except Exception as e:
            logger.warning("VectorMemory bulk index failed (non-fatal): %s", e)

    # 5. Start health check server (background Flask, port 8080)
    try:
        from tools.health_check import start_health_server
        start_health_server()
    except Exception as e:
        logger.warning(f"Health check server failed to start: {e}")

    # 6. Register SIGINT handler
    signal.signal(signal.SIGINT, _handle_sigint)

    # 7. Start Telegram command listener (background thread)
    start_command_listener()

    # 8. Log warmup status if enabled
    if config.WARMUP_MODE:
        from tools.warmup_manager import get_warmup_manager
        wm = get_warmup_manager()
        status = wm.get_status()
        logger.info(f"Warmup mode: {status['phase']} — limit {status['daily_limit']}/day")

    # 9. Send startup notification
    job_count = len(build_scheduler().get_jobs())
    send_message(
        f"🚀 *PT Cahaya Woodchar AI Agent v3.0 STARTED*\n"
        f"Scheduler aktif — {job_count} jobs registered.\n"
        f"Intelligence: VectorMemory ✅ SentimentAI ✅ ChurnDetector ✅\n"
        f"Ketik /stats untuk cek status."
    )

    # 10. Build and start blocking scheduler
    scheduler = build_scheduler()
    logger.info("All agents registered. Scheduler running. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped. Goodbye.")


if __name__ == "__main__":
    main()
