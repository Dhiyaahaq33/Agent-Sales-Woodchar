"""Scraper Agent — discovers charcoal buyer leads from free B2B directories."""
import logging

import db.database as db
from tools.scraper_tool import scrape_all

logger = logging.getLogger(__name__)


class ScraperAgent:
    """Runs all scrapers, deduplicates, and persists new leads to SQLite."""

    def run(self) -> dict:
        """Execute scraping run. Returns summary stats."""
        logger.info("[ScraperAgent] Starting lead discovery run...")
        raw = scrape_all()

        inserted = 0
        skipped = 0
        for lead in raw:
            company = lead.get("company_name", "").strip()
            if not company:
                skipped += 1
                continue

            lead_id = db.insert_lead(
                company_name=company,
                email=lead.get("email") or None,
                website=lead.get("website") or None,
                country=lead.get("country") or None,
                industry=lead.get("industry") or None,
                source=lead.get("source"),
            )
            if lead_id:
                inserted += 1
            else:
                skipped += 1

        logger.info(
            "[ScraperAgent] Done — inserted=%d, skipped/duplicates=%d",
            inserted, skipped,
        )
        return {"inserted": inserted, "skipped": skipped, "raw_total": len(raw)}
