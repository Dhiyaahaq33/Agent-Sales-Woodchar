"""Monitor Agent — reads Gmail inbox, classifies replies via Gemini, triggers follow-on actions."""
import json
import logging
import re

import db.database as db
from tools.gemini_tool import call_gemini
from tools.gmail_tool import get_unread_replies

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """Classify this email reply from a potential charcoal buyer.

From: {sender}
Subject: {subject}
Body:
{body}

Return ONLY valid JSON with these keys:
- "classification": one of INTERESTED / NOT_INTERESTED / NEEDS_INFO / OOO / UNRELATED
- "intent_summary": one sentence summary
- "requested_info": list of strings or null
- "ooo_return_date": ISO date string if OOO, else null"""

_ANSWER_PROMPT = """You are a sales assistant for {company}, an Indonesian charcoal exporter.
A potential buyer asked:
{question}

Answer concisely and professionally using this product catalog:
{catalog}

Keep answer under 120 words. Do not include a salutation or sign-off."""

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _extract_email(from_header: str) -> str:
    m = _EMAIL_RE.search(from_header)
    return m.group(0) if m else from_header


class MonitorAgent:
    """Scans inbox every 2 hours and dispatches INTERESTED leads for proposals."""

    def __init__(self):
        # Import here to avoid circular import; ProposalAgent is triggered directly
        from agents.proposal_agent import ProposalAgent
        self._proposal_agent = ProposalAgent()

    def run(self) -> dict:
        logger.info("[MonitorAgent] Checking inbox for replies...")
        thread_ids = db.get_all_thread_ids()

        if not thread_ids:
            logger.info("[MonitorAgent] No tracked threads yet.")
            return {"classified": 0}

        replies = get_unread_replies(thread_ids)
        logger.info("[MonitorAgent] Found %d new replies", len(replies))

        classified = 0
        for reply in replies:
            sender_email = _extract_email(reply["from"])
            lead = db.get_lead_by_email(sender_email)
            if not lead:
                logger.debug("Unknown sender %s — skipping", sender_email)
                continue

            prompt = _CLASSIFY_PROMPT.format(
                sender=reply["from"],
                subject=reply["subject"],
                body=reply["body"][:2000],
            )
            try:
                raw = call_gemini(prompt, expect_json=True)
                result = json.loads(raw)
            except Exception as exc:
                logger.error("Classification failed for %s: %s", sender_email, exc)
                continue

            cls = result.get("classification", "UNRELATED")
            db.update_lead(lead["id"], classification=cls, status="REPLIED")
            db.mark_replied(lead["id"])
            classified += 1

            logger.info(
                "Reply from %s → %s | %s",
                lead["company_name"], cls, result.get("intent_summary", ""),
            )

            if cls == "INTERESTED":
                logger.info("Triggering ProposalAgent for lead %d", lead["id"])
                try:
                    self._proposal_agent.run(lead["id"])
                except Exception as exc:
                    logger.error("ProposalAgent failed: %s", exc)

            elif cls == "NOT_INTERESTED" or cls == "UNRELATED":
                db.update_lead(lead["id"], status="DEAD")

            elif cls == "NEEDS_INFO":
                self._handle_needs_info(lead, result)

            elif cls == "OOO":
                return_date = result.get("ooo_return_date")
                if return_date:
                    db.update_lead(lead["id"],
                                   notes=f"OOO until {return_date}",
                                   classification="OOO")

        logger.info("[MonitorAgent] Done — classified=%d", classified)
        return {"classified": classified}

    def _handle_needs_info(self, lead: dict, classification_result: dict) -> None:
        """Draft and send an answer to the buyer's question using Gemini."""
        import json
        from pathlib import Path
        from tools.gmail_tool import send_email

        catalog_path = Path(__file__).parent.parent / "data" / "product_catalog.json"
        try:
            catalog_text = catalog_path.read_text()
        except Exception:
            catalog_text = "Various charcoal grades, competitive pricing."

        questions = classification_result.get("requested_info") or []
        question_text = "; ".join(questions) if questions else "general inquiry"

        prompt = _ANSWER_PROMPT.format(
            company=__import__("config").COMPANY_NAME,
            question=question_text,
            catalog=catalog_text[:3000],
        )
        try:
            answer = call_gemini(prompt)
            last = db.get_last_outreach(lead["id"])
            subject = f"Re: {last['subject']}" if last else "Re: Your Inquiry"
            send_email(lead["email"], subject, answer)
            logger.info("NEEDS_INFO reply sent to %s", lead["email"])
        except Exception as exc:
            logger.error("NEEDS_INFO reply failed: %s", exc)
