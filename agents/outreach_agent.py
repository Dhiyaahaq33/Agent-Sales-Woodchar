"""Outreach Agent — generates personalized cold emails via Gemini and sends via Gmail."""
import json
import logging
import random
import time
from pathlib import Path

import db.database as db
import config
from tools.gemini_tool import call_gemini
from tools.gmail_tool import send_email

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "product_catalog.json"


def _load_catalog_summary() -> str:
    try:
        catalog = json.loads(_CATALOG_PATH.read_text())
        items = catalog.get("products", [])
        lines = [
            f"- {p['name']}: grade {p.get('grade','')}, "
            f"MOQ {p.get('moq_mt','')} MT, "
            f"USD {p.get('price_usd_per_mt','')} /MT"
            for p in items
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Could not load product catalog: %s", exc)
        return "Industrial & BBQ charcoal, various grades, MOQ 20 MT, competitive pricing."


_PROMPT_TEMPLATE = """You are a professional B2B sales writer for {company}, an Indonesian charcoal exporter.
Write a cold outreach email to {buyer_company} in {country}.

Buyer profile: {profile}
Our products:
{catalog}

Rules:
- Maximum 150 words
- Professional but warm tone
- Mention their specific market or use-case
- One clear CTA: reply to discuss pricing or request a sample
- No attachments in first email
- Subject line under 50 characters

Return ONLY valid JSON with keys "subject" and "body". No markdown, no explanation."""


class OutreachAgent:
    """Reads ENRICHED leads and sends personalized first-touch emails."""

    def run(self) -> dict:
        sent_today = db.count_sent_today()
        remaining = config.MAX_EMAILS_PER_DAY - sent_today
        if remaining <= 0:
            logger.info("[OutreachAgent] Daily email limit reached (%d). Skipping.",
                        config.MAX_EMAILS_PER_DAY)
            return {"sent": 0, "failed": 0, "limit_reached": True}

        leads = db.get_leads_by_status("ENRICHED")
        qualified = [l for l in leads if l.get("score", 0) >= config.LEAD_SCORE_THRESHOLD]
        logger.info("[OutreachAgent] %d qualified leads, %d slots left today",
                    len(qualified), remaining)

        catalog = _load_catalog_summary()
        sent = 0
        failed = 0

        for lead in qualified[:remaining]:
            if not lead.get("email"):
                logger.debug("Lead %d has no email — skipping", lead["id"])
                failed += 1
                continue

            profile = (
                f"country={lead.get('country','unknown')}, "
                f"industry={lead.get('industry','')}, "
                f"score={lead.get('score',0)}, "
                f"notes={lead.get('notes','')}"
            )
            prompt = _PROMPT_TEMPLATE.format(
                company=config.COMPANY_NAME,
                buyer_company=lead["company_name"],
                country=lead.get("country", "their country"),
                profile=profile,
                catalog=catalog,
            )

            try:
                raw = call_gemini(prompt, expect_json=True)
                data = json.loads(raw)
                subject = data["subject"]
                body = data["body"]
            except Exception as exc:
                logger.error("Gemini failed for lead %d: %s", lead["id"], exc)
                failed += 1
                continue

            try:
                send_email(lead["email"], subject, body)
                db.log_outreach(lead["id"], touch_number=1, subject=subject, body=body)
                db.update_lead(lead["id"], status="OUTREACH")
                sent += 1
                logger.info("Cold email sent → %s (%s)", lead["email"], lead["company_name"])
            except Exception as exc:
                logger.error("Send failed for lead %d: %s", lead["id"], exc)
                failed += 1
                continue

            # Random delay to avoid spam filters
            time.sleep(random.uniform(config.EMAIL_DELAY_MIN, config.EMAIL_DELAY_MAX))

        logger.info("[OutreachAgent] Done — sent=%d, failed=%d", sent, failed)
        return {"sent": sent, "failed": failed}
