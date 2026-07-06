"""Negotiation Agent v2 — handles initial negotiation with INTERESTED buyers.

⚠️  SAFETY GATE: Tidak pernah kirim email langsung.
Selalu minta approval COO via Telegram (/approve [id] atau /reject [id]).
"""
import json
import logging
from pathlib import Path

import config
import db.database as db
from db.database import get_conn
from tools.llm_router import call as llm_call
from tools.telegram_tool import send_approval_request

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(config.DATA_DIR) / "product_catalog.json"
_PLAYBOOK_PATH = Path(__file__).parent.parent / "templates" / "objection_playbook.md"


def _load_playbook() -> str:
    try:
        return _PLAYBOOK_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


_OBJECTION_PLAYBOOK = _load_playbook()

_NEGOTIATION_PROMPT = """You are a professional export sales negotiator for {company},
an Indonesian charcoal exporter with 8+ years experience.

A buyer ({buyer_company} from {country}) who is INTERESTED has asked:
{buyer_question}

Product catalog available:
{catalog_summary}

Company policies:
- Payment: T/T 30% deposit / L/C at sight (no full advance)
- MOQ: Per product specification (never waive without COO approval)
- Sample: Free up to 2kg for qualified buyers
- Discount: Max 5% for orders >50 MT (never reveal this upfront)
- Lead time: 14-21 days after PO + deposit

Objection handling reference (apply LAARC: Listen→Acknowledge→Ask→Respond→Confirm):
{objection_playbook}

Instructions:
- Answer professionally and warmly
- Provide specific information from catalog
- Protect pricing — give range, not exact (unless buyer gave budget)
- Mention free sample offer if appropriate
- Apply LAARC if buyer raises any objection (price, MOQ, quality, trust)
- End with a clear next step (e.g., send PI, schedule call, confirm spec sheet)
- MAX 150 words
- Do NOT offer discounts unless buyer explicitly requests

Return JSON: {{"subject": "Re: [their subject]", "body": "..."}}"""


def _load_catalog_summary() -> str:
    try:
        data = json.loads(_CATALOG_PATH.read_text())
        products = data.get("products", [])
        lines = []
        for p in products:
            price = p.get("price_usd_per_mt", "TBD")
            moq = p.get("moq_mt", "TBD")
            lines.append(
                f"- {p['name']}: FC {p.get('fixed_carbon_pct','')}%, "
                f"Price {price} USD/MT, MOQ {moq} MT"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Catalog load failed: %s", exc)
        return "Multiple charcoal grades available — Halaban, Mix Hardwood, Briquette, Sawdust, Coconut Shell."


def _save_negotiation_draft(lead_id: int, subject: str, draft_body: str) -> int:
    """Save draft to negotiation_log and return id."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO negotiation_log (lead_id, subject, draft_body, status)
               VALUES (?,?,?,'PENDING')""",
            (lead_id, subject, draft_body),
        )
        return cur.lastrowid


class NegotiationAgent:
    """Triggered by MonitorAgent when INTERESTED buyer asks price/MOQ/terms questions."""

    def run(self, lead_id: int, buyer_question: str, original_subject: str = "") -> dict:
        """Generate negotiation draft and request COO approval.

        Args:
            lead_id: DB id of the INTERESTED lead.
            buyer_question: The question/content from the buyer's reply.
            original_subject: Original email subject for Re: line.

        Returns:
            dict with status and negotiation_id.
        """
        lead = db.get_lead_by_id(lead_id)
        if not lead:
            logger.error("[NegotiationAgent] Lead %d not found", lead_id)
            return {"status": "error", "message": "Lead not found"}

        logger.info("[NegotiationAgent] Drafting response for %s", lead["company_name"])

        catalog = _load_catalog_summary()
        prompt = _NEGOTIATION_PROMPT.format(
            company=config.COMPANY_NAME,
            buyer_company=lead["company_name"],
            country=lead.get("country", "their country"),
            buyer_question=buyer_question[:1000],
            catalog_summary=catalog,
            objection_playbook=_OBJECTION_PLAYBOOK[:1200],
        )

        try:
            raw = llm_call(prompt, expect_json=True)
            data = json.loads(raw)
            subject = data.get("subject", f"Re: {original_subject}")
            draft_body = data.get("body", "")
        except Exception as exc:
            logger.error("[NegotiationAgent] LLM failed: %s", exc)
            return {"status": "error", "message": str(exc)}

        # SAFETY GATE: Save draft and request approval
        if config.NEGOTIATION_AUTO_APPROVE:
            # Only if explicitly enabled (default: False)
            logger.warning("Auto-approve enabled — sending without COO review!")
            try:
                from tools.gmail_tool import send_email
                send_email(lead["email"], subject, draft_body)
                nid = _save_negotiation_draft(lead_id, subject, draft_body)
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE negotiation_log SET status='APPROVED', approved_at=datetime('now') WHERE id=?",
                        (nid,)
                    )
                return {"status": "auto_sent", "negotiation_id": nid}
            except Exception as exc:
                logger.error("Auto-send failed: %s", exc)
                return {"status": "error", "message": str(exc)}
        else:
            nid = _save_negotiation_draft(lead_id, subject, draft_body)
            send_approval_request(nid, lead["company_name"], draft_body)
            logger.info(
                "[NegotiationAgent] Draft saved (id=%d), waiting for COO approval.", nid
            )
            return {"status": "pending_approval", "negotiation_id": nid}
