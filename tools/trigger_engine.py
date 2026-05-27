"""Trigger Engine v2 — seasonal & event-based outreach triggers.

Dijalankan setiap hari pukul 06:30 (sebelum Scraper Agent).
Mengidentifikasi trigger aktif dan menyimpan ke tabel 'triggers'.
"""
import json
import logging
from datetime import date, datetime, timedelta

import requests

import config
from db.database import get_conn

logger = logging.getLogger(__name__)

# ── Static seasonal trigger definitions ──────────────────────────────────────

_SEASONAL_TRIGGERS = [
    {
        "name": "Ramadan",
        "type": "seasonal",
        "countries": ["UAE", "Saudi Arabia", "Qatar", "Kuwait", "Egypt", "Jordan"],
        "product_focus": ["Coconut Shell Charcoal", "Halaban Lump Wood Charcoal"],
        "tone": "warm, relationship-first, religiously aware",
        "days_before": 30,
    },
    {
        "name": "BBQ Season Europe",
        "type": "seasonal",
        "countries": ["Germany", "Netherlands", "France", "UK", "Belgium"],
        "months": [3, 4, 5, 6, 7, 8, 9],
        "product_focus": ["BBQ Briquette Charcoal", "Halaban Lump Wood Charcoal"],
        "tone": "casual, outdoor BBQ culture",
    },
    {
        "name": "BBQ Season Australia",
        "type": "seasonal",
        "countries": ["Australia", "New Zealand"],
        "months": [10, 11, 12, 1, 2, 3],
        "product_focus": ["BBQ Briquette Charcoal", "Halaban Lump Wood Charcoal"],
        "tone": "casual, friendly, practical",
    },
    {
        "name": "Winter Heating Europe",
        "type": "seasonal",
        "countries": ["Germany", "Netherlands", "Poland", "Sweden", "Norway"],
        "months": [10, 11, 12, 1, 2],
        "product_focus": ["Sawdust Charcoal", "Mix Hardwood Charcoal"],
        "tone": "practical, warmth-focused",
    },
    {
        "name": "Winter Japan/Korea",
        "type": "seasonal",
        "countries": ["Japan", "South Korea"],
        "months": [11, 12, 1, 2],
        "product_focus": ["Sawdust Charcoal", "Coconut Shell Charcoal"],
        "tone": "formal, quality-focused, indoor cooking",
    },
]

# Trade shows to scrape / track
_TRADE_SHOWS = [
    {"name": "Gulfood Dubai",      "url": "https://www.gulfood.com",  "countries": ["UAE"]},
    {"name": "SIAL Paris",         "url": "https://www.sialparis.com","countries": ["France", "Europe"]},
    {"name": "FOODEX Japan",       "url": "https://www.jma.or.jp/foodex", "countries": ["Japan"]},
    {"name": "Seoul Food & Hotel", "url": "https://www.seoulfood.or.kr",  "countries": ["South Korea"]},
]


def _get_active_seasonal() -> list[dict]:
    """Return seasonal triggers active in the current month."""
    month = datetime.now().month
    active = []
    for t in _SEASONAL_TRIGGERS:
        if "months" in t and month in t["months"]:
            active.append(t)
    return active


def _estimate_ramadan_trigger() -> dict | None:
    """Rough Ramadan start estimate (Islamic calendar cycles ~354 days).
    Returns trigger info if within 30 days of Ramadan start, else None.
    """
    # Approximate Ramadan 2026 start: March 18, 2026
    # (update annually or use hijri library for precision)
    ramadan_estimates = [
        date(2026, 3, 18),
        date(2027, 3, 8),
        date(2028, 2, 25),
    ]
    today = date.today()
    for ramadan_start in ramadan_estimates:
        days_until = (ramadan_start - today).days
        if 0 <= days_until <= 30:
            return {
                "name": "Ramadan Pre-Season",
                "type": "seasonal",
                "countries": ["UAE", "Saudi Arabia", "Qatar", "Kuwait"],
                "days_until": days_until,
                "product_focus": ["Coconut Shell Charcoal"],
                "message_hint": f"Ramadan starts in {days_until} days — prioritize shisha charcoal buyers",
            }
    return None


def _scrape_trade_show_schedule() -> list[dict]:
    """Attempt to detect upcoming trade shows via RSS/web scraping."""
    upcoming = []
    # Hardcoded upcoming shows (fallback if scraping fails)
    # In production: scrape actual dates from show websites
    known_shows = [
        {"name": "Gulfood Dubai",      "date": date(2027, 2, 17), "countries": ["UAE"]},
        {"name": "FOODEX Japan",       "date": date(2027, 3,  2), "countries": ["Japan"]},
        {"name": "SIAL Paris",         "date": date(2026, 10, 19), "countries": ["France"]},
        {"name": "Seoul Food & Hotel", "date": date(2026, 5,  12), "countries": ["South Korea"]},
    ]
    today = date.today()
    for show in known_shows:
        days_until = (show["date"] - today).days
        if 0 <= days_until <= 21:
            upcoming.append({**show, "days_until": days_until})
            logger.info("Trade show trigger active: %s in %d days", show["name"], days_until)
    return upcoming


def _upsert_trigger(conn, name: str, trigger_type: str,
                    countries: list, notes: str, active: bool = True):
    """Insert or update a trigger record."""
    existing = conn.execute(
        "SELECT id FROM triggers WHERE trigger_name=?", (name,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE triggers SET is_active=?, target_countries=?, notes=?
               WHERE trigger_name=?""",
            (1 if active else 0, json.dumps(countries), notes, name),
        )
    else:
        conn.execute(
            """INSERT INTO triggers (trigger_name, trigger_type, target_countries, is_active, notes)
               VALUES (?,?,?,?,?)""",
            (name, trigger_type, json.dumps(countries), 1 if active else 0, notes),
        )


def get_active_triggers() -> list[dict]:
    """Return currently active triggers from database."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM triggers WHERE is_active=1"
        ).fetchall()
        return [dict(r) for r in rows]


def run() -> dict:
    """Main entry point — evaluate and persist triggers."""
    logger.info("[TriggerEngine] Evaluating triggers for %s...", date.today())

    active_seasonal = _get_active_seasonal()
    ramadan = _estimate_ramadan_trigger()
    trade_shows = _scrape_trade_show_schedule()

    summary = {"seasonal": len(active_seasonal), "ramadan": bool(ramadan),
               "trade_shows": len(trade_shows)}

    with get_conn() as conn:
        # Reset all triggers to inactive first
        conn.execute("UPDATE triggers SET is_active=0")

        for t in active_seasonal:
            _upsert_trigger(
                conn, t["name"], "seasonal",
                t.get("countries", []),
                f"Products: {', '.join(t.get('product_focus', []))} | Tone: {t.get('tone','')}",
                active=True,
            )

        if ramadan:
            _upsert_trigger(
                conn, ramadan["name"], "seasonal",
                ramadan["countries"],
                ramadan["message_hint"],
                active=True,
            )

        for show in trade_shows:
            _upsert_trigger(
                conn, show["name"], "trade_show",
                show["countries"],
                f"Event in {show['days_until']} days — blast to buyers in {show['countries']}",
                active=True,
            )

    logger.info("[TriggerEngine] Done: %s", summary)
    return summary
