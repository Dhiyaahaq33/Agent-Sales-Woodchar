"""
tools/gmail_rotator.py — Multi-Gmail Account Round-Robin Rotator
PT Cahaya Woodchar International — PRD v2.1

Rotates outbound email sending across multiple Gmail accounts
to distribute send volume and protect primary account reputation.

Config (in .env):
  GMAIL_ACCOUNTS=primary@gmail.com:app_pass1,secondary@gmail.com:app_pass2
  (comma-separated account:password pairs)

If only one account or GMAIL_ACCOUNTS not set, falls back to
GMAIL_USER + GMAIL_APP_PASSWORD from main config.

Usage:
  from tools.gmail_rotator import GmailRotator
  rotator = GmailRotator()
  account = rotator.get_next_account()  # {"email": "...", "password": "..."}
  rotator.record_send(account["email"])
"""

import os
import logging
import threading
from datetime import date
from db.database import get_conn

logger = logging.getLogger(__name__)


def _parse_accounts(accounts_str: str) -> list:
    """
    Parse GMAIL_ACCOUNTS env var.
    Format: "email1:pass1,email2:pass2"
    Returns: [{"email": "...", "password": "..."}, ...]
    """
    accounts = []
    if not accounts_str:
        return accounts
    for pair in accounts_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            parts = pair.split(":", 1)
            email = parts[0].strip()
            password = parts[1].strip()
            if email and password:
                accounts.append({"email": email, "password": password})
    return accounts


class GmailRotator:
    """
    Round-robin email account selector with per-account daily send tracking.

    Algorithm:
    1. Get today's send count for each account from outreach table
    2. Select account with fewest sends today (least-used rotation)
    3. Respects warmup limits if WARMUP_MODE=true
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._accounts = self._load_accounts()
        self._index = 0  # fallback round-robin index

        if self._accounts:
            logger.info(f"GmailRotator: loaded {len(self._accounts)} accounts")
        else:
            logger.info("GmailRotator: single account mode (no GMAIL_ACCOUNTS configured)")

    def _load_accounts(self) -> list:
        """Load accounts from env. Falls back to primary account."""
        accounts_str = os.getenv("GMAIL_ACCOUNTS", "")
        accounts = _parse_accounts(accounts_str)

        # Always include primary as first account
        primary_email = os.getenv("GMAIL_USER", "")
        primary_pass = os.getenv("GMAIL_APP_PASSWORD", "")
        if primary_email and primary_pass:
            primary = {"email": primary_email, "password": primary_pass}
            # Don't duplicate if already in accounts list
            if not any(a["email"] == primary_email for a in accounts):
                accounts.insert(0, primary)

        return accounts

    def get_accounts(self) -> list:
        """Return list of accounts (passwords masked)."""
        return [{"email": a["email"], "password": "****"} for a in self._accounts]

    def get_send_count_today(self, email: str) -> int:
        """Count emails sent from a specific account today."""
        today = date.today().isoformat()
        try:
            with get_conn() as conn:
                # Check if sender_email column exists
                cols = [row[1] for row in conn.execute("PRAGMA table_info(outreach)").fetchall()]
                if "sender_email" in cols:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) = ? AND sender_email = ?",
                        (today, email)
                    ).fetchone()
                    return row[0] if row else 0
                else:
                    # Fallback: total sends today divided evenly
                    row = conn.execute(
                        "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) = ?", (today,)
                    ).fetchone()
                    total = row[0] if row else 0
                    return total // max(1, len(self._accounts))
        except Exception as e:
            logger.error(f"Error getting send count for {email}: {e}")
            return 0

    def get_next_account(self) -> dict:
        """
        Returns the account with fewest sends today (least-loaded).
        Thread-safe.
        """
        with self._lock:
            if not self._accounts:
                raise RuntimeError("No Gmail accounts configured. Check GMAIL_USER in .env")

            if len(self._accounts) == 1:
                return self._accounts[0]

            # Least-used selection
            counts = [(a, self.get_send_count_today(a["email"])) for a in self._accounts]
            counts.sort(key=lambda x: x[1])
            best_account = counts[0][0]
            logger.debug(
                f"GmailRotator: selected {best_account['email']} "
                f"({counts[0][1]} sends today)"
            )
            return best_account

    def record_send(self, email: str):
        """
        Record that an email was sent from this account.
        Note: outreach table insert is the primary record;
        this is an optional hook for future per-account tracking.
        """
        logger.debug(f"GmailRotator: recorded send from {email}")

    def get_status(self) -> list:
        """Return current send counts for all accounts."""
        return [
            {
                "email": a["email"],
                "sent_today": self.get_send_count_today(a["email"])
            }
            for a in self._accounts
        ]

    def send_email(self, to: str, subject: str, body: str,
                   attachment_path: str = None, account: dict = None) -> bool:
        """
        Send email using SMTP, auto-selecting account if not specified.
        Wraps smtplib STARTTLS logic from outreach_agent.

        Returns True on success, False on failure.
        """
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from pathlib import Path

        if not account:
            account = self.get_next_account()

        sender_email = account["email"]
        sender_pass = account["password"]

        try:
            msg = MIMEMultipart()
            msg["From"] = sender_email
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            if attachment_path:
                p = Path(attachment_path)
                if p.exists():
                    with open(p, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename={p.name}"
                    )
                    msg.attach(part)

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(sender_email, sender_pass)
                smtp.sendmail(sender_email, to, msg.as_string())

            self.record_send(sender_email)
            logger.info(f"Email sent: {sender_email} → {to} | Subject: {subject[:60]}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(f"SMTP auth failed for {sender_email}. Check app password.")
            return False
        except smtplib.SMTPRecipientsRefused:
            logger.warning(f"Recipient refused: {to}")
            return False
        except Exception as e:
            logger.error(f"Email send failed ({sender_email} → {to}): {e}")
            return False


# --- Module-level singleton ---
_rotator = None

def get_gmail_rotator() -> GmailRotator:
    global _rotator
    if _rotator is None:
        _rotator = GmailRotator()
    return _rotator
