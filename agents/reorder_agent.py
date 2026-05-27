"""
agents/reorder_agent.py — Post-Deal Reorder Sequence Agent
PT Cahaya Woodchar International — PRD v2.1

After a deal is closed (status=CLOSED / deal_closed_date set), sends
automated follow-up emails at 30, 60, and 90 days to re-engage buyers.

Reorder sequence:
  Day 30: "How is the charcoal performing? Ready for your next order?"
  Day 60: "Restock reminder — our availability is confirmed"
  Day 90: "Exclusive returning customer offer"

Usage:
  from agents.reorder_agent import ReorderAgent
  agent = ReorderAgent()
  agent.run()
"""

import logging
import os
from datetime import date, timedelta
from db.database import get_conn, log_outreach
from tools.llm_router import call as llm_call
from tools.gmail_rotator import get_gmail_rotator

logger = logging.getLogger(__name__)

REORDER_TOUCHPOINTS = [
    {
        "day": 30,
        "touch_id": "reorder_30",
        "subject": "How is the charcoal performing? 🔥",
        "prompt_hint": "30-day check-in after first order, ask about quality satisfaction, invite reorder"
    },
    {
        "day": 60,
        "touch_id": "reorder_60",
        "subject": "Restock Reminder — PT Cahaya Woodchar Stock Confirmed",
        "prompt_hint": "60-day restock reminder, mention confirmed stock availability, easy reorder process"
    },
    {
        "day": 90,
        "touch_id": "reorder_90",
        "subject": "Special Offer for Our Returning Customers 🎁",
        "prompt_hint": "90-day exclusive returning customer offer, slight discount hint, loyalty appreciation"
    },
]

COMPANY_SIGNATURE = """
────────────────

🌐 https://indonesiancharcoalsupplier.com
📱 WhatsApp: +62 895 0481 5988 | +62 812 2564 6585 | +62 812 2464 6585
📧 johanneshaq@indonesiancharcoalsupplier.com

────────────────

Warm regards,
Muhammad Yahya
Indonesian Charcoal Supplier | PT Cahaya Woodchar International
Lampung, Indonesia
"""


class ReorderAgent:
    def __init__(self):
        self.enabled = os.getenv("REORDER_SEQUENCE_ENABLED", "true").lower() == "true"

    def _get_reorder_leads(self) -> list:
        """
        Find closed deals where reorder touchpoints haven't been sent yet.
        Returns leads with deal_closed_date set and their pending touchpoints.
        """
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT id, company, contact_name, email, country,
                              deal_closed_date, deal_product, deal_value_usd,
                              reorder_touch_30_sent, reorder_touch_60_sent, reorder_touch_90_sent
                       FROM leads
                       WHERE deal_closed_date IS NOT NULL
                         AND status IN ('CLOSED', 'INTERESTED')
                         AND email IS NOT NULL AND email != ''
                       ORDER BY deal_closed_date ASC"""
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            # Fallback if reorder columns don't exist yet (pre-migration)
            logger.warning(f"Reorder query failed (try running migrate_v21.py): {e}")
            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        """SELECT id, company, contact_name, email, country,
                                  deal_closed_date, deal_product, deal_value_usd
                           FROM leads
                           WHERE deal_closed_date IS NOT NULL
                             AND status IN ('CLOSED', 'INTERESTED')
                             AND email IS NOT NULL AND email != ''"""
                    ).fetchall()
                return [dict(r) for r in rows]
            except Exception as e2:
                logger.error(f"Reorder lead query failed: {e2}")
                return []

    def _get_days_since_close(self, lead: dict) -> int:
        """Days since deal was closed."""
        try:
            closed = date.fromisoformat(lead["deal_closed_date"])
            return (date.today() - closed).days
        except Exception:
            return 0

    def _is_touch_sent(self, lead: dict, touch_id: str) -> bool:
        """Check if a specific reorder touchpoint has already been sent."""
        col = f"{touch_id}_sent"
        return bool(lead.get(col, False))

    def _mark_touch_sent(self, lead_id: int, touch_id: str):
        """Mark a reorder touchpoint as sent in DB."""
        col = f"{touch_id}_sent"
        try:
            with get_conn() as conn:
                conn.execute(
                    f"UPDATE leads SET {col} = 1 WHERE id = ?",
                    (lead_id,)
                )
        except Exception as e:
            logger.warning(f"Could not mark {touch_id} sent for lead {lead_id}: {e}")

    def _generate_email_body(self, lead: dict, touchpoint: dict) -> str:
        """Use LLM to generate personalized reorder email."""
        contact = lead.get("contact_name") or lead.get("company", "there")
        product = lead.get("deal_product") or "charcoal"
        country = lead.get("country", "your market")
        days = self._get_days_since_close(lead)

        prompt = f"""
You are Muhammad Yahya, COO of PT Cahaya Woodchar International, writing a reorder follow-up email.

Context:
- Buyer: {contact} from {lead.get('company', 'their company')} ({country})
- Product ordered: {product}
- Days since their order: {days}
- Email purpose: {touchpoint['prompt_hint']}

Write a SHORT, warm, professional email body (3-4 short paragraphs max).
- Start with: "Dear {contact},"
- Reference their previous order of {product}
- Keep it natural and NOT pushy
- Mention WhatsApp as an easy way to place reorder
- End before the signature (signature is added separately)

Output only the email body text, no subject line, no signature.
"""
        try:
            body = llm_call(prompt)
        except Exception as e:
            logger.warning(f"LLM reorder email generation failed: {e}")
            body = (
                f"Dear {contact},\n\n"
                f"I hope you have been enjoying your {product} from PT Cahaya Woodchar International. "
                f"It has been {days} days since your order and I wanted to check in.\n\n"
                f"If you are ready for your next order, please don't hesitate to reach out "
                f"on WhatsApp or reply to this email. We always have stock ready for our valued customers.\n\n"
            )

        return body.strip() + "\n" + COMPANY_SIGNATURE

    def _send_reorder_email(self, lead: dict, touchpoint: dict) -> bool:
        """Send reorder email and log to DB."""
        import time, random
        body = self._generate_email_body(lead, touchpoint)
        rotator = get_gmail_rotator()
        success = rotator.send_email(
            to=lead["email"],
            subject=touchpoint["subject"],
            body=body
        )
        if success:
            log_outreach(
                lead_id=lead["id"],
                touch_number=int(touchpoint["touch_id"].split("_")[1]),  # 30, 60, or 90
                subject=touchpoint["subject"],
                body=body[:500]
            )
            self._mark_touch_sent(lead["id"], touchpoint["touch_id"])
            logger.info(
                f"Reorder {touchpoint['touch_id']} sent: Lead {lead['id']} "
                f"({lead['email']})"
            )
        else:
            logger.warning(
                f"Reorder {touchpoint['touch_id']} FAILED: Lead {lead['id']}"
            )
        time.sleep(random.randint(20, 60))
        return success

    def run(self):
        """Check all closed deals and send pending reorder touchpoints."""
        if not self.enabled:
            logger.info("Reorder sequence disabled, skipping")
            return

        leads = self._get_reorder_leads()
        logger.info(f"ReorderAgent: checking {len(leads)} closed deals for reorder touches")

        sent_count = 0
        for lead in leads:
            days = self._get_days_since_close(lead)

            for touchpoint in REORDER_TOUCHPOINTS:
                # Check if this touchpoint is due (reached the day threshold)
                if days < touchpoint["day"]:
                    continue
                # Check if already sent
                if self._is_touch_sent(lead, touchpoint["touch_id"]):
                    continue
                # Don't send if email is empty
                if not lead.get("email"):
                    continue

                logger.info(
                    f"Sending {touchpoint['touch_id']} to Lead {lead['id']} "
                    f"({lead['company']}) — Day {days} since close"
                )
                success = self._send_reorder_email(lead, touchpoint)
                if success:
                    sent_count += 1

        logger.info(f"ReorderAgent: {sent_count} reorder emails sent")

        if sent_count > 0:
            try:
                from tools.telegram_tool import send_message
                send_message(f"♻️ Reorder Agent: {sent_count} reorder follow-up(s) sent today")
            except Exception:
                pass
