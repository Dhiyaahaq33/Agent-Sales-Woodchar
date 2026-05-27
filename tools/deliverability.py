"""Deliverability Manager v2 — bounce handling, unsubscribe, domain rate limiting.

Safety gate: jika bounce rate > BOUNCE_RATE_THRESHOLD, semua outreach di-pause dan COO dinotifikasi.
"""
import logging
import re
from datetime import date, datetime

import config
from db.database import get_conn
from tools.telegram_tool import send_message

logger = logging.getLogger(__name__)

_UNSUBSCRIBE_KEYWORDS = {
    "unsubscribe", "remove me", "stop emailing", "opt out",
    "opt-out", "take me off", "do not contact", "don't contact",
    "berhenti", "hapus email", "jangan hubungi",
}

_BOUNCE_SUBJECTS = {
    "delivery status notification", "undeliverable", "delivery failure",
    "mail delivery failed", "returned mail", "non-delivery", "bounce",
}


def is_bounce(subject: str) -> bool:
    """Return True if this email looks like a bounce notification."""
    return any(kw in subject.lower() for kw in _BOUNCE_SUBJECTS)


def is_unsubscribe(body: str) -> bool:
    """Return True if reply contains unsubscribe intent."""
    body_lower = body.lower()
    return any(kw in body_lower for kw in _UNSUBSCRIBE_KEYWORDS)


def add_to_blacklist(email: str = None, domain: str = None, reason: str = "MANUAL") -> None:
    """Add email or domain to blacklist table."""
    with get_conn() as conn:
        if email:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist (email, reason) VALUES (?,?)",
                    (email.lower().strip(), reason),
                )
                logger.info("Blacklisted email: %s (%s)", email, reason)
            except Exception as exc:
                logger.error("Blacklist insert failed: %s", exc)
        if domain:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist (domain, reason) VALUES (?,?)",
                    (domain.lower().strip(), reason),
                )
                logger.info("Blacklisted domain: %s (%s)", domain, reason)
            except Exception as exc:
                logger.error("Blacklist domain insert failed: %s", exc)


def is_blacklisted(email: str) -> bool:
    """Return True if email or its domain is blacklisted."""
    if not email:
        return False
    email = email.lower().strip()
    domain = email.split("@")[1] if "@" in email else ""
    with get_conn() as conn:
        hit = conn.execute(
            "SELECT 1 FROM blacklist WHERE email=? OR domain=? LIMIT 1",
            (email, domain),
        ).fetchone()
    return hit is not None


def check_domain_rate_limit(email: str) -> bool:
    """Return True if this domain has received < MAX_EMAILS_PER_DOMAIN in last 7 days.

    Returns False (blocked) if over limit.
    """
    if not email or "@" not in email:
        return True
    domain = email.split("@")[1].lower()
    with get_conn() as conn:
        count = conn.execute(
            """SELECT COUNT(*) FROM outreach o
               JOIN leads l ON o.lead_id = l.id
               WHERE LOWER(SUBSTR(l.email, INSTR(l.email,'@')+1)) = ?
               AND o.sent_at >= datetime('now', '-7 days')""",
            (domain,),
        ).fetchone()[0]
    allowed = count < config.MAX_EMAILS_PER_DOMAIN
    if not allowed:
        logger.debug("Domain rate limit hit: %s (%d sent in 7d)", domain, count)
    return allowed


def log_bounce_rate(emails_sent: int, bounces: int, unsubscribes: int) -> float:
    """Record deliverability stats and return bounce rate.

    If bounce rate exceeds threshold, pauses outreach and alerts COO.
    """
    today = date.today().isoformat()
    rate = (bounces / emails_sent * 100) if emails_sent > 0 else 0.0
    paused = rate > config.BOUNCE_RATE_THRESHOLD

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM deliverability_log WHERE date=?", (today,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE deliverability_log SET
                   emails_sent=?, bounces=?, unsubscribes=?, bounce_rate=?, paused=?
                   WHERE date=?""",
                (emails_sent, bounces, unsubscribes, rate, int(paused), today),
            )
        else:
            conn.execute(
                """INSERT INTO deliverability_log
                   (date, emails_sent, bounces, unsubscribes, bounce_rate, paused)
                   VALUES (?,?,?,?,?,?)""",
                (today, emails_sent, bounces, unsubscribes, rate, int(paused)),
            )

    if paused:
        msg = (
            f"🚨 *ALERT: Bounce Rate Critical!*\n\n"
            f"Bounce rate: *{rate:.1f}%* (threshold: {config.BOUNCE_RATE_THRESHOLD}%)\n"
            f"Emails sent today: {emails_sent}\n"
            f"Bounces: {bounces}\n\n"
            f"⛔ Outreach PAUSED automatically. Investigate Gmail reputation.\n"
            f"Resume manually with `/start` after resolving."
        )
        send_message(msg)
        logger.critical("BOUNCE RATE EXCEEDED: %.1f%% — outreach paused!", rate)

    return rate


def handle_bounce(lead_email: str) -> None:
    """Mark bounced lead and blacklist email."""
    import db.database as db
    lead = db.get_lead_by_email(lead_email)
    if lead:
        db.update_lead(lead["id"], status="DEAD", classification="BOUNCED",
                       notes="Email bounced — undeliverable")
    add_to_blacklist(email=lead_email, reason="BOUNCE")
    logger.info("Bounce handled: %s", lead_email)


def handle_unsubscribe(lead_email: str) -> None:
    """Mark unsubscribed lead as DEAD and blacklist."""
    import db.database as db
    lead = db.get_lead_by_email(lead_email)
    if lead:
        db.update_lead(lead["id"], status="DEAD", classification="UNSUBSCRIBED")
    add_to_blacklist(email=lead_email, reason="UNSUBSCRIBE")
    logger.info("Unsubscribe handled: %s", lead_email)
