"""
agents/monitor_agent.py — Inbox Monitor Agent v3.0
PT Cahaya Woodchar International

Upgrades from v2.0:
  - SentimentAnalyzer: 5-dim analysis (urgency, price sensitivity, timeline, competitors, qty)
  - HIGH urgency (score ≥ 7) → NegotiationAgent immediately + Telegram alert
  - 4-layer reply matching fallback:
      Layer 1: gmail_thread_id exact match
      Layer 2: sender email domain match
      Layer 3: Gemini fuzzy match from subject line
      Layer 4: Telegram alert to COO for manual /match command
  - Stores successful email bodies in VectorMemory for future personalization
  - Sample feedback detection: auto-updates sample_status on receipt confirmation
"""
import json
import logging
import re

import db.database as db
from tools.llm_router import call as llm_call
from tools.gmail_tool import get_unread_replies
from tools.sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _extract_email(from_header: str) -> str:
    m = _EMAIL_RE.search(from_header)
    return m.group(0) if m else from_header


def _extract_domain(email: str) -> str:
    """Get domain from email address: user@company.com → company.com"""
    if "@" in email:
        return email.split("@", 1)[1].lower()
    return ""


class MonitorAgent:
    """Scans inbox every 2 hours, classifies replies, routes to downstream agents."""

    def __init__(self):
        self._proposal_agent = None
        self._negotiation_agent = None
        self._sentiment = SentimentAnalyzer()

    def _get_proposal_agent(self):
        if not self._proposal_agent:
            from agents.proposal_agent import ProposalAgent
            self._proposal_agent = ProposalAgent()
        return self._proposal_agent

    def _get_negotiation_agent(self):
        if not self._negotiation_agent:
            from agents.negotiation_agent import NegotiationAgent
            self._negotiation_agent = NegotiationAgent()
        return self._negotiation_agent

    # ------------------------------------------------------------------ #
    #  4-layer reply matching                                              #
    # ------------------------------------------------------------------ #

    def _match_lead_layer1_thread(self, gmail_thread_id: str):
        """Layer 1: exact gmail_thread_id match."""
        if not gmail_thread_id:
            return None
        try:
            with db.get_conn() as conn:
                row = conn.execute(
                    """SELECT l.* FROM leads l
                       JOIN outreach o ON l.id = o.lead_id
                       WHERE o.gmail_thread_id = ?
                       LIMIT 1""",
                    (gmail_thread_id,)
                ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def _match_lead_layer2_domain(self, sender_email: str):
        """Layer 2: match by sender domain against known lead emails."""
        domain = _extract_domain(sender_email)
        if not domain:
            return None
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM leads WHERE email LIKE ?",
                    (f"%@{domain}",)
                ).fetchall()
            leads = [dict(r) for r in rows]
            if len(leads) == 1:
                return leads[0]
            if len(leads) > 1:
                # Return highest-score lead from domain
                return max(leads, key=lambda l: l.get("score", 0))
        except Exception:
            return None
        return None

    def _match_lead_layer3_fuzzy(self, subject: str, body: str):
        """Layer 3: LLM fuzzy match — extract company name from email, search DB."""
        try:
            prompt = (
                f"From this email subject and body, extract the company name of the sender.\n"
                f"Subject: {subject}\nBody: {body[:500]}\n\n"
                f"Return ONLY the company name as a plain string, nothing else."
            )
            company_guess = llm_call(prompt).strip().strip('"')
            if not company_guess or len(company_guess) > 100:
                return None
            with db.get_conn() as conn:
                row = conn.execute(
                    """SELECT * FROM leads
                       WHERE company LIKE ? OR company_name LIKE ?
                       ORDER BY score DESC LIMIT 1""",
                    (f"%{company_guess}%", f"%{company_guess}%")
                ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def _match_lead_layer4_alert(self, reply: dict):
        """Layer 4: Telegram alert to COO for manual /match command."""
        try:
            from tools.telegram_tool import send_message
            send_message(
                f"❓ *Unmatched Reply Received*\n\n"
                f"From: `{reply.get('from', 'Unknown')}`\n"
                f"Subject: {reply.get('subject', 'N/A')}\n"
                f"Preview: {reply.get('body', '')[:200]}\n\n"
                f"Use `/match [outreach_id] [lead_id]` to assign manually."
            )
        except Exception:
            pass

    def _find_lead_for_reply(self, reply: dict):
        """Try all 4 layers to match a reply to a lead."""
        sender_email = _extract_email(reply.get("from", ""))

        # Layer 1: thread_id
        lead = self._match_lead_layer1_thread(reply.get("thread_id", ""))
        if lead:
            logger.debug("Match L1 (thread): %s", lead.get("company") or lead.get("company_name"))
            return lead, 1

        # Layer 2: email exact match
        lead = db.get_lead_by_email(sender_email)
        if lead:
            logger.debug("Match L2a (exact email): %s", lead.get("company") or lead.get("company_name"))
            return lead, 2

        # Layer 2b: domain match
        lead = self._match_lead_layer2_domain(sender_email)
        if lead:
            logger.debug("Match L2b (domain): %s", lead.get("company") or lead.get("company_name"))
            return lead, 2

        # Layer 3: LLM fuzzy
        lead = self._match_lead_layer3_fuzzy(reply.get("subject", ""), reply.get("body", ""))
        if lead:
            logger.debug("Match L3 (fuzzy LLM): %s", lead.get("company") or lead.get("company_name"))
            return lead, 3

        # Layer 4: alert COO
        self._match_lead_layer4_alert(reply)
        return None, 4

    # ------------------------------------------------------------------ #
    #  Main run                                                            #
    # ------------------------------------------------------------------ #

    def run(self) -> dict:
        logger.info("[MonitorAgent] Checking inbox for replies...")
        thread_ids = db.get_all_thread_ids()

        if not thread_ids:
            logger.info("[MonitorAgent] No tracked threads yet.")
            return {"classified": 0}

        replies = get_unread_replies(thread_ids)
        logger.info("[MonitorAgent] Found %d new replies", len(replies))

        classified = 0
        high_urgency_count = 0

        for reply in replies:
            try:
                lead, match_layer = self._find_lead_for_reply(reply)
                if not lead:
                    logger.debug("No lead match for reply from: %s", reply.get("from", "?"))
                    continue

                company = lead.get("company") or lead.get("company_name", "Unknown")

                # Full sentiment + classification analysis
                sentiment = self._sentiment.analyze(
                    body=reply.get("body", "")[:3000],
                    lead_country=lead.get("country", ""),
                    lead_company=company,
                    subject=reply.get("subject", ""),
                )

                cls = sentiment.get("classification", "UNRELATED")
                urgency = sentiment.get("urgency_score", 0)
                action = sentiment.get("recommended_action", "WAIT")
                qty = sentiment.get("quantity_mentioned_mt")

                # Update DB
                db.mark_replied(lead["id"])
                db.update_lead(
                    lead["id"],
                    classification=cls,
                    status="REPLIED",
                    notes=f"Reply {cls} | urgency={urgency} | action={action}"
                         + (f" | qty={qty}MT" if qty else ""),
                )

                # Save sentiment to buyer_memory
                self._sentiment.save_to_db(lead["id"], 0, sentiment)

                # Store match confidence if not layer 1
                if match_layer > 1:
                    try:
                        with db.get_conn() as conn:
                            conn.execute(
                                """UPDATE outreach SET match_confidence=?
                                   WHERE lead_id=? ORDER BY id DESC LIMIT 1""",
                                (f"LAYER_{match_layer}", lead["id"])
                            )
                    except Exception:
                        pass

                classified += 1
                logger.info(
                    "[MonitorAgent] %s → %s | urgency=%d | action=%s | match=L%d",
                    company, cls, urgency, action, match_layer
                )

                # Route based on classification
                self._route_reply(lead, sentiment, reply, urgency)

                if self._sentiment.is_high_urgency(sentiment):
                    high_urgency_count += 1

                # Check for sample feedback in reply body
                self._check_sample_feedback(lead, reply.get("body", ""))

            except Exception as exc:
                logger.error("[MonitorAgent] Error processing reply: %s", exc)

        logger.info(
            "[MonitorAgent] Done — classified=%d, high_urgency=%d",
            classified, high_urgency_count
        )
        return {"classified": classified, "high_urgency": high_urgency_count}

    # ------------------------------------------------------------------ #
    #  Routing logic                                                       #
    # ------------------------------------------------------------------ #

    def _route_reply(self, lead: dict, sentiment: dict, reply: dict, urgency: int):
        """Route reply to appropriate downstream handler."""
        cls = sentiment.get("classification", "UNRELATED")
        action = sentiment.get("recommended_action", "WAIT")
        lead_id = lead["id"]
        company = lead.get("company") or lead.get("company_name", "")

        if cls == "INTERESTED" or urgency >= 7:
            if urgency >= 7:
                # URGENT: send to NegotiationAgent AND alert COO immediately
                logger.info("🔥 HIGH URGENCY reply from %s (urgency=%d)", company, urgency)
                try:
                    self._get_negotiation_agent().run(lead_id)
                except Exception as e:
                    logger.error("NegotiationAgent (urgent) failed: %s", e)

                # Telegram alert with full analysis
                try:
                    from tools.telegram_tool import send_message
                    send_message(
                        self._sentiment.format_for_telegram(sentiment, company) +
                        "\n\n⚡ NegotiationAgent has drafted a response — check /preview"
                    )
                except Exception:
                    pass
            else:
                # Normal INTERESTED → ProposalAgent
                try:
                    self._get_proposal_agent().run(lead_id)
                except Exception as e:
                    logger.error("ProposalAgent failed: %s", e)

            # Store successful reply context in vector memory
            self._store_success_pattern(lead, reply)

        elif cls in ("NOT_INTERESTED", "UNRELATED"):
            db.update_lead(lead_id, status="DEAD")

        elif cls == "NEEDS_INFO":
            questions = sentiment.get("requested_info") or []
            if action == "NEGOTIATE_PRICE":
                # Route to NegotiationAgent for price negotiation
                try:
                    self._get_negotiation_agent().run(lead_id)
                except Exception as e:
                    logger.error("NegotiationAgent (price) failed: %s", e)
            else:
                self._handle_needs_info(lead, questions, reply)

        elif cls == "OOO":
            return_date = sentiment.get("ooo_return_date")
            if return_date:
                db.update_lead(lead_id, notes=f"OOO until {return_date}", classification="OOO")

    def _handle_needs_info(self, lead: dict, questions: list, reply: dict):
        """Auto-answer product/logistics questions using LLM + catalog."""
        import json
        from pathlib import Path
        from tools.gmail_rotator import get_gmail_rotator
        import config

        catalog_path = Path(__file__).parent.parent / "data" / "product_catalog.json"
        try:
            catalog_text = catalog_path.read_text(encoding="utf-8")
        except Exception:
            catalog_text = "Various charcoal grades, competitive pricing."

        question_text = "; ".join(questions) if questions else "general inquiry"
        prompt = (
            f"You are a sales assistant for {config.COMPANY_NAME}, an Indonesian charcoal exporter.\n"
            f"A potential buyer from {lead.get('country', 'their country')} asked:\n"
            f"{question_text}\n\n"
            f"Answer concisely and professionally using this catalog:\n{catalog_text[:3000]}\n\n"
            f"Keep under 150 words. Do not include salutation or sign-off. "
            f"If they asked about price, mention our FOB prices start from competitive rates "
            f"and suggest they request a formal quotation."
        )
        try:
            answer = llm_call(prompt)
            last = db.get_last_outreach(lead["id"])
            subject = f"Re: {last['subject']}" if last else "Re: Your Inquiry"
            rotator = get_gmail_rotator()
            rotator.send_email(lead["email"], subject, answer)
            db.log_outreach(lead["id"], touch_number=0, subject=subject, body=answer[:500])
            logger.info("NEEDS_INFO reply sent to %s", lead["email"])
        except Exception as exc:
            logger.error("NEEDS_INFO reply failed: %s", exc)

    def _store_success_pattern(self, lead: dict, reply: dict):
        """Store the email that generated this INTERESTED reply in VectorMemory."""
        try:
            last = db.get_last_outreach(lead["id"])
            if not last:
                return
            from tools.vector_memory import get_vector_memory
            vm = get_vector_memory()
            vm.store_successful_reply_template(
                outreach_id=last["id"],
                subject=last.get("subject", ""),
                body=last.get("body", ""),
                lead_country=lead.get("country", ""),
            )
            vm.upsert_lead(lead)
        except Exception as e:
            logger.debug("VectorMemory store failed: %s", e)

    def _check_sample_feedback(self, lead: dict, body: str):
        """Detect sample receipt/feedback mentions in reply body."""
        body_lower = body.lower()
        feedback_keywords = ["received the sample", "got the sample", "sample arrived",
                             "tested the charcoal", "quality is", "burn time", "ash content"]
        if any(kw in body_lower for kw in feedback_keywords):
            try:
                from tools.sample_tracker import SampleTracker
                tracker = SampleTracker()
                if lead.get("sample_status") == "SHIPPED":
                    tracker.record_feedback(
                        lead_id=lead["id"],
                        feedback=body[:300],
                    )
                    logger.info("Sample feedback detected for lead %d", lead["id"])
            except Exception as e:
                logger.debug("Sample feedback auto-detect failed: %s", e)
