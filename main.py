"""Entry point v2 — init DB, validate config, start Telegram listener, start scheduler.

PT Cahaya Woodchar International — AI Sales Agent System v2.0
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
    send_message("⛔ *AI Agent System STOPPED* — restart with `python main.py`")
    sys.exit(0)


def main():
    logger.info("=" * 60)
    logger.info("PT Cahaya Woodchar International — AI Sales Agent v2.0")
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
        logger.warning("Database v2 schema not found — running migration...")
        from db.migrate_v2 import run_migration
        run_migration()

    # 4. Register SIGINT handler
    signal.signal(signal.SIGINT, _handle_sigint)

    # 5. Start Telegram command listener (background thread)
    start_command_listener()

    # 6. Send startup notification
    send_message(
        f"🚀 *PT Cahaya Woodchar AI Agent v2.0 STARTED*\n"
        f"Scheduler aktif — {len(build_scheduler().get_jobs())} jobs registered.\n"
        f"Ketik /stats untuk cek status."
    )

    # 7. Build and start blocking scheduler
    scheduler = build_scheduler()
    logger.info("All agents registered. Scheduler running. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped. Goodbye.")


if __name__ == "__main__":
    main()
