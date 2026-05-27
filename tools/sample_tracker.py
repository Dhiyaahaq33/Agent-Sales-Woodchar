"""
tools/sample_tracker.py — Free Sample Lifecycle Tracker
PT Cahaya Woodchar International — PRD v2.1

Tracks 2 kg free charcoal samples from request → ship → follow-up → feedback.

States: REQUESTED → SHIPPED → DELIVERED → FEEDBACK_RECEIVED → CONVERTED / DEAD

Usage:
  from tools.sample_tracker import SampleTracker
  tracker = SampleTracker()
  tracker.request_sample(lead_id=5, product="CWI-001 Halaban Lump Wood")
  tracker.ship_sample(lead_id=5, tracking_number="JNE123456789", sent_date="2026-05-27")
  pending = tracker.get_pending_followup()   # leads where sample sent > 7 days ago, no feedback
"""

import logging
import os
from datetime import date, timedelta
from db.database import get_conn

logger = logging.getLogger(__name__)

FOLLOWUP_AFTER_DAYS = int(os.getenv("SAMPLE_FOLLOWUP_DAYS", "7"))


class SampleTracker:
    def __init__(self):
        self.enabled = os.getenv("SAMPLE_FOLLOWUP_ENABLED", "true").lower() == "true"

    # ------------------------------------------------------------------ #
    #  State transitions                                                   #
    # ------------------------------------------------------------------ #

    def request_sample(self, lead_id: int, product: str, notes: str = "") -> bool:
        """
        Mark a lead as having requested a sample.
        Updates leads.sample_requested = 1 and sample_status = REQUESTED.
        Also inserts a row in sample_log table.
        """
        try:
            with get_conn() as conn:
                conn.execute(
                    """UPDATE leads
                       SET sample_requested = 1,
                           sample_status = 'REQUESTED'
                       WHERE id = ?""",
                    (lead_id,)
                )
                conn.execute(
                    """INSERT OR IGNORE INTO sample_log
                       (lead_id, product, status, requested_at, notes)
                       VALUES (?, ?, 'REQUESTED', date('now'), ?)""",
                    (lead_id, product, notes)
                )
            logger.info(f"Sample requested: Lead {lead_id} — {product}")
            return True
        except Exception as e:
            logger.error(f"Failed to record sample request for lead {lead_id}: {e}")
            return False

    def ship_sample(self, lead_id: int, tracking_number: str,
                    sent_date: str = None, courier: str = "JNE") -> bool:
        """
        Record that sample has been physically shipped.
        Updates leads.sample_sent_date, sample_tracking, sample_status = SHIPPED.
        """
        sent_date = sent_date or date.today().isoformat()
        try:
            with get_conn() as conn:
                conn.execute(
                    """UPDATE leads
                       SET sample_sent_date = ?,
                           sample_tracking = ?,
                           sample_status = 'SHIPPED'
                       WHERE id = ?""",
                    (sent_date, tracking_number, lead_id)
                )
                conn.execute(
                    """UPDATE sample_log
                       SET status = 'SHIPPED',
                           tracking_number = ?,
                           courier = ?,
                           shipped_at = ?
                       WHERE lead_id = ? AND status = 'REQUESTED'""",
                    (tracking_number, courier, sent_date, lead_id)
                )
            logger.info(
                f"Sample shipped: Lead {lead_id} | Tracking: {tracking_number} | "
                f"Date: {sent_date}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to record shipment for lead {lead_id}: {e}")
            return False

    def mark_delivered(self, lead_id: int) -> bool:
        """Mark sample as delivered (buyer confirmed receipt)."""
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET sample_status = 'DELIVERED' WHERE id = ?",
                    (lead_id,)
                )
                conn.execute(
                    """UPDATE sample_log SET status = 'DELIVERED', delivered_at = date('now')
                       WHERE lead_id = ? AND status = 'SHIPPED'""",
                    (lead_id,)
                )
            logger.info(f"Sample delivered: Lead {lead_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark delivery for lead {lead_id}: {e}")
            return False

    def record_feedback(self, lead_id: int, feedback: str, rating: int = None) -> bool:
        """
        Record buyer feedback on the sample.
        feedback: free-text buyer comment
        rating: 1-5 optional
        """
        try:
            with get_conn() as conn:
                conn.execute(
                    """UPDATE leads
                       SET sample_feedback = ?,
                           sample_status = 'FEEDBACK_RECEIVED'
                       WHERE id = ?""",
                    (feedback, lead_id)
                )
                conn.execute(
                    """UPDATE sample_log
                       SET status = 'FEEDBACK_RECEIVED',
                           feedback = ?,
                           rating = ?,
                           feedback_at = date('now')
                       WHERE lead_id = ?""",
                    (feedback, rating, lead_id)
                )
            logger.info(f"Sample feedback recorded: Lead {lead_id} | Rating: {rating} | {feedback[:80]}")
            return True
        except Exception as e:
            logger.error(f"Failed to record feedback for lead {lead_id}: {e}")
            return False

    def mark_converted(self, lead_id: int, deal_value_usd: float = None) -> bool:
        """Mark sample lead as CONVERTED to order."""
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET sample_status = 'CONVERTED', status = 'INTERESTED' WHERE id = ?",
                    (lead_id,)
                )
                if deal_value_usd:
                    conn.execute(
                        "UPDATE leads SET estimated_deal_value_usd = ? WHERE id = ?",
                        (deal_value_usd, lead_id)
                    )
                conn.execute(
                    "UPDATE sample_log SET status = 'CONVERTED' WHERE lead_id = ?",
                    (lead_id,)
                )
            logger.info(f"Sample CONVERTED to order: Lead {lead_id} | Value: ${deal_value_usd or 'N/A'}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark conversion for lead {lead_id}: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Query helpers                                                       #
    # ------------------------------------------------------------------ #

    def get_pending_followup(self) -> list:
        """
        Returns leads where:
        - sample_status = SHIPPED
        - sample_sent_date ≥ FOLLOWUP_AFTER_DAYS days ago
        - No feedback yet received
        Used by SampleFollowup job in scheduler (15:00 daily).
        """
        cutoff = (date.today() - timedelta(days=FOLLOWUP_AFTER_DAYS)).isoformat()
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT id, company, contact_name, email, country,
                              sample_product, sample_tracking, sample_sent_date
                       FROM leads
                       WHERE sample_status = 'SHIPPED'
                         AND sample_sent_date <= ?
                         AND sample_feedback IS NULL
                       ORDER BY sample_sent_date ASC""",
                    (cutoff,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching pending sample followups: {e}")
            return []

    def get_all_active_samples(self) -> list:
        """Return all samples not yet converted or dead."""
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT id, company, contact_name, email, country,
                              sample_status, sample_sent_date, sample_tracking, sample_feedback
                       FROM leads
                       WHERE sample_requested = 1
                         AND sample_status NOT IN ('CONVERTED', 'DEAD')
                       ORDER BY sample_sent_date DESC""",
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching active samples: {e}")
            return []

    def get_stats(self) -> dict:
        """Return sample pipeline stats for Telegram /samples command."""
        try:
            with get_conn() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE sample_requested = 1"
                ).fetchone()[0]
                shipped = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE sample_status = 'SHIPPED'"
                ).fetchone()[0]
                feedback = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE sample_status = 'FEEDBACK_RECEIVED'"
                ).fetchone()[0]
                converted = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE sample_status = 'CONVERTED'"
                ).fetchone()[0]
                pending_followup = len(self.get_pending_followup())
            return {
                "total_requested": total,
                "shipped": shipped,
                "awaiting_feedback": feedback,
                "converted": converted,
                "pending_followup": pending_followup,
                "conversion_rate": f"{(converted / total * 100):.1f}%" if total > 0 else "0%"
            }
        except Exception as e:
            logger.error(f"Error getting sample stats: {e}")
            return {}

    def format_stats_for_telegram(self) -> str:
        """Format /samples Telegram response."""
        s = self.get_stats()
        if not s:
            return "⚠️ Could not fetch sample stats."
        return (
            "📦 *Sample Pipeline Stats*\n\n"
            f"📬 Total requested: {s.get('total_requested', 0)}\n"
            f"✈️ Shipped (awaiting delivery): {s.get('shipped', 0)}\n"
            f"💬 Feedback received: {s.get('awaiting_feedback', 0)}\n"
            f"✅ Converted to orders: {s.get('converted', 0)}\n"
            f"📩 Need follow-up now: {s.get('pending_followup', 0)}\n"
            f"📈 Conversion rate: {s.get('conversion_rate', '0%')}"
        )


# ------------------------------------------------------------------ #
#  Sample Follow-up Runner (called by scheduler 15:00 daily)          #
# ------------------------------------------------------------------ #

class SampleFollowupAgent:
    """
    Sends sample follow-up emails to leads that received samples
    7+ days ago but haven't given feedback yet.
    Uses templates/sample_offer.txt template.
    """

    def __init__(self):
        self.tracker = SampleTracker()
        self.enabled = os.getenv("SAMPLE_FOLLOWUP_ENABLED", "true").lower() == "true"

    def run(self):
        if not self.enabled:
            logger.info("Sample followup disabled, skipping")
            return

        leads = self.tracker.get_pending_followup()
        logger.info(f"SampleFollowupAgent: {len(leads)} leads pending follow-up")

        for lead in leads:
            try:
                self._send_followup(lead)
            except Exception as e:
                logger.error(f"Sample followup failed for lead {lead.get('id')}: {e}")

    def _send_followup(self, lead: dict):
        """Send sample follow-up email for a single lead."""
        import random
        import time
        from pathlib import Path
        from tools.gmail_rotator import get_gmail_rotator

        template_path = Path(__file__).parent.parent / "templates" / "sample_offer.txt"
        if not template_path.exists():
            logger.error("sample_offer.txt template not found")
            return

        template = template_path.read_text(encoding="utf-8")
        days_ago = (date.today() - date.fromisoformat(
            lead.get("sample_sent_date", date.today().isoformat())
        )).days

        body = template.format(
            contact_name=lead.get("contact_name") or lead.get("company", ""),
            days_ago=days_ago,
            sample_product=lead.get("sample_product") or lead.get("deal_product", "Charcoal Sample"),
            tracking_number=lead.get("sample_tracking", "N/A"),
            sent_date=lead.get("sample_sent_date", "N/A"),
        )

        subject = "Did You Receive Our Charcoal Sample?"
        rotator = get_gmail_rotator()
        success = rotator.send_email(
            to=lead["email"],
            subject=subject,
            body=body
        )

        if success:
            # Log outreach
            from db.database import log_outreach
            log_outreach(
                lead_id=lead["id"],
                touch_number=99,  # special touch number for sample followup
                subject=subject,
                body=body[:500]
            )
            logger.info(f"Sample follow-up sent: Lead {lead['id']} ({lead['email']})")
        else:
            logger.warning(f"Sample follow-up FAILED: Lead {lead['id']} ({lead['email']})")

        # Polite delay between sends
        time.sleep(random.randint(15, 45))
