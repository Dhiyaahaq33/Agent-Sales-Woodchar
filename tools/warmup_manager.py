"""
tools/warmup_manager.py — Email Warmup Manager
PT Cahaya Woodchar International — PRD v2.1

Progressive daily email send limit to build Gmail reputation:
  Week 1: 20/day
  Week 2: 50/day
  Week 3: 100/day
  Week 4: 150/day
  Week 5+: 200/day (full throttle)

Usage:
  from tools.warmup_manager import WarmupManager
  wm = WarmupManager()
  if wm.can_send():
      send_email(...)
      wm.record_send()
  else:
      logging.info(f"Warmup limit reached today: {wm.get_limit()}/day")
"""

import os
import logging
from datetime import date, timedelta
from db.database import get_conn

logger = logging.getLogger(__name__)

# Warmup schedule: (days_since_start, daily_limit)
WARMUP_SCHEDULE = [
    (7,  20),   # Week 1: 20/day
    (14, 50),   # Week 2: 50/day
    (21, 100),  # Week 3: 100/day
    (28, 150),  # Week 4: 150/day
]
FULL_LIMIT = 200  # After warmup period


class WarmupManager:
    def __init__(self):
        self.warmup_mode = os.getenv("WARMUP_MODE", "false").lower() == "true"
        self.start_date_str = os.getenv("WARMUP_START_DATE", "")
        self.start_date = self._parse_start_date()

    def _parse_start_date(self):
        """Parse WARMUP_START_DATE from env, default to today if not set."""
        if self.start_date_str:
            try:
                return date.fromisoformat(self.start_date_str)
            except ValueError:
                logger.warning(f"Invalid WARMUP_START_DATE: {self.start_date_str}, defaulting to today")
        return date.today()

    def is_warmup_active(self) -> bool:
        """Returns True if warmup mode is enabled."""
        return self.warmup_mode

    def get_days_since_start(self) -> int:
        """Days elapsed since warmup began."""
        return (date.today() - self.start_date).days

    def get_limit(self) -> int:
        """
        Returns today's email send limit based on warmup schedule.
        If warmup is disabled, returns FULL_LIMIT.
        """
        if not self.warmup_mode:
            return FULL_LIMIT

        days = self.get_days_since_start()
        for threshold, limit in WARMUP_SCHEDULE:
            if days < threshold:
                return limit
        return FULL_LIMIT

    def get_sent_today(self) -> int:
        """Count emails already sent today from outreach log."""
        today = date.today().isoformat()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) = ?",
                (today,)
            ).fetchone()
        return row[0] if row else 0

    def can_send(self) -> bool:
        """Returns True if we haven't hit today's limit yet."""
        limit = self.get_limit()
        sent = self.get_sent_today()
        if sent >= limit:
            logger.info(f"Warmup limit reached: {sent}/{limit} emails sent today")
            return False
        return True

    def record_send(self):
        """
        Called after each email send. Logging is handled by outreach table inserts,
        so this is mostly a no-op hook for future extension (e.g., per-account counts).
        """
        pass  # outreach table insert already records send time

    def get_status(self) -> dict:
        """Return human-readable warmup status dict."""
        days = self.get_days_since_start()
        limit = self.get_limit()
        sent = self.get_sent_today()
        remaining = max(0, limit - sent)

        if not self.warmup_mode:
            phase = "Disabled (full speed)"
        elif days < 7:
            phase = f"Week 1 (Day {days + 1})"
        elif days < 14:
            phase = f"Week 2 (Day {days + 1})"
        elif days < 21:
            phase = f"Week 3 (Day {days + 1})"
        elif days < 28:
            phase = f"Week 4 (Day {days + 1})"
        else:
            phase = f"Graduated (Day {days + 1})"

        return {
            "warmup_active": self.warmup_mode,
            "phase": phase,
            "days_since_start": days,
            "daily_limit": limit,
            "sent_today": sent,
            "remaining_today": remaining,
            "start_date": self.start_date.isoformat(),
        }

    def log_status_to_db(self):
        """
        Persist warmup status snapshot to health_log table for monitoring.
        Called by scheduler daily.
        """
        status = self.get_status()
        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO health_log (check_time, component, status, details)
                       VALUES (datetime('now'), 'warmup_manager', 'OK', ?)""",
                    (str(status),)
                )
            logger.info(f"Warmup status: {status}")
        except Exception as e:
            logger.error(f"Failed to log warmup status: {e}")


# --- Convenience singleton ---
_warmup_manager = None

def get_warmup_manager() -> WarmupManager:
    global _warmup_manager
    if _warmup_manager is None:
        _warmup_manager = WarmupManager()
    return _warmup_manager
