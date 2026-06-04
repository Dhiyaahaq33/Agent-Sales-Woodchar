"""
agents/churn_detector.py — Intelligent Churn Risk Detector
PT Cahaya Woodchar International — v3.0

Identifies INTERESTED/OUTREACH leads that are going cold and need intervention.

Churn risk factors (weighted):
  - days_since_last_contact:  40% weight
  - days_since_last_open:     25% weight
  - total_touches:            15% weight (many touches = lower interest)
  - score_decay:              10% weight (time since enrichment)
  - competitor_mention:        5% weight (if they mentioned competitor)
  - country_payment_risk:      5% weight

Risk levels:
  0.0-0.3: Safe (recently contacted, high engagement)
  0.3-0.6: Warning (slowing down, needs attention)
  0.6-0.8: Danger (likely losing interest)
  0.8-1.0: Critical (almost lost — send break-up email or escalate)

Actions triggered:
  Critical: Send to FollowUpAgent for break-up email (touch 4) if not sent
  Danger:   COO Telegram alert
  Warning:  Automatically schedule next follow-up sooner

Runs daily at 11:00 WIB.

Usage:
  from agents.churn_detector import ChurnDetector
  detector = ChurnDetector()
  detector.run()
"""

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Risk factor weights (must sum to 1.0)
WEIGHTS = {
    "days_since_contact": 0.40,
    "days_since_open":    0.25,
    "touch_count":        0.15,
    "score_decay":        0.10,
    "competitor_flag":    0.05,
    "country_risk":       0.05,
}

# Days thresholds for each risk factor
DAYS_CONTACT_RISK = {
    (0,  3):  0.0,
    (3,  7):  0.1,
    (7,  14): 0.3,
    (14, 21): 0.5,
    (21, 30): 0.7,
    (30, 60): 0.85,
    (60, 9999): 1.0,
}

DAYS_OPEN_RISK = {
    (0,  7):   0.0,
    (7,  14):  0.2,
    (14, 30):  0.4,
    (30, 9999): 0.8,  # Never opened = high risk
}

TOUCH_RISK = {
    (0, 1): 0.1,  # Only 1 touch — too early to judge
    (1, 3): 0.2,  # Normal
    (3, 5): 0.4,  # Getting repetitive
    (5, 9999): 0.7,  # Exhausted follow-ups
}

COUNTRY_RISK_WEIGHTS = {
    "LOW":     0.0,
    "MEDIUM":  0.3,
    "HIGH":    0.6,
    "BLOCKED": 1.0,
}


def _range_score(value: float, range_dict: dict) -> float:
    """Look up a value in a range dictionary."""
    for (low, high), score in range_dict.items():
        if low <= value < high:
            return score
    return 1.0  # Default high risk if out of range


class ChurnDetector:
    def __init__(self):
        pass

    def _get_at_risk_leads(self) -> list:
        """Get all OUTREACH and INTERESTED leads for churn analysis."""
        from db.database import get_conn
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT l.id, l.company, l.company_name, l.email, l.country,
                              l.status, l.score, l.classification, l.notes,
                              l.last_open_at, l.open_count, l.country_risk,
                              l.updated_at, l.created_at,
                              MAX(o.sent_at) as last_contact,
                              COUNT(o.id) as touch_count,
                              MAX(o.touch_number) as max_touch,
                              SUM(o.replied) as total_replies
                       FROM leads l
                       LEFT JOIN outreach o ON l.id = o.lead_id
                       WHERE l.status IN ('OUTREACH', 'INTERESTED', 'REPLIED', 'ENGAGED')
                         AND l.email IS NOT NULL
                       GROUP BY l.id
                       ORDER BY l.updated_at ASC"""
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Churn query failed: %s", e)
            return []

    def _calculate_churn_risk(self, lead: dict) -> dict:
        """
        Calculate 0.0-1.0 churn risk score for a lead.
        Returns dict with score and contributing factors.
        """
        today = date.today()

        # Factor 1: Days since last contact
        last_contact_str = lead.get("last_contact") or lead.get("updated_at", "")
        try:
            last_contact = datetime.fromisoformat(str(last_contact_str)).date()
            days_contact = (today - last_contact).days
        except Exception:
            days_contact = 30  # Unknown → assume high risk
        contact_score = _range_score(days_contact, DAYS_CONTACT_RISK)

        # Factor 2: Days since last email open
        last_open_str = lead.get("last_open_at", "")
        if last_open_str:
            try:
                last_open = datetime.fromisoformat(str(last_open_str)).date()
                days_open = (today - last_open).days
            except Exception:
                days_open = 30
        else:
            # Never opened → high risk
            days_open = 999
        open_score = _range_score(days_open, DAYS_OPEN_RISK)

        # Factor 3: Touch count (many unanswered touches → losing interest)
        touch_count = lead.get("touch_count") or 0
        touch_score = _range_score(touch_count, TOUCH_RISK)

        # Factor 4: Score decay (older enrichment becomes stale)
        created_str = lead.get("created_at", "")
        try:
            created = datetime.fromisoformat(str(created_str)).date()
            days_old = (today - created).days
            score_decay = min(1.0, days_old / 90)  # Fully stale after 90 days
        except Exception:
            score_decay = 0.5

        # Factor 5: Competitor mention flag (from buyer_memory/sentiment)
        competitor_flag = 0.0
        try:
            from db.database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT content FROM buyer_memory
                       WHERE lead_id = ? AND memory_type = 'sentiment'
                       ORDER BY created_at DESC LIMIT 1""",
                    (lead["id"],)
                ).fetchone()
            if row:
                sentiment = json.loads(row["content"])
                if sentiment.get("competitor_mentioned"):
                    competitor_flag = 0.8
        except Exception:
            pass

        # Factor 6: Country payment risk
        country_risk_level = lead.get("country_risk", "")
        country_score = COUNTRY_RISK_WEIGHTS.get(country_risk_level, 0.2)

        # Weighted composite
        churn_risk = (
            WEIGHTS["days_since_contact"] * contact_score +
            WEIGHTS["days_since_open"]    * open_score +
            WEIGHTS["touch_count"]        * touch_score +
            WEIGHTS["score_decay"]        * score_decay +
            WEIGHTS["competitor_flag"]    * competitor_flag +
            WEIGHTS["country_risk"]       * country_score
        )
        churn_risk = round(min(1.0, churn_risk), 3)

        return {
            "lead_id": lead["id"],
            "company": lead.get("company") or lead.get("company_name", ""),
            "churn_risk": churn_risk,
            "risk_level": self._risk_label(churn_risk),
            "factors": {
                "days_since_contact": days_contact,
                "days_since_open": days_open if days_open < 999 else "never",
                "touch_count": touch_count,
                "score_decay_days": int(score_decay * 90),
                "competitor_mentioned": bool(competitor_flag),
                "country_risk": country_risk_level or "UNKNOWN",
            }
        }

    @staticmethod
    def _risk_label(score: float) -> str:
        if score >= 0.8: return "CRITICAL"
        if score >= 0.6: return "DANGER"
        if score >= 0.3: return "WARNING"
        return "SAFE"

    def _save_churn_score(self, result: dict):
        """Persist churn score to churn_scores table."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO churn_scores
                       (lead_id, score_date, churn_risk, factors)
                       VALUES (?,?,?,?)""",
                    (result["lead_id"], date.today().isoformat(),
                     result["churn_risk"], json.dumps(result["factors"]))
                )
                # Update leads.churn_risk column
                conn.execute(
                    "UPDATE leads SET churn_risk = ? WHERE id = ?",
                    (result["churn_risk"], result["lead_id"])
                )
        except Exception as e:
            logger.debug("Could not save churn score: %s", e)

    def _take_action(self, result: dict, lead: dict):
        """Trigger appropriate action based on churn risk level."""
        risk_level = result["risk_level"]
        company = result["company"]
        lead_id = result["lead_id"]
        churn_risk = result["churn_risk"]

        if risk_level == "CRITICAL":
            # Send break-up email if not already sent
            max_touch = lead.get("max_touch", 0) or 0
            if max_touch < 4:
                logger.info("CRITICAL churn: Lead %d (%s) — scheduling break-up email", lead_id, company)
                try:
                    from agents.followup_agent import FollowUpAgent
                    FollowUpAgent().run_for_lead(lead_id)
                except Exception as e:
                    logger.error("FollowUpAgent for critical churn failed: %s", e)
            else:
                # Already exhausted — mark DEAD
                from db.database import update_lead
                update_lead(lead_id, status="DEAD",
                           notes=f"Auto-closed: churn_risk={churn_risk:.2f}")
                logger.info("Lead %d marked DEAD (churn risk %.2f, all touches exhausted)", lead_id, churn_risk)

        elif risk_level == "DANGER":
            logger.warning("DANGER churn: Lead %d (%s) score=%.2f", lead_id, company, churn_risk)
            # Will be included in daily alert batch

        elif risk_level == "WARNING":
            logger.debug("WARNING churn: Lead %d (%s) score=%.2f", lead_id, company, churn_risk)

    def run(self):
        """Run daily churn analysis on all at-risk leads."""
        leads = self._get_at_risk_leads()
        logger.info("ChurnDetector: analyzing %d leads", len(leads))

        results = []
        critical = []
        danger = []

        for lead in leads:
            result = self._calculate_churn_risk(lead)
            self._save_churn_score(result)
            self._take_action(result, lead)
            results.append(result)

            if result["risk_level"] == "CRITICAL":
                critical.append(result)
            elif result["risk_level"] == "DANGER":
                danger.append(result)

        # Send daily churn summary
        self._send_churn_report(results, critical, danger)
        logger.info(
            "ChurnDetector done: analyzed=%d, critical=%d, danger=%d",
            len(results), len(critical), len(danger)
        )
        return {"analyzed": len(results), "critical": len(critical), "danger": len(danger)}

    def _send_churn_report(self, results: list, critical: list, danger: list):
        """Send daily churn summary to COO via Telegram."""
        if not results:
            return
        total = len(results)
        safe = sum(1 for r in results if r["risk_level"] == "SAFE")
        warning = sum(1 for r in results if r["risk_level"] == "WARNING")

        lines = [
            f"🧊 *Churn Risk Report — {date.today().strftime('%d %b %Y')}*\n",
            f"✅ Safe: {safe}  ⚠️ Warning: {warning}  🔴 Danger: {len(danger)}  🚨 Critical: {len(critical)}",
        ]

        if critical:
            lines.append("\n🚨 *CRITICAL (action taken):*")
            for r in critical[:5]:
                lines.append(
                    f"  • {r['company']} — risk {r['churn_risk']:.0%} "
                    f"({r['factors'].get('days_since_contact', '?')}d silent)"
                )

        if danger:
            lines.append("\n🔴 *DANGER (needs attention):*")
            for r in danger[:5]:
                lines.append(
                    f"  • {r['company']} — risk {r['churn_risk']:.0%}"
                )

        try:
            from tools.telegram_tool import send_message
            send_message("\n".join(lines))
        except Exception as e:
            logger.error("Churn report send failed: %s", e)

    def get_churning_leads(self, threshold: float = 0.6) -> list:
        """Return leads with churn_risk above threshold (for dashboard)."""
        from db.database import get_conn
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT id, company, company_name, country, status, churn_risk,
                              last_open_at, updated_at
                       FROM leads
                       WHERE churn_risk >= ? AND status NOT IN ('DEAD', 'CLOSED')
                       ORDER BY churn_risk DESC""",
                    (threshold,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_churning_leads failed: %s", e)
            return []
