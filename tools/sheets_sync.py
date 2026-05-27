"""Google Sheets CRM Sync v2 — sinkronisasi SQLite ke Google Sheets setiap 30 menit.

Sheets yang dibuat:
  1. Leads Pipeline
  2. Outreach Log
  3. Interested Buyers
  4. Weekly Stats
"""
import json
import logging
from datetime import datetime

import config
from db.database import get_conn, get_daily_stats

logger = logging.getLogger(__name__)

_SPREADSHEET_NAME = "Cahaya Woodchar CRM"

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _GSPREAD_OK = True
except ImportError:
    _GSPREAD_OK = False
    logger.warning("gspread not installed — Google Sheets sync disabled.")


def _get_client():
    """Return authenticated gspread client using existing OAuth credentials."""
    if not _GSPREAD_OK:
        raise RuntimeError("gspread not installed. Run: pip install gspread")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    try:
        import pickle
        from pathlib import Path
        from google.auth.transport.requests import Request
        token_path = Path("token.pickle")
        creds = None
        if token_path.exists():
            with open(token_path, "rb") as f:
                creds = pickle.load(f)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
        return gspread.authorize(creds)
    except Exception as exc:
        raise RuntimeError(f"Google Sheets auth failed: {exc}") from exc


def _get_or_create_spreadsheet(client):
    """Return existing spreadsheet or create new one."""
    try:
        return client.open(_SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        logger.info("Creating new spreadsheet: %s", _SPREADSHEET_NAME)
        ss = client.create(_SPREADSHEET_NAME)
        # Share with the Gmail account
        ss.share(config.GMAIL_ADDRESS, perm_type="user", role="writer")
        return ss


def _ensure_sheet(ss, title: str, headers: list[str]):
    """Get sheet by title or create it with headers."""
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers)
        logger.info("Created sheet: %s", title)
    return ws


def _color_row(ws, row_idx: int, status: str):
    """Apply color coding based on lead status."""
    colors = {
        "DEAD":       {"red": 1.0, "green": 0.8, "blue": 0.8},
        "INTERESTED": {"red": 0.8, "green": 1.0, "blue": 0.8},
        "QUALIFIED":  {"red": 0.6, "green": 1.0, "blue": 0.6},
        "NEEDS_INFO": {"red": 1.0, "green": 1.0, "blue": 0.7},
        "OOO":        {"red": 0.9, "green": 0.9, "blue": 1.0},
    }
    color = colors.get(status)
    if color:
        try:
            ws.format(f"A{row_idx}:N{row_idx}", {
                "backgroundColor": color
            })
        except Exception:
            pass  # Color formatting is best-effort


def sync_leads_pipeline(ss):
    """Sync leads table to 'Leads Pipeline' sheet."""
    headers = [
        "ID", "Company", "Country", "Email", "Status", "Score",
        "Classification", "Industry", "Source", "Notes", "Created", "Updated"
    ]
    ws = _ensure_sheet(ss, "Leads Pipeline", headers)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY updated_at DESC LIMIT 1000"
        ).fetchall()

    # Clear and rewrite (simpler than diff-updating)
    ws.clear()
    ws.append_row(headers)
    data = []
    for r in rows:
        data.append([
            r["id"], r["company_name"], r["country"] or "",
            r["email"] or "", r["status"], r["score"],
            r["classification"] or "", r["industry"] or "",
            r["source"] or "", r["notes"] or "",
            r["created_at"] or "", r["updated_at"] or "",
        ])
    if data:
        ws.append_rows(data)
    logger.info("Synced %d leads to Google Sheets", len(data))


def sync_interested_buyers(ss):
    """Sync INTERESTED and QUALIFIED leads to priority sheet."""
    headers = [
        "Company", "Country", "Email", "Score", "Classification",
        "Preferred Product", "Notes", "Last Contact", "COO Notes"
    ]
    ws = _ensure_sheet(ss, "Interested Buyers", headers)
    ws.clear()
    ws.append_row(headers)

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT l.*, o.sent_at as last_contact
               FROM leads l
               LEFT JOIN outreach o ON l.id=o.lead_id
               WHERE l.classification='INTERESTED' OR l.status='QUALIFIED'
               ORDER BY l.updated_at DESC"""
        ).fetchall()

    data = []
    for r in rows:
        data.append([
            r["company_name"], r["country"] or "", r["email"] or "",
            r["score"], r["classification"] or "",
            r["preferred_product"] or "", r["notes"] or "",
            r["last_contact"] or "", "",  # COO Notes — editable column
        ])
    if data:
        ws.append_rows(data)
    logger.info("Synced %d interested buyers", len(data))


def sync_outreach_log(ss):
    """Sync outreach table to log sheet."""
    headers = [
        "ID", "Lead", "Email", "Touch #", "Subject", "Sent At",
        "Channel", "Replied", "Thread ID"
    ]
    ws = _ensure_sheet(ss, "Outreach Log", headers)
    ws.clear()
    ws.append_row(headers)

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT o.*, l.company_name, l.email
               FROM outreach o JOIN leads l ON o.lead_id=l.id
               ORDER BY o.sent_at DESC LIMIT 500"""
        ).fetchall()

    data = [
        [r["id"], r["company_name"], r["email"] or "", r["touch_number"],
         r["subject"] or "", r["sent_at"] or "",
         r["channel"] if "channel" in r.keys() else "email",
         "Yes" if r["replied"] else "No", r["gmail_thread_id"] or ""]
        for r in rows
    ]
    if data:
        ws.append_rows(data)
    logger.info("Synced %d outreach records", len(data))


def sync_weekly_stats(ss):
    """Sync aggregated weekly stats."""
    headers = ["Date", "Leads Scraped", "Emails Sent", "Replies", "Proposals", "Interested"]
    ws = _ensure_sheet(ss, "Weekly Stats", headers)

    stats = get_daily_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    row = [
        today,
        stats.get("leads_scraped_today", 0),
        stats.get("emails_sent_today", 0),
        stats.get("replies_today", 0),
        stats.get("proposals_sent_today", 0),
        len(stats.get("interested_today", [])),
    ]
    ws.append_row(row)
    logger.info("Weekly stats row added: %s", today)


def run() -> bool:
    """Run full sync. Returns True on success."""
    if not _GSPREAD_OK:
        logger.warning("Sheets sync skipped — gspread not installed.")
        return False
    try:
        client = _get_client()
        ss = _get_or_create_spreadsheet(client)
        sync_leads_pipeline(ss)
        sync_interested_buyers(ss)
        sync_outreach_log(ss)
        sync_weekly_stats(ss)
        logger.info("[SheetsSync] Full sync complete → %s", _SPREADSHEET_NAME)
        return True
    except Exception as exc:
        logger.error("[SheetsSync] Failed: %s", exc)
        return False
