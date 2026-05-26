"""PDF quotation generator using ReportLab."""
import logging
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

import config

logger = logging.getLogger(__name__)

_GREEN = colors.HexColor("#2C5F2E")
_LIGHT = colors.HexColor("#F0F7F0")


def generate_quotation_pdf(
    lead: dict,
    product_items: list[dict],
    cover_paragraph: str,
    total_value_usd: float = None,
) -> str:
    """Build a PDF quotation and return the absolute file path."""
    config.PROPOSALS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in lead.get("company_name", "buyer") if c.isalnum() or c in "_-")
    filepath = config.PROPOSALS_DIR / f"quotation_{safe}_{ts}.pdf"

    doc = SimpleDocTemplate(
        str(filepath), pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    s_title = ParagraphStyle("qt", parent=styles["Normal"],
                              fontSize=18, fontName="Helvetica-Bold",
                              alignment=TA_CENTER, spaceAfter=2)
    s_sub   = ParagraphStyle("qs", parent=styles["Normal"],
                              fontSize=10, alignment=TA_CENTER, spaceAfter=10)
    s_sec   = ParagraphStyle("qs2", parent=styles["Normal"],
                              fontSize=11, fontName="Helvetica-Bold",
                              spaceBefore=8, spaceAfter=4)
    s_body  = ParagraphStyle("qb", parent=styles["Normal"], fontSize=10, spaceAfter=5)
    s_foot  = ParagraphStyle("qf", parent=styles["Normal"], fontSize=8,
                              textColor=colors.grey)

    date_str     = datetime.now().strftime("%d %B %Y")
    valid_str    = (datetime.now() + timedelta(days=30)).strftime("%d %B %Y")
    quote_ref    = f"QUO-{datetime.now().strftime('%Y%m%d')}-{lead.get('id', 1):04d}"

    story = []

    # ── Header ──────────────────────────────────────────────────────────
    story.append(Paragraph(config.COMPANY_NAME.upper(), s_title))
    story.append(Paragraph("Charcoal Export Division · Indonesia", s_sub))
    story.append(Paragraph(config.COMPANY_EMAIL, s_sub))
    story.append(HRFlowable(width="100%", thickness=2, color=_GREEN))
    story.append(Spacer(1, 6 * mm))

    # ── Quote metadata ───────────────────────────────────────────────────
    meta = [
        ["COMMERCIAL QUOTATION", ""],
        ["Reference :", quote_ref],
        ["Date :", date_str],
        ["Valid Until :", valid_str],
        ["Attention :", lead.get("company_name", "")],
        ["Country :", lead.get("country", "")],
    ]
    meta_table = Table(meta, colWidths=[50 * mm, 115 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME",  (0, 0), (1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",  (0, 0), (1, 0),  14),
        ("SPAN",      (0, 0), (1, 0)),
        ("FONTNAME",  (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",  (0, 1), (-1, -1), 10),
        ("VALIGN",    (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    # ── Cover ────────────────────────────────────────────────────────────
    story.append(Paragraph("Dear Sir / Madam,", s_body))
    story.append(Paragraph(cover_paragraph, s_body))
    story.append(Spacer(1, 4 * mm))

    # ── Product table ────────────────────────────────────────────────────
    story.append(Paragraph("PRODUCT SPECIFICATION & PRICING", s_sec))
    header_row = ["Product", "Grade", "MOQ (MT)", "Price USD/MT", "Packaging"]
    rows = [header_row] + [
        [
            item.get("name", ""),
            item.get("grade", ""),
            str(item.get("moq_mt", "")),
            f"USD {item.get('price_usd_per_mt', 0):,.0f}",
            item.get("packaging", ""),
        ]
        for item in product_items
    ]
    col_w = [42 * mm, 28 * mm, 25 * mm, 35 * mm, 35 * mm]
    prod_t = Table(rows, colWidths=col_w)
    prod_t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  _GREEN),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("ALIGN",        (2, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ]))
    story.append(prod_t)
    story.append(Spacer(1, 4 * mm))

    # ── Terms ────────────────────────────────────────────────────────────
    story.append(Paragraph("TERMS & CONDITIONS", s_sec))
    for term in [
        "Payment Terms: T/T — 30% deposit, 70% before shipment; or L/C at sight.",
        "Delivery Terms: FOB Indonesian Port (Tanjung Priok / Banjarmasin).",
        "Lead Time: 14–21 working days after order confirmation and deposit receipt.",
        "Inspection: SGS / Sucofindo pre-shipment inspection available on request.",
        "Quotation Validity: 30 days from the date above.",
        "Volume Discounts: Available for orders exceeding 50 MT/month.",
    ]:
        story.append(Paragraph(f"• {term}", s_body))

    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
    story.append(Spacer(1, 4 * mm))

    # ── Signature ────────────────────────────────────────────────────────
    story.append(Paragraph(
        f"Sincerely,<br/><b>{config.SENDER_NAME}</b><br/>"
        f"{config.COMPANY_NAME}<br/>{config.COMPANY_EMAIL}",
        s_body,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "To unsubscribe from our communications, reply with 'UNSUBSCRIBE' in the subject line.",
        s_foot,
    ))

    doc.build(story)
    logger.info("Quotation PDF → %s", filepath)
    return str(filepath)
