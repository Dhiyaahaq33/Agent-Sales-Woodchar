"""
agents/competitor_agent.py — Alibaba Competitor Price Monitor
PT Cahaya Woodchar International — v3.0

Monitors competitor Alibaba listings (from data/competitors.json) for:
  - Price changes (alert if competitor drops >5%)
  - New product listings from competitors
  - MOQ changes
  - Review count / transaction count growth (market share signals)

Runs every 6 hours via scheduler.
Stores history in competitor_history table.
Sends Telegram alert when significant price changes detected.

Also tracks our own Alibaba listing position (if URL provided in config).

Usage:
  from agents.competitor_agent import CompetitorAgent
  agent = CompetitorAgent()
  agent.run()
"""

import json
import logging
import os
import re
import time
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

COMPETITORS_FILE = Path(__file__).parent.parent / "data" / "competitors.json"
PRICE_CHANGE_ALERT_THRESHOLD = 0.05   # Alert if price changes by ±5%
REQUEST_DELAY = (5, 15)               # Seconds between Alibaba requests

# Regex patterns for price extraction from Alibaba HTML
_PRICE_PATTERNS = [
    re.compile(r'\$\s*(\d+(?:\.\d+)?)\s*[-–]\s*\$\s*(\d+(?:\.\d+)?)'),  # "$200 - $350"
    re.compile(r'USD\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)'),       # "USD 200 - 350"
    re.compile(r'(\d+(?:\.\d+)?)\s*USD\s*/\s*(?:MT|Metric\s*Ton)'),      # "280 USD/MT"
    re.compile(r'USD\s*(\d+(?:\.\d+)?)'),                                 # "USD 280"
]

_MOQ_PATTERNS = [
    re.compile(r'(\d+(?:\.\d+)?)\s*(?:Metric\s*Tons?|MT)\s*(?:Min\.?\s*Order)?', re.IGNORECASE),
    re.compile(r'Min\.?\s*Order[:.]?\s*(\d+(?:\.\d+)?)\s*(?:MT|Ton)', re.IGNORECASE),
]


def _extract_price_from_text(text: str) -> Optional[float]:
    """Try all price patterns, return average of range or single price."""
    for pattern in _PRICE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                groups = m.groups()
                if len(groups) >= 2 and groups[1]:
                    return (float(groups[0]) + float(groups[1])) / 2
                return float(groups[0])
            except (ValueError, IndexError):
                continue
    return None


def _extract_moq_from_text(text: str) -> Optional[float]:
    """Extract minimum order quantity from text."""
    for pattern in _MOQ_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


class CompetitorAgent:
    def __init__(self):
        self.competitors = self._load_competitors()

    def _load_competitors(self) -> list:
        """Load competitor list from JSON file."""
        try:
            data = json.loads(COMPETITORS_FILE.read_text(encoding="utf-8"))
            comps = data.get("competitors", [])
            # Filter out unfilled entries
            filled = [c for c in comps if c.get("alibaba_url") != "FILL_BY_COO"
                      and c.get("name") != "FILL_BY_COO"]
            if not filled:
                logger.info("CompetitorAgent: no competitors configured yet (all FILL_BY_COO)")
            return filled
        except Exception as e:
            logger.error("Failed to load competitors.json: %s", e)
            return []

    def _scrape_alibaba_listing(self, url: str) -> dict:
        """
        Scrape an Alibaba product listing page.
        Returns dict with price, moq, product_name, reviews, transactions.
        """
        try:
            import requests
            from bs4 import BeautifulSoup
            from fake_useragent import UserAgent

            ua = UserAgent()
            headers = {
                "User-Agent": ua.random,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning("Alibaba request failed: %d for %s", resp.status_code, url[:60])
                return {}

            soup = BeautifulSoup(resp.text, "lxml")
            text = soup.get_text(" ", strip=True)

            # Extract price
            price = _extract_price_from_text(text)

            # Extract MOQ
            moq = _extract_moq_from_text(text)

            # Extract product name
            title_tag = soup.find("h1") or soup.find("title")
            product_name = title_tag.get_text(strip=True)[:200] if title_tag else ""

            # Try to extract transaction count / review count
            tx_match = re.search(r'(\d+[\d,]*)\s*(?:transaction|order)s?', text, re.IGNORECASE)
            transactions = int(tx_match.group(1).replace(",", "")) if tx_match else 0

            review_match = re.search(r'(\d+)\s*review', text, re.IGNORECASE)
            reviews = int(review_match.group(1)) if review_match else 0

            return {
                "price_usd_per_mt": price,
                "moq_mt": moq,
                "product_name": product_name,
                "transactions": transactions,
                "reviews": reviews,
                "raw_text_sample": text[:500],
            }

        except Exception as e:
            logger.warning("Alibaba scrape failed for %s: %s", url[:60], e)
            return {}

    def _get_last_price(self, competitor_id: str, product_type: str) -> Optional[float]:
        """Get the most recent recorded price for a competitor product."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT price_usd_per_mt FROM competitor_history
                       WHERE competitor_id = ? AND product_type = ?
                       ORDER BY scraped_at DESC LIMIT 1""",
                    (competitor_id, product_type)
                ).fetchone()
            return row["price_usd_per_mt"] if row else None
        except Exception:
            return None

    def _save_price(self, competitor: dict, product_type: str, scraped: dict, change_pct: float = 0.0):
        """Save price observation to competitor_history table."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO competitor_history
                       (competitor_id, competitor_name, product_type, price_usd_per_mt,
                        moq_mt, alibaba_url, change_pct, notes)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        competitor["id"], competitor["name"], product_type,
                        scraped.get("price_usd_per_mt"),
                        scraped.get("moq_mt"),
                        competitor.get("alibaba_url", ""),
                        change_pct,
                        scraped.get("product_name", "")[:200]
                    )
                )
        except Exception as e:
            logger.error("Failed to save competitor price: %s", e)

    def _check_price_change(self, competitor: dict, product_type: str,
                            new_price: float) -> Optional[dict]:
        """
        Compare with previous price. Returns alert dict if significant change.
        """
        old_price = self._get_last_price(competitor["id"], product_type)
        if old_price is None or old_price <= 0:
            return None  # First observation, no change to report

        change_pct = (new_price - old_price) / old_price
        if abs(change_pct) >= PRICE_CHANGE_ALERT_THRESHOLD:
            direction = "📉 DROPPED" if change_pct < 0 else "📈 RAISED"
            return {
                "competitor": competitor["name"],
                "product": product_type,
                "old_price": old_price,
                "new_price": new_price,
                "change_pct": round(change_pct * 100, 1),
                "direction": direction,
                "alert": abs(change_pct) >= 0.10,  # Extra urgent if >10%
            }
        return None

    def run(self):
        """Scrape all configured competitors and detect price changes."""
        if not self.competitors:
            logger.info("CompetitorAgent: no competitors configured — skipping")
            return {"scraped": 0, "alerts": 0}

        logger.info("CompetitorAgent: monitoring %d competitors", len(self.competitors))
        alerts = []
        scraped_count = 0

        for comp in self.competitors:
            url = comp.get("alibaba_url", "")
            if not url:
                continue

            logger.debug("Scraping: %s (%s)", comp.get("name"), url[:60])
            data = self._scrape_alibaba_listing(url)

            if not data:
                logger.warning("No data scraped for %s", comp.get("name"))
                continue

            scraped_count += 1
            new_price = data.get("price_usd_per_mt")

            for product_type in comp.get("known_products", ["unknown"]):
                if new_price:
                    alert = self._check_price_change(comp, product_type, new_price)
                    if alert:
                        alerts.append(alert)
                self._save_price(comp, product_type, data)

            logger.info(
                "Competitor %s: price=$%.0f/MT, MOQ=%.0f MT, tx=%d",
                comp.get("name", "?"),
                new_price or 0,
                data.get("moq_mt") or 0,
                data.get("transactions", 0),
            )
            time.sleep(random.uniform(*REQUEST_DELAY))

        # Send alerts
        if alerts:
            self._send_alerts(alerts)

        # Also compare with our pricing
        if scraped_count > 0:
            self._send_competitive_position_report()

        logger.info("CompetitorAgent done: scraped=%d, alerts=%d", scraped_count, len(alerts))
        return {"scraped": scraped_count, "alerts": len(alerts)}

    def _send_alerts(self, alerts: list):
        """Send price change alerts to COO via Telegram."""
        try:
            from tools.telegram_tool import send_message
            msg_lines = ["⚡ *Competitor Price Change Alert*\n"]
            for a in alerts[:5]:  # Max 5 alerts per run
                urgency = "🚨" if a.get("alert") else "⚠️"
                msg_lines.append(
                    f"{urgency} *{a['competitor']}*\n"
                    f"   {a['product']} — {a['direction']}\n"
                    f"   ${a['old_price']:.0f} → ${a['new_price']:.0f}/MT "
                    f"({a['change_pct']:+.1f}%)"
                )
            send_message("\n\n".join(msg_lines))
        except Exception as e:
            logger.error("Failed to send competitor alerts: %s", e)

    def _send_competitive_position_report(self):
        """Send daily competitive position summary."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT competitor_name, AVG(price_usd_per_mt) as avg_price,
                              COUNT(*) as observations
                       FROM competitor_history
                       WHERE price_usd_per_mt IS NOT NULL
                         AND DATE(scraped_at) = DATE('now')
                       GROUP BY competitor_name
                       ORDER BY avg_price ASC"""
                ).fetchall()

            if not rows:
                return

            from agents.pricing_agent import PricingAgent
            pa = PricingAgent()
            our_price = pa.calculate("CWI-001", 50)["fob_price_usd_mt"]

            lines = ["📊 *Competitive Position Report*\n"]
            lines.append(f"Our FOB (CWI-001, 50MT): *${our_price}/MT*\n")
            lines.append("Competitors:")
            for r in rows:
                diff = our_price - r["avg_price"]
                icon = "✅" if diff <= 10 else "⚠️"
                lines.append(
                    f"{icon} {r['competitor_name']}: ${r['avg_price']:.0f}/MT "
                    f"({'+' if diff > 0 else ''}{diff:.0f} vs us)"
                )

            from tools.telegram_tool import send_message
            send_message("\n".join(lines))
        except Exception as e:
            logger.error("Competitive position report failed: %s", e)

    def get_market_avg_price(self, product_type: str = "coconut shell") -> Optional[float]:
        """Return current market average price from recent competitor data."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT AVG(price_usd_per_mt) as avg_price
                       FROM competitor_history
                       WHERE product_type LIKE ?
                         AND price_usd_per_mt IS NOT NULL
                         AND DATE(scraped_at) >= DATE('now', '-7 days')""",
                    (f"%{product_type.lower()}%",)
                ).fetchone()
            return round(row["avg_price"], 2) if row and row["avg_price"] else None
        except Exception:
            return None
