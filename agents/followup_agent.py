"""Follow-up Agent — sends touch 2/3/4 emails to non-responding leads."""
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import db.database as db
import config
from tools.gmail_tool import send_email

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_TOUCH_CONFIGS = {
    2: {"file": "email_followup_2.txt", "delay_days": 3},
    3: {"file": "email_followup_3.txt", "delay_days": 5},
    4: {"file": "email_followup_4.txt", "delay_days": 7},
}


def _load_template(filename: str) -> str:
    path = _TEMPLATES_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Could not read template %s: %s", filename, exc)
        return "Following up on our previous email. Please let us know if you are interested."


def _fill_template(template: str, lead: dict) -> str:
    return template.format(
        company_name=lead.get("company_name", ""),
        contact_name=lead.get("contact_name") or "Sir/Madam",
        country=lead.get("country", ""),
        sender_name=config.SENDER_NAME,
        company=config.COMPANY_NAME,
        email=config.COMPANY_EMAIL,
    )


class FollowUpAgent:
    """Sends scheduled follow-up touches to OUTREACH leads that have not replied."""

    def run(self) -> dict:
        leads = db.get_leads_for_followup()
        logger.info("[FollowUpAgent] %d leads eligible for follow-up", len(leads))

        sent = 0
        skipped = 0

        for lead in leads:
            current_touch = lead.get("touch_number", 1)
            next_touch = current_touch + 1

            if next_touch > config.MAX_TOUCHES:
                db.update_lead(lead["id"], status="DEAD")
                logger.debug("Lead %d exceeded max touches — marked DEAD", lead["id"])
                continue

            conf = _TOUCH_CONFIGS.get(next_touch)
            if not conf:
                continue

            # Check if enough days have passed since last contact
            last_sent_str = lead.get("last_sent", "")
            if last_sent_str:
                try:
                    last_sent = datetime.fromisoformat(last_sent_str)
                    required_wait = timedelta(days=conf["delay_days"])
                    if datetime.now() - last_sent < required_wait:
                        skipped += 1
                        continue
                except ValueError:
                    pass

            if not lead.get("email"):
                skipped += 1
                continue

            template_text = _load_template(conf["file"])
            body = _fill_template(template_text, lead)
            last_outreach = db.get_last_outreach(lead["id"])
            subject = f"Following up — {config.COMPANY_NAME} charcoal export"
            if last_outreach and last_outreach.get("subject"):
                subject = f"Re: {last_outreach['subject']}"

            try:
                send_email(lead["email"], subject, body)
                db.log_outreach(
                    lead["id"],
                    touch_number=next_touch,
                    subject=subject,
                    body=body,
                    gmail_thread_id=lead.get("gmail_thread_id"),
                )
                sent += 1
                logger.info(
                    "Follow-up touch %d sent → %s (%s)",
                    next_touch, lead["email"], lead["company_name"],
                )

                if next_touch == config.MAX_TOUCHES:
                    db.update_lead(lead["id"],
                                   notes=f"Max touches ({config.MAX_TOUCHES}) reached.")
            except Exception as exc:
                logger.error("Follow-up send failed for lead %d: %s", lead["id"], exc)
                skipped += 1
                continue

            time.sleep(random.uniform(config.EMAIL_DELAY_MIN, config.EMAIL_DELAY_MAX))

        logger.info("[FollowUpAgent] Done — sent=%d, skipped=%d", sent, skipped)
        return {"sent": sent, "skipped": skipped}
