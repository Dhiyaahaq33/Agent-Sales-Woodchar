"""Entry point — initialise DB, validate config, start scheduler."""
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


def _handle_sigint(sig, frame):
    logger.info("Shutdown signal received — stopping scheduler...")
    sys.exit(0)


def main():
    logger.info("PT Ekspor Arang AI Agent System starting...")

    # 1. Validate environment
    config.validate_config()

    # 2. Initialise database
    db.init_db()

    # 3. Register SIGINT handler for clean shutdown
    signal.signal(signal.SIGINT, _handle_sigint)

    # 4. Build and start scheduler
    scheduler = build_scheduler()

    logger.info("All agents registered. Scheduler running. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped. Goodbye.")


if __name__ == "__main__":
    main()
