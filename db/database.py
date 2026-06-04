"""SQLite schema, CRUD helpers, and reporting queries."""
import sqlite3
import shutil
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name     TEXT NOT NULL,
    contact_name     TEXT,
    email            TEXT UNIQUE,
    website          TEXT,
    country          TEXT,
    industry         TEXT,
    estimated_volume TEXT,
    score            INTEGER DEFAULT 0,
    source           TEXT,
    status           TEXT DEFAULT 'NEW',
    classification   TEXT,
    notes            TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outreach (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL,
    touch_number    INTEGER NOT NULL,
    subject         TEXT,
    body            TEXT,
    sent_at         DATETIME,
    gmail_thread_id TEXT,
    opened          INTEGER DEFAULT 0,
    replied         INTEGER DEFAULT 0,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL,
    pdf_path        TEXT,
    total_value_usd REAL,
    product_specs   TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_at         DATETIME,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

CREATE TRIGGER IF NOT EXISTS trg_leads_updated
AFTER UPDATE ON leads
BEGIN
    UPDATE leads SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""


@contextmanager
def get_conn():
    """Yield a committed-on-success, rolled-back-on-error connection."""
    conn = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create schema if it does not exist."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Database ready: %s", config.DB_PATH)


def backup_db() -> None:
    """Copy crm.db to a timestamped backup file."""
    src = Path(config.DB_PATH)
    if not src.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = src.parent / f"crm_backup_{ts}.db"
    shutil.copy2(src, dst)
    logger.info("Database backed up → %s", dst)


# ── Leads ──────────────────────────────────────────────────────────────────

def insert_lead(company_name: str = None, email: str = None, website: str = None,
                country: str = None, contact_name: str = None,
                industry: str = None, source: str = None,
                # v2.1+ aliases and extra fields
                company: str = None,
                notes: str = None,
                score: int = 0,
                extra_fields: dict = None) -> int | None:
    """
    Insert lead; returns new id or None on duplicate email.
    Accepts both `company_name` (legacy) and `company` (v2.1+).
    extra_fields: dict of v2.1 social columns to set if columns exist.
    """
    # Unify company name
    canonical_name = company or company_name or ""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO leads
                   (company_name, company, contact_name, email, website,
                    country, industry, source, notes, score)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (canonical_name, canonical_name, contact_name, email,
                 website, country, industry, source, notes or "", score),
            )
            lead_id = cur.lastrowid

            # Set extra v2.1 social fields if columns exist
            if extra_fields and lead_id:
                for col, val in extra_fields.items():
                    try:
                        conn.execute(
                            f"UPDATE leads SET {col} = ? WHERE id = ?",
                            (val, lead_id)
                        )
                    except Exception:
                        pass  # Column may not exist yet

            return lead_id
    except sqlite3.IntegrityError:
        logger.debug("Duplicate skipped: %s / %s", email, canonical_name)
        return None


def get_leads_by_status(status: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE status=?", (status,)).fetchall()
        return [dict(r) for r in rows]


def update_lead(lead_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    with get_conn() as conn:
        conn.execute(f"UPDATE leads SET {cols} WHERE id=?", [*kwargs.values(), lead_id])


def get_lead_by_id(lead_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row else None


def get_lead_by_email(email: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def get_leads_for_followup() -> list[dict]:
    """Leads that were contacted but have not replied and are below MAX_TOUCHES."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT l.*, o.touch_number, o.sent_at AS last_sent, o.gmail_thread_id
            FROM leads l
            JOIN outreach o ON l.id = o.lead_id
            WHERE l.status = 'OUTREACH'
              AND (l.classification IS NULL OR l.classification = 'OOO')
              AND o.touch_number = (SELECT MAX(touch_number) FROM outreach WHERE lead_id = l.id)
              AND o.touch_number < ?
              AND o.replied = 0
            ORDER BY o.sent_at
            """,
            (config.MAX_TOUCHES,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_thread_ids() -> set:
    """All gmail_thread_ids we sent, used by Monitor Agent."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT gmail_thread_id FROM outreach WHERE gmail_thread_id IS NOT NULL"
        ).fetchall()
        return {r["gmail_thread_id"] for r in rows}


# ── Outreach ───────────────────────────────────────────────────────────────

def log_outreach(lead_id: int, touch_number: int, subject: str,
                 body: str, gmail_thread_id: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO outreach (lead_id, touch_number, subject, body, sent_at, gmail_thread_id)
               VALUES (?,?,?,?,?,?)""",
            (lead_id, touch_number, subject, body, datetime.now().isoformat(), gmail_thread_id),
        )
        return cur.lastrowid


def mark_replied(lead_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE outreach SET replied=1
               WHERE lead_id=? AND id=(SELECT MAX(id) FROM outreach WHERE lead_id=?)""",
            (lead_id, lead_id),
        )


def get_last_outreach(lead_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM outreach WHERE lead_id=? ORDER BY id DESC LIMIT 1", (lead_id,)
        ).fetchone()
        return dict(row) if row else None


def count_sent_today() -> int:
    today = datetime.now().date().isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at)=?", (today,)
        ).fetchone()[0]


# ── Proposals ──────────────────────────────────────────────────────────────

def log_proposal(lead_id: int, pdf_path: str,
                 total_value_usd: float = None, product_specs: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO proposals (lead_id, pdf_path, total_value_usd, product_specs)
               VALUES (?,?,?,?)""",
            (lead_id, pdf_path, total_value_usd, product_specs),
        )
        return cur.lastrowid


def mark_proposal_sent(proposal_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET sent_at=? WHERE id=?",
            (datetime.now().isoformat(), proposal_id),
        )


# ── Reporting ──────────────────────────────────────────────────────────────

def get_daily_stats() -> dict:
    today = datetime.now().date().isoformat()
    with get_conn() as conn:
        stats: dict = {}
        stats["leads_scraped_today"] = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE DATE(created_at)=?", (today,)
        ).fetchone()[0]
        stats["emails_sent_today"] = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at)=?", (today,)
        ).fetchone()[0]
        stats["replies_today"] = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at)=? AND replied=1", (today,)
        ).fetchone()[0]
        stats["interested_today"] = [
            dict(r) for r in conn.execute(
                """SELECT COALESCE(company, company_name, '') as company, country
                   FROM leads
                   WHERE classification='INTERESTED' AND DATE(updated_at)=?""",
                (today,),
            ).fetchall()
        ]
        stats["proposals_sent_today"] = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE DATE(sent_at)=?", (today,)
        ).fetchone()[0]
        pipeline: dict = {}
        for row in conn.execute("SELECT status, COUNT(*) AS cnt FROM leads GROUP BY status"):
            pipeline[row["status"]] = row["cnt"]
        stats["pipeline"] = pipeline
        cls_breakdown: dict = {}
        for row in conn.execute(
            """SELECT classification, COUNT(*) AS cnt FROM leads
               WHERE classification IS NOT NULL GROUP BY classification"""
        ):
            cls_breakdown[row["classification"]] = row["cnt"]
        stats["classification_breakdown"] = cls_breakdown
        return stats


# ── Revenue / v3.0 helpers ─────────────────────────────────────────────

def get_pipeline_value() -> dict:
    """Return total pipeline value and weighted forecast in USD."""
    from agents.reporter_agent import STAGE_WEIGHTS, AVG_DEAL_VALUE
    pipeline_value = 0.0
    weighted_value = 0.0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status"
            ).fetchall()
        for row in rows:
            stage = row["status"]
            count = row["cnt"]
            if stage == "DEAD":
                continue
            avg = AVG_DEAL_VALUE.get(stage, 5_000)
            weight = STAGE_WEIGHTS.get(stage, 0.05)
            pipeline_value += count * avg
            weighted_value += count * avg * weight
        return {
            "pipeline_value_usd": round(pipeline_value),
            "weighted_forecast_usd": round(weighted_value),
        }
    except Exception:
        return {"pipeline_value_usd": 0, "weighted_forecast_usd": 0}


def get_lead_by_company(company_name: str) -> dict | None:
    """Fuzzy match lead by company name."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM leads
               WHERE company LIKE ? OR company_name LIKE ?
               ORDER BY score DESC LIMIT 1""",
            (f"%{company_name}%", f"%{company_name}%")
        ).fetchone()
    return dict(row) if row else None
