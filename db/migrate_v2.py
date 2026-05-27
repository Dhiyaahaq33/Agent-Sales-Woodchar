"""Database migration script v1.0 → v2.0.

Menambah tabel baru dan kolom baru tanpa menghapus data existing.
Jalankan SEKALI sebelum menjalankan agen v2:
    python db/migrate_v2.py
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
-- Buyer memory untuk long-term vector storage (Phase 4)
CREATE TABLE IF NOT EXISTS buyer_memory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL,
    memory_type  TEXT NOT NULL,   -- email_sent / reply_received / objection / interest
    summary      TEXT NOT NULL,   -- 1-2 sentence summary
    full_context TEXT,            -- Full email/reply text
    embedding    BLOB,            -- ChromaDB embedding (stored externally, this is metadata)
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- A/B test tracking (Phase 2)
CREATE TABLE IF NOT EXISTS ab_tests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    test_name       TEXT NOT NULL,
    variant_a_desc  TEXT,
    variant_b_desc  TEXT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at        DATETIME,
    winner          TEXT,          -- A / B / inconclusive
    confidence_pct  REAL,
    variant_a_sent  INTEGER DEFAULT 0,
    variant_a_replies INTEGER DEFAULT 0,
    variant_b_sent  INTEGER DEFAULT 0,
    variant_b_replies INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'ACTIVE'  -- ACTIVE / COMPLETED
);

-- Market intelligence daily snapshots (Phase 4)
CREATE TABLE IF NOT EXISTS market_intel (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE NOT NULL,
    usd_idr_rate    REAL,
    charcoal_demand_signal TEXT,  -- JSON: {country: signal_pct}
    bbq_season_active TEXT,       -- JSON: [country, ...]
    trade_shows     TEXT,         -- JSON: [{name, location, date, days_until}]
    competitor_prices TEXT,       -- JSON snapshot
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seasonal & event triggers (Phase 2)
CREATE TABLE IF NOT EXISTS triggers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,   -- seasonal / trade_show / custom
    active_from  DATE,
    active_until DATE,
    target_countries TEXT,        -- JSON list
    is_active    INTEGER DEFAULT 0,
    notes        TEXT,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Blacklist — email/domains yang tidak boleh dikontak
CREATE TABLE IF NOT EXISTS blacklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT,
    domain     TEXT,
    reason     TEXT,              -- UNSUBSCRIBE / BOUNCE / MANUAL
    added_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_blacklist_email  ON blacklist(email)  WHERE email  IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_blacklist_domain ON blacklist(domain) WHERE domain IS NOT NULL;

-- Negotiation log dengan approval gate (Phase 4)
CREATE TABLE IF NOT EXISTS negotiation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL,
    subject      TEXT,
    draft_body   TEXT,
    status       TEXT DEFAULT 'PENDING', -- PENDING / APPROVED / REJECTED
    approved_at  DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- Deliverability tracking (Phase 4)
CREATE TABLE IF NOT EXISTS deliverability_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          DATE NOT NULL,
    emails_sent   INTEGER DEFAULT 0,
    bounces       INTEGER DEFAULT 0,
    unsubscribes  INTEGER DEFAULT 0,
    bounce_rate   REAL DEFAULT 0.0,
    paused        INTEGER DEFAULT 0,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_NEW_COLUMNS = [
    # table, column, definition
    ("leads",    "ab_variant",       "TEXT"),
    ("leads",    "trigger_source",   "TEXT"),
    ("leads",    "last_news",        "TEXT"),
    ("leads",    "preferred_product","TEXT"),
    ("leads",    "price_sensitivity","TEXT"),
    ("outreach", "send_local_time",  "DATETIME"),
    ("outreach", "channel",          "TEXT DEFAULT 'email'"),
    ("outreach", "open_tracked",     "INTEGER DEFAULT 0"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def run_migration():
    logger.info("Starting database migration v1.0 → v2.0...")
    logger.info("Database: %s", config.DB_PATH)

    with get_conn() as conn:
        # Create new tables
        logger.info("Creating new tables...")
        conn.executescript(_NEW_TABLES)
        logger.info("New tables created OK.")

        # Add new columns to existing tables
        logger.info("Adding new columns...")
        for table, column, definition in _NEW_COLUMNS:
            if _column_exists(conn, table, column):
                logger.info("  [SKIP] %s.%s already exists", table, column)
            else:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                logger.info("  [OK]   %s.%s added", table, column)

    logger.info("Migration v2.0 COMPLETE. Database is ready for v2 agents.")


if __name__ == "__main__":
    run_migration()
