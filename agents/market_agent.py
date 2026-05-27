"""Market Intelligence Agent v2 — daily briefing setiap pukul 06:30.

Mengumpulkan:
- Kurs USD/IDR
- Google Trends demand signals per negara target
- Trade show calendar
- Kompetitor pricing dari Alibaba
- Seasonal triggers aktif
"""
import json
import logging
from datetime import date, datetime

import requests

import config
import db.database as db
from db.database import get_conn
from tools.telegram_tool import send_message
from tools.trigger_engine import run as run_triggers, get_active_triggers

logger = logging.getLogger(__name__)

_TARGET_COUNTRIES = [
    "UAE", "Saudi Arabia", "Japan", "South Korea",
    "Australia", "Germany", "Netherlands", "India", "Qatar",
]

_CHARCOAL_KEYWORDS = [
    "charcoal import", "BBQ charcoal supplier", "shisha charcoal",
    "wood charcoal export", "charcoal wholesale",
]


def _get_usd_idr() -> float | None:
    """Fetch live USD/IDR rate. Free via exchangerate-api.com."""
    try:
        if config.EXCHANGERATE_API_KEY:
            url = f"https://v6.exchangerate-api.com/v6/{config.EXCHANGERATE_API_KEY}/pair/USD/IDR"
            resp = requests.get(url, timeout=10)
            data = resp.json()
            return data.get("conversion_rate")
        else:
            # Fallback: free endpoint (limited)
            resp = requests.get(
                "https://open.er-api.com/v6/latest/USD", timeout=10
            )
            return resp.json().get("rates", {}).get("IDR")
    except Exception as exc:
        logger.warning("USD/IDR fetch failed: %s", exc)
        return None


def _get_google_trends(keywords: list[str], countries: list[str]) -> dict:
    """Fetch interest-over-time from Google Trends via pytrends."""
    signals = {}
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=420)
        for country in countries[:5]:  # limit to avoid rate limiting
            try:
                pt.build_payload(keywords[:3], timeframe="now 7-d", geo=_country_to_code(country))
                df = pt.interest_over_time()
                if df is not None and not df.empty:
                    avg = df[keywords[0]].mean() if keywords[0] in df.columns else 0
                    signals[country] = round(float(avg), 1)
            except Exception:
                pass
    except ImportError:
        logger.warning("pytrends not installed — trend signals unavailable.")
    return signals


def _country_to_code(country: str) -> str:
    _CODES = {
        "UAE": "AE", "Saudi Arabia": "SA", "Japan": "JP",
        "South Korea": "KR", "Australia": "AU", "Germany": "DE",
        "Netherlands": "NL", "India": "IN", "Qatar": "QA",
        "France": "FR", "UK": "GB",
    }
    return _CODES.get(country, "")


def _scrape_alibaba_prices() -> list[dict]:
    """Scrape charcoal listing prices from Alibaba search (best-effort)."""
    prices = []
    try:
        import time
        from bs4 import BeautifulSoup
        from fake_useragent import UserAgent
        ua = UserAgent()
        url = "https://www.alibaba.com/trade/search?SearchText=charcoal+exporter&IndexArea=product_en"
        resp = requests.get(url, headers={"User-Agent": ua.random}, timeout=12)
        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.select(".price, .product-price")[:10]:
            text = item.get_text(strip=True)
            if text:
                prices.append({"source": "Alibaba", "price_text": text})
        time.sleep(2)
    except Exception as exc:
        logger.debug("Alibaba price scrape failed: %s", exc)
    return prices


def _build_morning_briefing(
    usd_idr: float | None,
    trends: dict,
    trade_shows: list[dict],
    active_triggers: list[dict],
    competitor_prices: list[dict],
) -> str:
    today_str = datetime.now().strftime("%d %B %Y")
    lines = [f"🌅 *MORNING BRIEFING — {today_str}*\n"]

    # Currency
    if usd_idr:
        lines.append(f"💱 USD/IDR: *{usd_idr:,.0f}*")
    else:
        lines.append("💱 USD/IDR: tidak tersedia")

    # Demand signals
    if trends:
        top = sorted(trends.items(), key=lambda x: x[1], reverse=True)[:3]
        trend_str = " | ".join(f"{c}: {v:.0f}" for c, v in top)
        lines.append(f"📈 Demand signals: {trend_str}")

    # Trade shows
    if trade_shows:
        for show in trade_shows:
            lines.append(f"🏪 *{show['trigger_name']}* — {show['notes']}")

    # Active seasonal triggers
    seasonal = [t for t in active_triggers if t.get("trigger_type") == "seasonal"]
    if seasonal:
        names = ", ".join(t["trigger_name"] for t in seasonal)
        lines.append(f"🔥 Active seasonal: {names}")

    # Action recommendation
    lines.append("\n*⚡ Recommended Priority Today:*")
    if any("Ramadan" in t.get("trigger_name", "") for t in active_triggers):
        lines.append("→ Prioritaskan UAE & Saudi Arabia (Ramadan season)")
    elif any("Australia" in t.get("target_countries", "") for t in seasonal):
        lines.append("→ Prioritaskan Australia (BBQ season aktif)")
    elif any("Europe" in t.get("trigger_name", "") for t in seasonal):
        lines.append("→ Prioritaskan Eropa (BBQ/Winter season)")
    else:
        lines.append("→ Balance outreach ke semua target markets")

    return "\n".join(lines)


def _save_to_db(usd_idr, trends, trade_shows, competitor_prices) -> None:
    """Persist market intel snapshot."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM market_intel WHERE date=?", (date.today().isoformat(),)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE market_intel SET usd_idr_rate=?, charcoal_demand_signal=?,
                   trade_shows=?, competitor_prices=? WHERE date=?""",
                (usd_idr, json.dumps(trends),
                 json.dumps(trade_shows), json.dumps(competitor_prices),
                 date.today().isoformat()),
            )
        else:
            conn.execute(
                """INSERT INTO market_intel
                   (date, usd_idr_rate, charcoal_demand_signal, trade_shows, competitor_prices)
                   VALUES (?,?,?,?,?)""",
                (date.today().isoformat(), usd_idr,
                 json.dumps(trends), json.dumps(trade_shows), json.dumps(competitor_prices)),
            )


class MarketAgent:
    """Runs daily at 06:30 — collects market intel and sends morning briefing."""

    def run(self) -> dict:
        logger.info("[MarketAgent] Collecting market intelligence...")

        # 1. USD/IDR rate
        usd_idr = _get_usd_idr()

        # 2. Google Trends demand signals
        trends = _get_google_trends(_CHARCOAL_KEYWORDS, _TARGET_COUNTRIES)

        # 3. Update trigger engine
        run_triggers()
        active_triggers = get_active_triggers()
        trade_shows = [t for t in active_triggers if t.get("trigger_type") == "trade_show"]

        # 4. Alibaba competitor prices (best-effort)
        competitor_prices = _scrape_alibaba_prices()

        # 5. Save to DB
        _save_to_db(usd_idr, trends, trade_shows, competitor_prices)

        # 6. Send morning briefing
        briefing = _build_morning_briefing(
            usd_idr, trends, trade_shows, active_triggers, competitor_prices
        )
        send_message(briefing)
        logger.info("[MarketAgent] Morning briefing sent.")

        return {
            "usd_idr": usd_idr,
            "trend_countries": len(trends),
            "active_triggers": len(active_triggers),
            "trade_shows_upcoming": len(trade_shows),
        }
