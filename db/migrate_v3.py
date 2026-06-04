"""
db/migrate_v3.py — Database Migration v3.0 Advanced Intelligence
PT Cahaya Woodchar International

New tables:
  - email_opens         — pixel tracking: open events per outreach
  - competitor_history  — price change log for competitor monitoring
  - churn_scores        — daily churn risk score per lead
  - pricing_cache       — cached FOB price calculations
  - revenue_forecast    — monthly revenue projection snapshots
  - lead_enrichment     — deep enrichment data (website analysis, social signals)

New columns:
  - leads.company            — canonical company name (mirrors company_name)
  - leads.industry_code      — SIC/ISIC industry code
  - leads.employee_count     — estimated employees from enrichment
  - leads.annual_revenue_est — estimated annual revenue
  - leads.import_signals     — JSON: keywords found indicating import activity
  - leads.send_window_start  — optimal send hour (local time)
  - leads.send_window_end    — optimal send window end
  - leads.last_open_at       — last email open timestamp
  - leads.open_count         — total opens
  - leads.churn_risk         — 0.0-1.0 churn probability
  - leads.lifetime_value_est — estimated LTV in USD
  - outreach.tracking_url    — Bit.ly tracking URL
  - outreach.pixel_id        — unique tracking pixel ID
  - outreach.open_count      — opens recorded for this outreach
  - outreach.click_count     — link clicks recorded

New indexes:
  - leads status + score     — fast enriched lead queries
  - leads country + status   — country filter for risk checks
  - outreach lead + replied  — follow-up queries

Run: python db/migrate_v3.py
"""

import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


NEW_TABLES = """
-- Email open tracking (pixel-based)
CREATE TABLE IF NOT EXISTS email_opens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    outreach_id INTEGER NOT NULL,
    lead_id     INTEGER NOT NULL,
    pixel_id    TEXT UNIQUE NOT NULL,
    opened_at   DATETIME,
    ip_address  TEXT,
    user_agent  TEXT,
    open_count  INTEGER DEFAULT 0,
    FOREIGN KEY (outreach_id) REFERENCES outreach(id),
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- Competitor price history (Alibaba monitoring)
CREATE TABLE IF NOT EXISTS competitor_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id   TEXT NOT NULL,     -- COMP-001..005 from competitors.json
    competitor_name TEXT,
    product_type    TEXT,
    price_usd_per_mt REAL,
    moq_mt          REAL,
    currency        TEXT DEFAULT 'USD',
    alibaba_url     TEXT,
    scraped_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    change_pct      REAL,              -- % change from previous observation
    notes           TEXT
);

-- Churn risk scores (run daily)
CREATE TABLE IF NOT EXISTS churn_scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id       INTEGER NOT NULL,
    score_date    DATE NOT NULL,
    churn_risk    REAL NOT NULL,       -- 0.0=safe, 1.0=will churn
    days_silent   INTEGER,             -- days since last interaction
    touch_count   INTEGER,
    open_rate     REAL,
    factors       TEXT,                -- JSON: which factors drove score
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    UNIQUE(lead_id, score_date)
);

-- FOB price calculation cache
CREATE TABLE IF NOT EXISTS pricing_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      TEXT NOT NULL,
    quantity_mt     REAL NOT NULL,
    destination     TEXT NOT NULL,
    payment_terms   TEXT DEFAULT 'TT',
    price_usd_per_mt REAL NOT NULL,
    total_usd       REAL NOT NULL,
    freight_est_usd REAL,
    margin_pct      REAL,
    calculated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    valid_until     DATE
);

-- Revenue forecast snapshots
CREATE TABLE IF NOT EXISTS revenue_forecast (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date   DATE NOT NULL,
    month           TEXT NOT NULL,     -- YYYY-MM
    pipeline_value  REAL,              -- total estimated value in pipeline
    weighted_value  REAL,              -- probability-weighted value
    closed_mtd      REAL,             -- closed deals this month
    forecast_method TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Deep lead enrichment data
CREATE TABLE IF NOT EXISTS lead_enrichment (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id          INTEGER NOT NULL UNIQUE,
    website_title    TEXT,
    website_desc     TEXT,
    website_keywords TEXT,             -- JSON array
    company_size_est TEXT,             -- micro/small/medium/large
    employee_count   INTEGER,
    annual_rev_est   TEXT,             -- e.g. "$1M-$5M"
    import_signals   TEXT,             -- JSON: found import keywords + context
    product_mentions TEXT,             -- JSON: charcoal products found on site
    contact_title    TEXT,             -- CEO/Purchasing/Import Manager
    linkedin_url     TEXT,
    enriched_by      TEXT DEFAULT 'auto',
    enriched_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
"""

NEW_COLUMNS_LEADS = [
    ("company",            "TEXT",    ""),
    ("industry_code",      "TEXT",    ""),
    ("send_window_start",  "INTEGER", "9"),     # hour 0-23 local
    ("send_window_end",    "INTEGER", "17"),    # hour 0-23 local
    ("last_open_at",       "DATETIME",""),
    ("open_count",         "INTEGER", "0"),
    ("churn_risk",         "REAL",    "0.0"),
    ("lifetime_value_est", "REAL",    "0.0"),
    ("vector_id",          "TEXT",    ""),      # ChromaDB document ID
]

NEW_COLUMNS_OUTREACH = [
    ("tracking_url",  "TEXT",    ""),
    ("pixel_id",      "TEXT",    ""),
    ("open_count",    "INTEGER", "0"),
    ("click_count",   "INTEGER", "0"),
    ("sender_email",  "TEXT",    ""),           # which Gmail account sent this
]

NEW_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_leads_status_score ON leads(status, score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_leads_country_status ON leads(country, status)",
    "CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email)",
    "CREATE INDEX IF NOT EXISTS idx_leads_churn ON leads(churn_risk DESC, status)",
    "CREATE INDEX IF NOT EXISTS idx_outreach_lead_replied ON outreach(lead_id, replied)",
    "CREATE INDEX IF NOT EXISTS idx_outreach_sent_at ON outreach(sent_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_competitor_scraped ON competitor_history(scraped_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_churn_date ON churn_scores(score_date DESC, churn_risk DESC)",
]


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols


def run_migration():
    import config
    db_path = config.DB_PATH
    logger.info("=" * 60)
    logger.info("Starting DB Migration v3.0 — %s", db_path)
    logger.info("=" * 60)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # 1. New tables
        logger.info("Creating new tables...")
        conn.executescript(NEW_TABLES)
        conn.commit()
        logger.info("  ✅ New tables created")

        # 2. New columns on leads
        logger.info("Adding columns to leads table...")
        for col_name, col_type, default in NEW_COLUMNS_LEADS:
            if not _column_exists(conn, "leads", col_name):
                default_clause = f" DEFAULT {default}" if default != "" else ""
                conn.execute(
                    f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}{default_clause}"
                )
                logger.info("  + leads.%s %s", col_name, col_type)
            else:
                logger.info("  ~ leads.%s already exists", col_name)
        conn.commit()

        # 3. Backfill company from company_name
        logger.info("Backfilling leads.company from company_name...")
        conn.execute(
            "UPDATE leads SET company = company_name WHERE company IS NULL OR company = ''"
        )
        conn.commit()
        logger.info("  ✅ company column backfilled")

        # 4. New columns on outreach
        logger.info("Adding columns to outreach table...")
        for col_name, col_type, default in NEW_COLUMNS_OUTREACH:
            if not _column_exists(conn, "outreach", col_name):
                default_clause = f" DEFAULT {default}" if default != "" else ""
                conn.execute(
                    f"ALTER TABLE outreach ADD COLUMN {col_name} {col_type}{default_clause}"
                )
                logger.info("  + outreach.%s %s", col_name, col_type)
            else:
                logger.info("  ~ outreach.%s already exists", col_name)
        conn.commit()

        # 5. Indexes
        logger.info("Creating indexes...")
        for idx_sql in NEW_INDEXES:
            try:
                conn.execute(idx_sql)
                name = idx_sql.split("INDEX IF NOT EXISTS ")[1].split(" ON ")[0]
                logger.info("  + index: %s", name)
            except Exception as e:
                logger.warning("  index skipped: %s", e)
        conn.commit()

        logger.info("=" * 60)
        logger.info("Migration v3.0 complete!")
        logger.info("=" * 60)

    except Exception as e:
        conn.rollback()
        logger.error("Migration failed: %s", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
