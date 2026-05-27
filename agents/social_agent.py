"""
agents/social_agent.py — Social Media Lead Scraper Orchestrator
PT Cahaya Woodchar International — PRD v2.1

Runs platform scrapers in daily rotation:
  Mon/Thu: YouTube + Instagram
  Tue/Fri: Facebook + TikTok
  Wed/Sat: Twitter
  Sun:     LinkedIn (low-risk rotation)

Each run:
  1. Calls enabled platform scrapers
  2. Deduplicates against existing leads in DB
  3. Inserts new leads with status=NEW and source=social_*
  4. Sends Telegram summary

Called by scheduler at 07:00 daily.

Usage:
  from agents.social_agent import SocialAgent
  agent = SocialAgent()
  agent.run()
"""

import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

# Platform rotation schedule (weekday 0=Monday, 6=Sunday)
ROTATION_SCHEDULE = {
    0: ["YouTube", "Instagram"],   # Monday
    1: ["Facebook", "TikTok"],     # Tuesday
    2: ["Twitter"],                # Wednesday
    3: ["YouTube", "Instagram"],   # Thursday
    4: ["Facebook", "TikTok"],     # Friday
    5: ["Twitter"],                # Saturday
    6: ["LinkedIn"],               # Sunday
}


def _get_scraper(platform: str):
    """Lazy import platform scraper to avoid dependency errors on disabled platforms."""
    scrapers = {
        "YouTube": ("agents.social_scrapers.youtube_scraper", "YouTubeScraper"),
        "Instagram": ("agents.social_scrapers.instagram_scraper", "InstagramScraper"),
        "Facebook": ("agents.social_scrapers.facebook_scraper", "FacebookScraper"),
        "TikTok": ("agents.social_scrapers.tiktok_scraper", "TikTokScraper"),
        "Twitter": ("agents.social_scrapers.twitter_scraper", "TwitterScraper"),
        "LinkedIn": ("agents.social_scrapers.linkedin_scraper", "LinkedInScraper"),
    }
    if platform not in scrapers:
        raise ValueError(f"Unknown platform: {platform}")
    module_name, class_name = scrapers[platform]
    import importlib
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls()


class SocialAgent:
    def __init__(self):
        self.enabled = os.getenv("SOCIAL_SCRAPING_ENABLED", "false").lower() == "true"

    def _get_today_platforms(self) -> list:
        """Get list of platforms to scrape today based on rotation schedule."""
        weekday = date.today().weekday()  # 0=Monday
        return ROTATION_SCHEDULE.get(weekday, [])

    def _deduplicate(self, leads: list) -> list:
        """Filter out leads that already exist in DB (by email or social handle)."""
        from db.database import get_conn
        new_leads = []
        try:
            with get_conn() as conn:
                existing_emails = set(
                    r[0].lower() for r in conn.execute(
                        "SELECT email FROM leads WHERE email IS NOT NULL AND email != ''"
                    ).fetchall()
                )
                existing_handles = set(
                    r[0].lower() for r in conn.execute(
                        "SELECT social_handle FROM leads WHERE social_handle IS NOT NULL AND social_handle != ''"
                    ).fetchall()
                    if r[0]
                )

            for lead in leads:
                email = (lead.get("email") or "").lower().strip()
                handle = (lead.get("social_handle") or "").lower().strip()
                if email and email in existing_emails:
                    continue
                if handle and handle in existing_handles:
                    continue
                new_leads.append(lead)

        except Exception as e:
            logger.error(f"Deduplication error: {e}")
            return leads  # Return all if dedup fails, let DB unique constraint handle it

        return new_leads

    def _insert_leads(self, leads: list) -> int:
        """Insert new social leads into DB. Returns count inserted."""
        from db.database import insert_lead
        inserted = 0
        for lead in leads:
            try:
                # Check if country is BLOCKED before inserting
                country = lead.get("country", "")
                if country:
                    from tools.country_risk import get_country_risk
                    cr = get_country_risk()
                    if cr.is_blocked(country):
                        logger.warning(
                            f"Skipping BLOCKED country lead: {lead.get('company')} ({country})"
                        )
                        continue

                insert_lead(
                    company=lead.get("company", ""),
                    contact_name=lead.get("contact_name", ""),
                    email=lead.get("email", ""),
                    country=lead.get("country", ""),
                    source=lead.get("source", f"social_{lead.get('social_platform', '').lower()}"),
                    notes=lead.get("notes", ""),
                    # v2.1 social fields (if DB has columns):
                    extra_fields={
                        "social_platform": lead.get("social_platform", ""),
                        "social_handle": lead.get("social_handle", ""),
                        "social_url": lead.get("social_url", ""),
                        "social_followers": lead.get("social_followers", 0),
                        "social_bio": lead.get("social_bio", ""),
                        "discovery_context": lead.get("discovery_context", ""),
                    }
                )
                inserted += 1
            except Exception as e:
                logger.debug(f"Lead insert skipped ({lead.get('company')}): {e}")

        return inserted

    def run(self):
        """Main daily run: scrape today's platforms and insert new leads."""
        if not self.enabled:
            logger.info("Social scraping disabled (SOCIAL_SCRAPING_ENABLED=false), skipping")
            return

        platforms_today = self._get_today_platforms()
        today_str = date.today().strftime("%A %Y-%m-%d")
        logger.info(f"SocialAgent: {today_str} — platforms: {platforms_today}")

        all_leads = []
        platform_stats = {}

        for platform in platforms_today:
            try:
                scraper = _get_scraper(platform)
                if not scraper.enabled:
                    logger.info(f"{platform} scraper disabled, skipping")
                    platform_stats[platform] = {"scraped": 0, "inserted": 0}
                    continue

                leads = scraper.run()
                new_leads = self._deduplicate(leads)
                inserted = self._insert_leads(new_leads)
                all_leads.extend(new_leads[:inserted])

                platform_stats[platform] = {
                    "scraped": len(leads),
                    "new": len(new_leads),
                    "inserted": inserted,
                }
                logger.info(
                    f"{platform}: scraped={len(leads)}, new={len(new_leads)}, inserted={inserted}"
                )

            except Exception as e:
                logger.error(f"SocialAgent: {platform} run failed: {e}")
                platform_stats[platform] = {"error": str(e)[:100]}

        # Send Telegram summary
        total_inserted = sum(s.get("inserted", 0) for s in platform_stats.values())
        self._send_telegram_summary(platforms_today, platform_stats, total_inserted)
        logger.info(f"SocialAgent complete: {total_inserted} new leads inserted")

    def _send_telegram_summary(self, platforms: list, stats: dict, total: int):
        """Send daily social scraping summary to COO via Telegram."""
        try:
            from tools.telegram_tool import send_message
            lines = [f"📱 *Social Scraper Report — {date.today().strftime('%a %d %b')}*\n"]
            for platform in platforms:
                s = stats.get(platform, {})
                if "error" in s:
                    lines.append(f"❌ {platform}: ERROR — {s['error'][:60]}")
                else:
                    lines.append(
                        f"✅ {platform}: {s.get('scraped', 0)} scraped → "
                        f"{s.get('inserted', 0)} new leads"
                    )
            lines.append(f"\n📊 Total new leads today: *{total}*")
            send_message("\n".join(lines))
        except Exception as e:
            logger.warning(f"Could not send social scraper Telegram summary: {e}")
