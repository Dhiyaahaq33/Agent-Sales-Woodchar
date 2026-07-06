"""Proposal Agent — generates PDF quotation and emails it to INTERESTED buyers."""
import json
import logging
from pathlib import Path

import db.database as db
import config
from tools.gemini_tool import call_gemini
from tools.gmail_tool import send_email
from tools.pdf_tool import generate_quotation_pdf

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "product_catalog.json"

_COVER_PROMPT = """Write a short (3-4 sentence) professional cover paragraph for a commercial quotation
from {company} to {buyer_company} in {country}.

Buyer context: {notes}

Personalize it to their market. Mention we look forward to a long-term partnership.
Return ONLY the paragraph text — no greeting, no sign-off."""


def _load_catalog() -> dict:
    try:
        return json.loads(_CATALOG_PATH.read_text())
    except Exception as exc:
        logger.warning("Could not load product catalog: %s", exc)
        return {
            "products": [
                {
                    "name": "Industrial Charcoal",
                    "grade": "A",
                    "moq_mt": 20,
                    "price_usd_per_mt": 350,
                    "packaging": "50kg Woven Bags",
                },
                {
                    "name": "BBQ Charcoal",
                    "grade": "Premium",
                    "moq_mt": 10,
                    "price_usd_per_mt": 480,
                    "packaging": "10kg Retail Bags / Bulk",
                },
            ]
        }


class ProposalAgent:
    """Creates and delivers PDF quotations to INTERESTED leads."""

    def run(self, lead_id: int) -> dict:
        lead = db.get_lead_by_id(lead_id)
        if not lead:
            logger.error("[ProposalAgent] Lead %d not found", lead_id)
            return {"success": False}

        logger.info("[ProposalAgent] Generating quotation for %s", lead["company_name"])

        catalog = _load_catalog()
        products = catalog.get("products", [])

        # Generate personalized cover paragraph
        prompt = _COVER_PROMPT.format(
            company=config.COMPANY_NAME,
            buyer_company=lead["company_name"],
            country=lead.get("country", "their country"),
            notes=lead.get("notes", "potential charcoal buyer"),
        )
        try:
            cover = call_gemini(prompt)
        except Exception as exc:
            logger.warning("Gemini cover failed, using default: %s", exc)
            cover = (
                f"Thank you for your interest in {config.COMPANY_NAME}'s charcoal products. "
                "We are pleased to present this commercial quotation tailored to your requirements. "
                "We look forward to establishing a long-term and mutually beneficial partnership."
            )

        # Estimate deal value (mid-tier product × MOQ)
        try:
            mid = products[len(products) // 2]
            total_value = mid.get("moq_mt", 20) * mid.get("price_usd_per_mt", 400)
        except (IndexError, TypeError):
            total_value = None

        # Generate PDF
        try:
            pdf_path = generate_quotation_pdf(
                lead=lead,
                product_items=products,
                cover_paragraph=cover,
                total_value_usd=total_value,
            )
        except Exception as exc:
            logger.error("[ProposalAgent] PDF generation failed: %s", exc)
            return {"success": False}

        # Log proposal in DB
        proposal_id = db.log_proposal(
            lead_id=lead_id,
            pdf_path=pdf_path,
            total_value_usd=total_value,
            product_specs=json.dumps(products),
        )

        # Email proposal to buyer
        if lead.get("email"):
            last = db.get_last_outreach(lead_id)
            subject = (
                f"Commercial Quotation — {config.COMPANY_NAME} Charcoal Export"
                if not last else f"Re: {last['subject']}"
            )
            calendly_line = (
                f"\n\nWould you like to schedule a call to discuss the details? "
                f"Book a time here: {config.CALENDLY_URL}"
                if getattr(config, "CALENDLY_URL", "") else ""
            )
            body = (
                f"Dear {lead.get('contact_name') or 'Sir/Madam'},\n\n"
                f"{cover}\n\n"
                "Please find our commercial quotation attached. "
                f"Do not hesitate to reach out with any questions.{calendly_line}\n\n"
                f"Best regards,\n{config.SENDER_NAME}\n{config.COMPANY_NAME}"
            )
            try:
                send_email(lead["email"], subject, body, attachment_path=pdf_path)
                db.mark_proposal_sent(proposal_id)
                db.update_lead(lead_id, status="QUALIFIED")
                logger.info("[ProposalAgent] Quotation emailed to %s", lead["email"])
            except Exception as exc:
                logger.error("[ProposalAgent] Email send failed: %s", exc)
        else:
            logger.warning("[ProposalAgent] Lead %d has no email — PDF saved but not sent", lead_id)

        return {"success": True, "pdf_path": pdf_path, "proposal_id": proposal_id}
