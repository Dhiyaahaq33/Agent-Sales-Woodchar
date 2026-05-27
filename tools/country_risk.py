"""
tools/country_risk.py — Country Payment Risk & OFAC Sanctions Check
PT Cahaya Woodchar International — PRD v2.1

Loads country_payment_risk.json and provides:
  - get_risk(country_code) → dict with risk level + notes
  - is_blocked(country_code) → True if OFAC/sanctions
  - is_high_risk(country_code) → True if HIGH or BLOCKED
  - check_and_alert(country_code) → logs + Telegram alert for BLOCKED
  - update_lead_risk(lead_id, country_code) → saves to DB
  - run_ofac_refresh() → placeholder for weekly OFAC list refresh

Risk levels:
  LOW     → Normal processing
  MEDIUM  → Flag for COO attention, L/C recommended
  HIGH    → Skip or require strong reference + L/C
  BLOCKED → OFAC/EU sanctions — STRICTLY NO EXPORT

Usage:
  from tools.country_risk import CountryRisk
  cr = CountryRisk()
  result = cr.get_risk("IR")   # {"risk": "BLOCKED", "notes": "OFAC Sanctions — STRICTLY BLOCKED"}
  if cr.is_blocked("IR"):
      skip_lead()
"""

import json
import logging
import os
from pathlib import Path
from db.database import get_conn

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent.parent / "data" / "country_payment_risk.json"

RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "BLOCKED"]
UNKNOWN_COUNTRY = {
    "name": "Unknown",
    "risk": "MEDIUM",
    "notes": "Country not in database — treat as MEDIUM risk, verify manually"
}


class CountryRisk:
    def __init__(self):
        self.ofac_enabled = os.getenv("OFAC_CHECK_ENABLED", "true").lower() == "true"
        self._data = self._load()

    def _load(self) -> dict:
        """Load country risk JSON from disk."""
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            countries = raw.get("countries", {})
            logger.info(f"CountryRisk: loaded {len(countries)} countries from {DATA_FILE.name}")
            return countries
        except FileNotFoundError:
            logger.error(f"country_payment_risk.json not found at {DATA_FILE}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in country_payment_risk.json: {e}")
            return {}

    def reload(self):
        """Reload data from disk (called after OFAC refresh)."""
        self._data = self._load()

    def get_risk(self, country_code: str) -> dict:
        """
        Returns risk dict for a 2-letter ISO country code.
        Example: get_risk("IN") → {"name": "India", "risk": "MEDIUM", "notes": "..."}
        """
        if not country_code:
            return UNKNOWN_COUNTRY.copy()
        code = country_code.upper().strip()
        entry = self._data.get(code)
        if entry:
            return dict(entry)
        logger.warning(f"CountryRisk: unknown country code '{code}'")
        result = UNKNOWN_COUNTRY.copy()
        result["name"] = code
        return result

    def is_blocked(self, country_code: str) -> bool:
        """True if country is on OFAC or EU sanctions list."""
        if not self.ofac_enabled:
            return False
        return self.get_risk(country_code).get("risk") == "BLOCKED"

    def is_high_risk(self, country_code: str) -> bool:
        """True if risk level is HIGH or BLOCKED."""
        risk = self.get_risk(country_code).get("risk", "MEDIUM")
        return risk in ("HIGH", "BLOCKED")

    def is_medium_or_above(self, country_code: str) -> bool:
        """True if MEDIUM, HIGH, or BLOCKED."""
        risk = self.get_risk(country_code).get("risk", "MEDIUM")
        return risk in ("MEDIUM", "HIGH", "BLOCKED")

    def check_and_alert(self, country_code: str, lead_id: int = None, buyer_name: str = "") -> dict:
        """
        Checks country risk and sends Telegram alert for BLOCKED countries.
        Returns the risk dict.
        """
        risk_info = self.get_risk(country_code)
        risk_level = risk_info.get("risk", "MEDIUM")

        if risk_level == "BLOCKED" and self.ofac_enabled:
            self._send_blocked_alert(country_code, risk_info, lead_id, buyer_name)
        elif risk_level == "HIGH":
            logger.warning(
                f"HIGH risk country {country_code} ({risk_info.get('name')}): "
                f"{risk_info.get('notes')} — Lead {lead_id or 'N/A'}"
            )
        elif risk_level == "MEDIUM":
            logger.info(
                f"MEDIUM risk country {country_code}: {risk_info.get('notes')} — "
                f"L/C recommended for lead {lead_id or 'N/A'}"
            )

        return risk_info

    def _send_blocked_alert(self, country_code: str, risk_info: dict, lead_id, buyer_name: str):
        """Send Telegram alert when a BLOCKED country is detected."""
        try:
            from tools.telegram_tool import send_message
            msg = (
                "🚨 *SANCTIONS ALERT — BLOCKED COUNTRY*\n\n"
                f"⛔ Country: {risk_info.get('name', country_code)} ({country_code})\n"
                f"📋 Reason: {risk_info.get('notes', 'OFAC Sanctions')}\n"
            )
            if lead_id:
                msg += f"🆔 Lead ID: {lead_id}\n"
            if buyer_name:
                msg += f"🏢 Buyer: {buyer_name}\n"
            msg += "\n⚠️ DO NOT proceed with this lead. Mark as DEAD immediately."
            send_message(msg)
            logger.critical(
                f"BLOCKED country detected: {country_code} — Lead {lead_id} — {buyer_name}"
            )
        except Exception as e:
            logger.error(f"Failed to send BLOCKED alert via Telegram: {e}")
            # Still log to stderr so it doesn't go unnoticed
            print(f"!!! BLOCKED COUNTRY ALERT: {country_code} Lead {lead_id} {buyer_name} !!!")

    def update_lead_risk(self, lead_id: int, country_code: str):
        """
        Save country_risk level to leads table and update status to DEAD if BLOCKED.
        """
        risk_info = self.check_and_alert(country_code, lead_id)
        risk_level = risk_info.get("risk", "MEDIUM")

        try:
            with get_conn() as conn:
                # Update country_risk column (added in migrate_v21)
                conn.execute(
                    "UPDATE leads SET country_risk = ? WHERE id = ?",
                    (risk_level, lead_id)
                )
                # Auto-kill BLOCKED leads
                if risk_level == "BLOCKED":
                    conn.execute(
                        "UPDATE leads SET status = 'DEAD', notes = ? WHERE id = ?",
                        (f"AUTO-BLOCKED: {risk_info.get('notes', 'Sanctions')}", lead_id)
                    )
                    logger.warning(f"Lead {lead_id} marked DEAD — BLOCKED country {country_code}")
        except Exception as e:
            logger.error(f"Failed to update country risk for lead {lead_id}: {e}")

    def get_summary(self) -> dict:
        """Return count of countries per risk level."""
        summary = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "BLOCKED": 0, "TOTAL": 0}
        for entry in self._data.values():
            risk = entry.get("risk", "MEDIUM")
            if risk in summary:
                summary[risk] += 1
            summary["TOTAL"] += 1
        return summary

    def run_ofac_refresh(self):
        """
        Placeholder for weekly OFAC sanctions list refresh.
        Future implementation: fetch from https://sanctionssearch.ofac.treas.gov/
        and update country_payment_risk.json with any changes.

        Currently: just reloads from disk and logs.
        Called by scheduler Sunday 04:00.
        """
        logger.info("OFAC refresh: reloading country_payment_risk.json from disk")
        self.reload()
        summary = self.get_summary()
        logger.info(f"OFAC refresh complete: {summary}")

        # Log to health_log
        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO health_log (check_time, component, status, details)
                       VALUES (datetime('now'), 'ofac_refresh', 'OK', ?)""",
                    (str(summary),)
                )
        except Exception as e:
            logger.error(f"Failed to log OFAC refresh: {e}")

        return summary

    def get_all_blocked(self) -> list:
        """Return list of all BLOCKED country codes and names."""
        return [
            {"code": code, "name": entry["name"], "notes": entry["notes"]}
            for code, entry in self._data.items()
            if entry.get("risk") == "BLOCKED"
        ]

    def format_risk_for_telegram(self, country_code: str) -> str:
        """Format risk info as Telegram message for /risk command."""
        info = self.get_risk(country_code)
        risk = info.get("risk", "UNKNOWN")
        emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "BLOCKED": "⛔"}.get(risk, "⚪")
        return (
            f"{emoji} *Country Risk: {info.get('name', country_code)} ({country_code.upper()})*\n"
            f"Risk Level: `{risk}`\n"
            f"Notes: {info.get('notes', 'No notes')}"
        )


# --- Module-level singleton ---
_country_risk = None

def get_country_risk() -> CountryRisk:
    global _country_risk
    if _country_risk is None:
        _country_risk = CountryRisk()
    return _country_risk
