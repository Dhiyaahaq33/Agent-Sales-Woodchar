"""
tools/send_time_optimizer.py — Timezone-Aware Send Time Optimizer
PT Cahaya Woodchar International — v3.0

Determines the optimal email send window for each buyer based on:
  1. Buyer's country/timezone (business hours preference)
  2. Historical reply data: which send hours get most replies per country
  3. Day-of-week patterns: avoid Monday AM and Friday PM
  4. Anti-spam: don't send outside 07:00-19:00 local time

Country → UTC offset mapping (simplified, major trading partners)
For precise timezone, pytz is used when country code is known.

Usage:
  from tools.send_time_optimizer import SendTimeOptimizer
  sto = SendTimeOptimizer()
  should_send = sto.should_send_now(lead_country="JP")
  best_hour = sto.get_best_hour(lead_country="JP")  # returns hour 0-23 local
  wait_secs = sto.seconds_until_send_window(lead_country="JP")
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Country → (timezone_name, UTC_offset_hours)
# Primary export markets for PT Cahaya Woodchar
COUNTRY_TIMEZONES = {
    "JP": ("Asia/Tokyo",        9),
    "KR": ("Asia/Seoul",        9),
    "AU": ("Australia/Sydney",  10),  # AEST
    "SG": ("Asia/Singapore",    8),
    "MY": ("Asia/Kuala_Lumpur", 8),
    "VN": ("Asia/Ho_Chi_Minh",  7),
    "TH": ("Asia/Bangkok",      7),
    "IN": ("Asia/Kolkata",      5.5),
    "PK": ("Asia/Karachi",      5),
    "BD": ("Asia/Dhaka",        6),
    "AE": ("Asia/Dubai",        4),
    "SA": ("Asia/Riyadh",       3),
    "QA": ("Asia/Qatar",        3),
    "KW": ("Asia/Kuwait",       3),
    "EG": ("Africa/Cairo",      2),
    "TR": ("Europe/Istanbul",   3),
    "DE": ("Europe/Berlin",     1),   # CET
    "NL": ("Europe/Amsterdam",  1),
    "FR": ("Europe/Paris",      1),
    "GB": ("Europe/London",     0),
    "US": ("America/New_York", -5),   # EST (most importers east coast)
    "NG": ("Africa/Lagos",      1),
    "GH": ("Africa/Accra",      0),
}

DEFAULT_UTC_OFFSET = 7  # WIB (Indonesia) if unknown

# Optimal send window per country (local time, business hours)
# Based on B2B email engagement research
SEND_WINDOWS = {
    "JP": (9, 11),    # Japan: 9-11am most responsive
    "KR": (9, 11),    # Korea: similar to Japan
    "AU": (9, 12),    # Australia: morning
    "SG": (9, 12),    # Singapore: morning
    "MY": (9, 12),
    "IN": (10, 12),   # India: mid-morning best
    "AE": (9, 12),    # UAE: morning (avoid Fri PM)
    "SA": (9, 12),    # Saudi: morning
    "DE": (9, 11),    # Germany: morning, punctual
    "NL": (9, 11),
    "GB": (9, 12),
    "US": (10, 12),   # USA: mid-morning East Coast
    "TR": (9, 12),
    "EG": (9, 12),
    "DEFAULT": (9, 12),   # Fallback: 9am-12pm local
}

# Days to avoid sending (too many emails, lower open rates)
AVOID_DAYS = {0: "weekday_priority"}  # 0=Monday: high competition, avoid very early Monday

# Minimum open rate threshold to trust historical data
MIN_HISTORICAL_SENDS = 10


class SendTimeOptimizer:
    def __init__(self):
        self._historical_cache = {}  # {country: best_hour}

    def _get_utc_offset(self, country: str) -> float:
        """Return UTC offset in hours for a country."""
        if not country:
            return DEFAULT_UTC_OFFSET
        tz_info = COUNTRY_TIMEZONES.get(country.upper())
        if tz_info:
            return tz_info[1]
        return DEFAULT_UTC_OFFSET

    def _get_local_time(self, country: str) -> datetime:
        """Get current local time for a country."""
        try:
            import pytz
            tz_info = COUNTRY_TIMEZONES.get(country.upper() if country else "")
            if tz_info:
                tz = pytz.timezone(tz_info[0])
                return datetime.now(tz)
        except Exception:
            pass
        # Fallback: simple UTC offset math
        offset = self._get_utc_offset(country)
        utc_now = datetime.now(timezone.utc)
        local = utc_now + timedelta(hours=offset)
        return local

    def _get_historical_best_hour(self, country: str) -> Optional[int]:
        """
        Query outreach table to find which send hours get the most replies
        for a given country. Returns best hour 0-23 or None if insufficient data.
        """
        if country in self._historical_cache:
            return self._historical_cache[country]
        try:
            from db.database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT CAST(strftime('%H', o.sent_at) AS INTEGER) as send_hour,
                              COUNT(*) as total,
                              SUM(o.replied) as replies
                       FROM outreach o
                       JOIN leads l ON l.id = o.lead_id
                       WHERE l.country = ?
                         AND o.sent_at IS NOT NULL
                       GROUP BY send_hour
                       HAVING total >= 3
                       ORDER BY CAST(replies AS FLOAT) / total DESC
                       LIMIT 1""",
                    (country,)
                ).fetchall()
            if rows:
                best_hour = rows[0]["send_hour"]
                total = rows[0]["total"]
                if total >= MIN_HISTORICAL_SENDS:
                    self._historical_cache[country] = best_hour
                    logger.debug("Historical best hour for %s: %d:00 (n=%d)", country, best_hour, total)
                    return best_hour
        except Exception as e:
            logger.debug("Historical send hour query failed: %s", e)
        return None

    def get_best_hour(self, country: str) -> int:
        """
        Returns the best send hour (local time, 0-23) for a country.
        Priority: historical data > country-specific window start > default 9am.
        """
        # Try historical data first
        hist_hour = self._get_historical_best_hour(country)
        if hist_hour is not None:
            return hist_hour

        # Fall back to country send window start
        window = SEND_WINDOWS.get(country.upper() if country else "", SEND_WINDOWS["DEFAULT"])
        return window[0]

    def is_in_send_window(self, country: str) -> bool:
        """
        Returns True if current time is within the optimal send window for this country.
        """
        local_time = self._get_local_time(country)
        local_hour = local_time.hour
        local_weekday = local_time.weekday()  # 0=Monday

        # Never send on weekends to business buyers
        if local_weekday >= 5:  # Saturday=5, Sunday=6
            return False

        # Avoid sending before 7am or after 7pm local time
        if local_hour < 7 or local_hour >= 19:
            return False

        # Get optimal window
        window = SEND_WINDOWS.get(country.upper() if country else "", SEND_WINDOWS["DEFAULT"])
        window_start, window_end = window

        # Check historical override
        hist_hour = self._get_historical_best_hour(country)
        if hist_hour is not None:
            # Be flexible: ±1 hour around best historical hour
            return abs(local_hour - hist_hour) <= 1

        return window_start <= local_hour < window_end

    def should_send_now(self, country: str) -> bool:
        """Alias for is_in_send_window. Returns True if OK to send now."""
        return self.is_in_send_window(country)

    def seconds_until_send_window(self, country: str) -> int:
        """
        Returns seconds to wait until the send window opens.
        Returns 0 if already in window.
        """
        if self.is_in_send_window(country):
            return 0

        local_time = self._get_local_time(country)
        local_hour = local_time.hour
        local_weekday = local_time.weekday()

        # Get target hour
        window = SEND_WINDOWS.get(country.upper() if country else "", SEND_WINDOWS["DEFAULT"])
        target_hour = window[0]

        if local_weekday >= 5:
            # Weekend → wait until Monday
            days_until_monday = (7 - local_weekday)
            target = local_time + timedelta(days=days_until_monday)
            target = target.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        elif local_hour >= 19:
            # Evening → wait until next business day
            target = local_time + timedelta(days=1)
            target = target.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        elif local_hour < 7:
            # Too early → wait until window opens today
            target = local_time.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        else:
            # After window end today → next day
            target = local_time + timedelta(days=1)
            target = target.replace(hour=target_hour, minute=0, second=0, microsecond=0)

        wait = (target - local_time).total_seconds()
        return max(0, int(wait))

    def get_optimal_batch_order(self, leads: list) -> list:
        """
        Sort leads by country timezone so emails are sent during business hours.
        Leads with SEND_TIME_OPTIMIZE=false are not reordered.
        """
        import os
        if os.getenv("SEND_TIME_OPTIMIZE", "true").lower() != "true":
            return leads

        def priority(lead):
            country = lead.get("country", "")
            local_time = self._get_local_time(country)
            # Score: already in window → 0 (send first), farther away → higher
            if self.is_in_send_window(country):
                return 0
            return self.seconds_until_send_window(country)

        return sorted(leads, key=priority)

    def get_country_status(self, country: str) -> dict:
        """Return send-time status for /risk or diagnostic purposes."""
        local_time = self._get_local_time(country)
        in_window = self.is_in_send_window(country)
        wait = self.seconds_until_send_window(country)
        return {
            "country": country,
            "local_time": local_time.strftime("%Y-%m-%d %H:%M %Z"),
            "in_send_window": in_window,
            "best_hour": self.get_best_hour(country),
            "wait_seconds": wait,
            "wait_human": f"{wait // 3600}h {(wait % 3600) // 60}m" if wait > 0 else "Now",
        }
