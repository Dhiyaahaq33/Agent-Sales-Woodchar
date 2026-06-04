"""
tools/email_tracker.py — Email Open & Click Tracking
PT Cahaya Woodchar International — v3.0

Provides:
  1. Bit.ly link shortening with click tracking
  2. Tracking pixel injection (1x1 transparent GIF served via Flask /pixel endpoint)
  3. Open event recording: updates outreach.open_count + leads.last_open_at
  4. Telegram alert on FIRST open by INTERESTED lead (high urgency signal)

Architecture:
  - Flask /pixel/<pixel_id>.gif endpoint → records open → updates DB
  - This endpoint runs on the same Flask health check server (reuses port)
  - EmailTracker.inject_tracking(body, outreach_id) → returns modified body

Requirements:
  - BITLY_ACCESS_TOKEN in .env (optional — falls back to plain URLs)
  - HEALTH_CHECK_PORT (default 8080) must be accessible from internet (or ngrok)

Usage:
  from tools.email_tracker import EmailTracker
  tracker = EmailTracker()
  pixel_id, modified_body = tracker.inject_tracking(body, outreach_id=42)
  short_url = tracker.shorten_url("https://indonesiancharcoalsupplier.com")
"""

import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 1x1 transparent GIF bytes
_TRACKING_PIXEL_GIF = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80,
    0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x21, 0xF9, 0x04,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x2C, 0x00, 0x00, 0x00, 0x00, 0x01,
    0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44, 0x01, 0x00, 0x3B
])


class EmailTracker:
    def __init__(self):
        self.bitly_token = os.getenv("BITLY_ACCESS_TOKEN", "")
        self.server_url = os.getenv("TRACKING_SERVER_URL", "")  # e.g. https://yourserver.com
        self.port = int(os.getenv("HEALTH_CHECK_PORT", "8080"))
        self.enabled = bool(self.server_url)

        if not self.server_url:
            logger.debug("EmailTracker: TRACKING_SERVER_URL not set — pixel tracking disabled")
        if not self.bitly_token:
            logger.debug("EmailTracker: BITLY_ACCESS_TOKEN not set — Bit.ly disabled")

    # ------------------------------------------------------------------ #
    #  Pixel tracking                                                      #
    # ------------------------------------------------------------------ #

    def generate_pixel_id(self, outreach_id: int) -> str:
        """Generate a unique pixel ID for an outreach row."""
        raw = f"{outreach_id}_{time.time()}_{secrets.token_hex(8)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get_pixel_url(self, pixel_id: str) -> str:
        """Return the tracking pixel URL to embed in emails."""
        if not self.server_url:
            return ""
        return f"{self.server_url.rstrip('/')}/pixel/{pixel_id}.gif"

    def inject_tracking(self, body: str, outreach_id: int,
                        lead_id: int = None) -> Tuple[str, str]:
        """
        Injects a 1x1 tracking pixel into an email body.
        Returns (pixel_id, modified_body).

        If TRACKING_SERVER_URL is not set, returns ("", original_body).
        """
        if not self.enabled:
            return "", body

        pixel_id = self.generate_pixel_id(outreach_id)
        pixel_url = self.get_pixel_url(pixel_id)

        # Store pixel_id in DB before sending
        self._register_pixel(outreach_id, lead_id, pixel_id)

        # Inject invisible pixel at end of email body
        pixel_html = f'\n\n<img src="{pixel_url}" width="1" height="1" alt="" style="display:none">'

        # For plain text emails: add as raw URL comment (most clients ignore it)
        if "<html" not in body.lower():
            # Plain text: pixel won't work but register pixel_id for later
            logger.debug("Plain text body — pixel tracking URL registered but not injected")
            return pixel_id, body

        modified = body.rstrip() + pixel_html
        return pixel_id, modified

    def _register_pixel(self, outreach_id: int, lead_id: int, pixel_id: str):
        """Save pixel_id → outreach mapping to email_opens table."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO email_opens
                       (outreach_id, lead_id, pixel_id)
                       VALUES (?, ?, ?)""",
                    (outreach_id, lead_id or 0, pixel_id)
                )
                # Also update outreach.pixel_id column
                conn.execute(
                    "UPDATE outreach SET pixel_id = ? WHERE id = ?",
                    (pixel_id, outreach_id)
                )
        except Exception as e:
            logger.error("Failed to register pixel %s: %s", pixel_id, e)

    # ------------------------------------------------------------------ #
    #  Open event handler (called by Flask endpoint)                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def record_open(pixel_id: str, ip_address: str = "", user_agent: str = "") -> bytes:
        """
        Called when tracking pixel is fetched.
        Updates DB and returns the 1x1 GIF bytes.
        """
        try:
            from db.database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT outreach_id, lead_id, open_count FROM email_opens WHERE pixel_id = ?",
                    (pixel_id,)
                ).fetchone()

                if not row:
                    logger.debug("Unknown pixel ID: %s", pixel_id)
                    return _TRACKING_PIXEL_GIF

                outreach_id = row["outreach_id"]
                lead_id = row["lead_id"]
                open_count = row["open_count"] or 0
                is_first_open = (open_count == 0)

                # Update email_opens
                conn.execute(
                    """UPDATE email_opens
                       SET opened_at = COALESCE(opened_at, datetime('now')),
                           ip_address = COALESCE(ip_address, ?),
                           user_agent = COALESCE(user_agent, ?),
                           open_count = open_count + 1
                       WHERE pixel_id = ?""",
                    (ip_address, user_agent, pixel_id)
                )
                # Update outreach row
                conn.execute(
                    "UPDATE outreach SET open_count = open_count + 1 WHERE id = ?",
                    (outreach_id,)
                )
                # Update lead
                conn.execute(
                    """UPDATE leads
                       SET last_open_at = datetime('now'),
                           open_count = COALESCE(open_count, 0) + 1
                       WHERE id = ?""",
                    (lead_id,)
                )

                lead_company = ""
                lead_row = conn.execute(
                    "SELECT company, company_name, status FROM leads WHERE id = ?",
                    (lead_id,)
                ).fetchone()
                if lead_row:
                    lead_company = lead_row["company"] or lead_row["company_name"] or ""
                    # Upgrade OUTREACH → ENGAGED if they opened
                    if lead_row["status"] == "OUTREACH":
                        conn.execute(
                            "UPDATE leads SET status = 'ENGAGED' WHERE id = ? AND status = 'OUTREACH'",
                            (lead_id,)
                        )

            logger.info(
                "Email open recorded: pixel=%s outreach=%d lead=%d (%s) open#%d",
                pixel_id[:8], outreach_id, lead_id, lead_company, open_count + 1
            )

            # Alert COO on FIRST open (strong buying signal)
            if is_first_open:
                try:
                    from tools.telegram_tool import send_message
                    send_message(
                        f"👀 *Email Opened!*\n"
                        f"Company: {lead_company}\n"
                        f"Outreach #{outreach_id} — first open detected\n"
                        f"Lead ID: {lead_id}"
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error("Error recording email open for pixel %s: %s", pixel_id, e)

        return _TRACKING_PIXEL_GIF

    # ------------------------------------------------------------------ #
    #  Bit.ly link shortening                                             #
    # ------------------------------------------------------------------ #

    def shorten_url(self, long_url: str, title: str = "") -> str:
        """
        Shorten a URL via Bit.ly API. Falls back to original URL if unavailable.
        """
        if not self.bitly_token:
            return long_url
        try:
            import requests
            resp = requests.post(
                "https://api-ssl.bitly.com/v4/shorten",
                headers={
                    "Authorization": f"Bearer {self.bitly_token}",
                    "Content-Type": "application/json",
                },
                json={"long_url": long_url, "domain": "bit.ly"},
                timeout=10,
            )
            if resp.status_code == 201:
                data = resp.json()
                short = data.get("link", long_url)
                logger.debug("Bit.ly: %s → %s", long_url[:60], short)
                return short
            else:
                logger.warning("Bit.ly error %d: %s", resp.status_code, resp.text[:100])
                return long_url
        except Exception as e:
            logger.warning("Bit.ly shorten failed: %s", e)
            return long_url

    def inject_tracked_links(self, body: str, outreach_id: int) -> str:
        """
        Replace plain URLs in email body with Bit.ly tracked short URLs.
        Only replaces our company website and catalog links.
        """
        import re
        company_url = os.getenv("COMPANY_WEBSITE", "https://indonesiancharcoalsupplier.com")
        if company_url in body and self.bitly_token:
            short = self.shorten_url(company_url, title=f"Outreach {outreach_id}")
            body = body.replace(company_url, short)
        return body

    # ------------------------------------------------------------------ #
    #  Flask route registration                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def register_flask_routes(app):
        """
        Register /pixel/<pixel_id>.gif route on an existing Flask app.
        Call this in health_check.create_app() to add tracking endpoint.
        """
        from flask import Response, request

        @app.route("/pixel/<pixel_id>.gif", methods=["GET"])
        def tracking_pixel(pixel_id):
            ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            ua = request.headers.get("User-Agent", "")
            gif_bytes = EmailTracker.record_open(pixel_id, ip, ua)
            return Response(
                gif_bytes,
                status=200,
                headers={
                    "Content-Type": "image/gif",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )


# Module-level singleton
_tracker = None

def get_email_tracker() -> EmailTracker:
    global _tracker
    if _tracker is None:
        _tracker = EmailTracker()
    return _tracker
