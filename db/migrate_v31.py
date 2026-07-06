"""Database migration v3.0 → v3.1

Fixes:
  BUG-01: Create negotiation_log table (NegotiationAgent._save_negotiation_draft breaks without it)
  BUG-02: Create market_intel table (MarketAgent._save_to_db + PricingAgent._get_usd_idr_rate)
  BUG-03: Create system_config table (telegram_tool persistent pause state)
  BUG-04: Create failed_sends table (email retry queue)
  NEW-01: Add conv_stage column to leads (conversation stage tracking)
  NEW-02: Add bant_json column to leads (BANT qualification data)

Run:
  python -m db.migrate_v31
"""
import logging
import sqlite3
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def run():
    db_path = config.DB_PATH
    if not Path(db_path).exists():
        logger.warning("DB not found at %s — run main.py first to init schema", db_path)
        return

    conn = sqlite3.connect(db_path)
    try:
        # BUG-01: negotiation_log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS negotiation_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id      INTEGER NOT NULL,
                subject      TEXT,
                draft_body   TEXT,
                status       TEXT DEFAULT 'PENDING',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                approved_at  DATETIME,
                rejected_at  DATETIME,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
        """)
        logger.info("negotiation_log OK")

        # BUG-02: market_intel
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_intel (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT,
                usd_idr_rate REAL,
                data_type    TEXT,
                data_value   TEXT,
                source       TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("market_intel OK")

        # BUG-03: system_config
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("system_config OK")

        # BUG-04: failed_sends
        conn.execute("""
            CREATE TABLE IF NOT EXISTS failed_sends (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id       INTEGER,
                subject       TEXT,
                body          TEXT,
                error         TEXT,
                retry_count   INTEGER DEFAULT 0,
                next_retry_at DATETIME,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("failed_sends OK")

        # NEW-01: conv_stage column
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN conv_stage TEXT DEFAULT 'COLD'")
            logger.info("leads.conv_stage added")
        except sqlite3.OperationalError:
            logger.debug("leads.conv_stage already exists")

        # NEW-02: bant_json column
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN bant_json TEXT")
            logger.info("leads.bant_json added")
        except sqlite3.OperationalError:
            logger.debug("leads.bant_json already exists")

        conn.commit()
        logger.info("Migration v3.1 complete")
    except Exception as exc:
        conn.rollback()
        logger.error("Migration v3.1 FAILED: %s", exc)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
