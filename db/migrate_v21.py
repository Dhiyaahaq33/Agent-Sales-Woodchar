"""Database migration v2.1 — tambahan kolom dan tabel dari PRD v2.1 Addendum.

Jalankan SEKALI setelah migrate_v2.py:
    python db/migrate_v21.py
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import config
from db.database import get_conn

_NEW_TABLES = """
-- Sample tracking lifecycle
CREATE TABLE IF NOT EXISTS sample_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id             INTEGER NOT NULL,
    product             TEXT,
    weight_kg           REAL DEFAULT 2.0,
    shipped_via         TEXT,
    sent_date           DATE,
    received_confirmed  INTEGER DEFAULT 0,
    feedback            TEXT,
    status              TEXT DEFAULT 'REQUESTED',  -- REQUESTED/SENT/RECEIVED/FEEDBACK_GIVEN
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- Post-deal reorder sequences
CREATE TABLE IF NOT EXISTS reorder_sequence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL,
    touch_number    INTEGER NOT NULL,  -- 1=30d, 2=60d, 3=90d
    scheduled_date  DATE,
    sent_date       DATETIME,
    reply_received  INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- Health check log
CREATE TABLE IF NOT EXISTS health_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'ok',
    jobs_active     INTEGER,
    leads_today     INTEGER,
    emails_today    INTEGER,
    errors_count    INTEGER DEFAULT 0,
    bounce_rate     REAL DEFAULT 0.0,
    notes           TEXT
);

-- Competitor prices (structured)
CREATE TABLE IF NOT EXISTS competitor_prices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_name     TEXT NOT NULL,
    product             TEXT,
    price_usd_per_mt    REAL,
    moq_mt              REAL,
    scraped_date        DATE,
    source_url          TEXT,
    notes               TEXT
);

-- Country risk database
CREATE TABLE IF NOT EXISTS country_risk_db (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    country_code    TEXT NOT NULL,
    country_name    TEXT NOT NULL,
    risk_level      TEXT NOT NULL,   -- LOW / MEDIUM / HIGH / BLOCKED
    last_updated    DATE,
    source          TEXT,
    notes           TEXT,
    UNIQUE(country_code)
);

-- Multi-Gmail account registry
CREATE TABLE IF NOT EXISTS gmail_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT NOT NULL UNIQUE,
    app_password        TEXT,
    credentials_path    TEXT,
    status              TEXT DEFAULT 'ACTIVE',  -- ACTIVE / DISABLED / RATE_LIMITED
    bounce_rate         REAL DEFAULT 0.0,
    emails_today        INTEGER DEFAULT 0,
    last_used           DATETIME,
    added_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_NEW_COLUMNS = [
    # Social media fields on leads
    ("leads", "social_platform",          "TEXT"),
    ("leads", "social_handle",            "TEXT"),
    ("leads", "social_url",               "TEXT"),
    ("leads", "social_followers",         "INTEGER"),
    ("leads", "social_bio",               "TEXT"),
    ("leads", "discovery_context",        "TEXT"),
    # Sample tracking on leads
    ("leads", "sample_requested",         "DATETIME"),
    ("leads", "sample_sent_date",         "DATETIME"),
    ("leads", "sample_tracking",          "TEXT"),
    ("leads", "sample_status",            "TEXT"),
    ("leads", "sample_feedback",          "TEXT"),
    # Deal lifecycle on leads
    ("leads", "deal_closed_date",         "DATETIME"),
    ("leads", "deal_value_usd",           "REAL"),
    ("leads", "deal_product",             "TEXT"),
    ("leads", "reorder_sequence_status",  "TEXT"),
    # Risk & estimation on leads
    ("leads", "country_risk",             "TEXT"),
    ("leads", "estimated_deal_value_usd", "REAL"),
    # Outreach reply matching
    ("outreach", "match_confidence",      "TEXT"),
    # Proposal versioning
    ("proposals", "version_number",       "INTEGER DEFAULT 1"),
    ("proposals", "previous_version_id",  "INTEGER"),
    ("proposals", "reason_for_revision",  "TEXT"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def run_migration():
    logger.info("Starting database migration v2.1...")
    logger.info("Database: %s", config.DB_PATH)

    with get_conn() as conn:
        logger.info("Creating new tables...")
        conn.executescript(_NEW_TABLES)
        logger.info("New tables created OK.")

        logger.info("Adding new columns...")
        for table, column, definition in _NEW_COLUMNS:
            if not _table_exists(conn, table):
                logger.warning("  [SKIP] Table '%s' does not exist — run migrate_v2.py first", table)
                continue
            if _column_exists(conn, table, column):
                logger.info("  [SKIP] %s.%s already exists", table, column)
            else:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                logger.info("  [OK]   %s.%s added", table, column)

    logger.info("Migration v2.1 COMPLETE.")


if __name__ == "__main__":
    run_migration()
