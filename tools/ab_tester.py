"""A/B Testing Engine v2 — otomatis test email variants dan pilih pemenang.

20% leads secara random mendapat variant B.
Setelah 7 hari, reply rate dibandingkan.
Jika B unggul dengan confidence >80%, B otomatis jadi template baru.
"""
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import config
from db.database import get_conn

logger = logging.getLogger(__name__)

_DEFAULT_VARIANTS = {
    "subject": {
        "A": "Premium Indonesian Charcoal for Your Business",
        "B": "Quick question about your charcoal supply",
    },
    "opening": {
        "A": "I am reaching out regarding our premium charcoal export offerings from Indonesia.",
        "B": "Noticed you import charcoal — we export 200+ MT/month from Indonesia.",
    },
    "cta": {
        "A": "Reply to discuss pricing",
        "B": "Can I send you our spec sheet?",
    },
    "length": {
        "A": "150 words",
        "B": "80 words — concise",
    },
}


def assign_variant(lead_id: int) -> str:
    """Assign A or B variant to a lead based on AB_TEST_PERCENTAGE config.

    Returns 'A' or 'B'. Updates leads.ab_variant in DB.
    """
    variant = "B" if random.randint(1, 100) <= config.AB_TEST_PERCENTAGE else "A"
    with get_conn() as conn:
        conn.execute("UPDATE leads SET ab_variant=? WHERE id=?", (variant, lead_id))
    return variant


def get_variant_for_lead(lead_id: int) -> str:
    """Return existing variant assignment for a lead, or assign one."""
    with get_conn() as conn:
        row = conn.execute("SELECT ab_variant FROM leads WHERE id=?", (lead_id,)).fetchone()
        if row and row["ab_variant"]:
            return row["ab_variant"]
    return assign_variant(lead_id)


def get_active_test() -> Optional[dict]:
    """Return currently active A/B test record, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ab_tests WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def create_test(test_name: str, variant_a: str, variant_b: str) -> int:
    """Create a new A/B test. Returns test id."""
    with get_conn() as conn:
        # Close any existing active test
        conn.execute("UPDATE ab_tests SET status='COMPLETED', ended_at=datetime('now') WHERE status='ACTIVE'")
        cur = conn.execute(
            """INSERT INTO ab_tests (test_name, variant_a_desc, variant_b_desc)
               VALUES (?,?,?)""",
            (test_name, variant_a, variant_b),
        )
        test_id = cur.lastrowid
    logger.info("A/B test created: %s (id=%d)", test_name, test_id)
    return test_id


def update_test_stats(test_id: int) -> dict:
    """Recalculate variant stats from outreach/leads tables."""
    with get_conn() as conn:
        a_sent = conn.execute(
            "SELECT COUNT(*) FROM leads l JOIN outreach o ON l.id=o.lead_id WHERE l.ab_variant='A' AND o.touch_number=1"
        ).fetchone()[0]
        b_sent = conn.execute(
            "SELECT COUNT(*) FROM leads l JOIN outreach o ON l.id=o.lead_id WHERE l.ab_variant='B' AND o.touch_number=1"
        ).fetchone()[0]
        a_replies = conn.execute(
            "SELECT COUNT(*) FROM leads l JOIN outreach o ON l.id=o.lead_id WHERE l.ab_variant='A' AND o.replied=1"
        ).fetchone()[0]
        b_replies = conn.execute(
            "SELECT COUNT(*) FROM leads l JOIN outreach o ON l.id=o.lead_id WHERE l.ab_variant='B' AND o.replied=1"
        ).fetchone()[0]
        conn.execute(
            """UPDATE ab_tests SET
               variant_a_sent=?, variant_b_sent=?,
               variant_a_replies=?, variant_b_replies=?
               WHERE id=?""",
            (a_sent, b_sent, a_replies, b_replies, test_id),
        )
    return {"a_sent": a_sent, "b_sent": b_sent, "a_replies": a_replies, "b_replies": b_replies}


def evaluate_and_conclude(test_id: int) -> dict:
    """Evaluate test after 7 days. Declare winner if confidence ≥ 80%.

    Returns dict with winner, confidence, and action taken.
    """
    stats = update_test_stats(test_id)
    a_sent, b_sent = stats["a_sent"], stats["b_sent"]
    a_rep, b_rep   = stats["a_replies"], stats["b_replies"]

    if a_sent < 20 or b_sent < 20:
        logger.info("A/B test %d: insufficient data (A=%d, B=%d sent)", test_id, a_sent, b_sent)
        return {"winner": None, "confidence": 0, "action": "insufficient_data"}

    a_rate = a_rep / a_sent if a_sent else 0
    b_rate = b_rep / b_sent if b_sent else 0

    # Simple confidence: |difference| / average_rate as proportion
    avg_rate = (a_rate + b_rate) / 2
    if avg_rate == 0:
        confidence = 0.0
    else:
        confidence = abs(b_rate - a_rate) / avg_rate * 100

    winner = None
    action = "no_change"

    if confidence >= 80:
        winner = "B" if b_rate > a_rate else "A"
        action = f"winner_{winner}_declared"
        logger.info(
            "A/B test %d: WINNER=%s (A=%.1f%% vs B=%.1f%%, confidence=%.0f%%)",
            test_id, winner, a_rate*100, b_rate*100, confidence,
        )

    with get_conn() as conn:
        conn.execute(
            """UPDATE ab_tests SET winner=?, confidence_pct=?, status='COMPLETED', ended_at=datetime('now')
               WHERE id=?""",
            (winner, confidence, test_id),
        )

    return {"winner": winner, "confidence": confidence, "action": action,
            "a_rate": round(a_rate*100, 2), "b_rate": round(b_rate*100, 2)}


def run_weekly_evaluation() -> dict:
    """Called by Reporter Agent — evaluate any test older than 7 days."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM ab_tests WHERE status='ACTIVE'
               AND started_at <= datetime('now', '-7 days')
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        logger.info("No A/B tests ready for evaluation.")
        return {}
    return evaluate_and_conclude(row["id"])
