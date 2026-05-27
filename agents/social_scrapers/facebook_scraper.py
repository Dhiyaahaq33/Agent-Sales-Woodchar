"""
agents/social_scrapers/facebook_scraper.py — Facebook Page/Group Scraper
PT Cahaya Woodchar International — PRD v2.1

Uses facebook-scraper library (no API key needed, public pages only).
Searches public Facebook pages and groups related to:
  - Hookah lounges, shisha bars
  - BBQ restaurants, grilling supply
  - Charcoal importers/distributors

⚠️  facebook-scraper uses HTML parsing — may break when Facebook updates layout.
⚠️  Use rotating proxies (PROXY_URL env) if IP gets blocked.

Required env:
  SOCIAL_FACEBOOK_ENABLED=true
Optional:
  FACEBOOK_PROXY=http://proxy:port  (to avoid IP blocks)
"""

import logging
import os
import time
import random
from agents.social_scrapers import SocialScraperBase, is_charcoal_relevant, extract_email_from_text

logger = logging.getLogger(__name__)

# Public Facebook pages to scrape (populated from a manual seed list + dynamic search)
SEED_PAGE_KEYWORDS = [
    "hookah lounge wholesale",
    "shisha charcoal supplier",
    "BBQ restaurant supply",
    "charcoal importer",
    "coconut charcoal wholesale",
]

# Known charcoal-related Facebook group search terms
GROUP_SEARCH_TERMS = [
    "hookah business owners",
    "shisha lounge owners",
    "BBQ supply buyers",
    "charcoal importers",
]

MAX_POSTS_PER_PAGE = 5
REQUEST_DELAY = (5, 15)


class FacebookScraper(SocialScraperBase):
    platform_name = "Facebook"
    max_results_per_run = 40

    def _get_proxy(self) -> dict:
        proxy_url = os.getenv("FACEBOOK_PROXY", "")
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return {}

    def _scrape_page(self, page_name: str) -> list:
        """Scrape a specific Facebook page for business info."""
        leads = []
        try:
            from facebook_scraper import get_page_info
        except ImportError:
            raise ImportError("facebook-scraper not installed. Run: pip install facebook-scraper")

        try:
            info = get_page_info(page_name, options={"proxy": self._get_proxy()})
            if not info:
                return []

            name = info.get("name", page_name)
            about = info.get("about", "") or ""
            description = info.get("description", "") or ""
            email = (
                info.get("email", "")
                or extract_email_from_text(about)
                or extract_email_from_text(description)
                or ""
            )
            website = info.get("website", "")
            followers = info.get("followers", 0) or 0
            country = info.get("country_page_likes", "") or ""

            combined = f"{name} {about} {description}"
            if not is_charcoal_relevant(combined):
                return []

            lead = self._make_lead(
                company=name,
                email=email,
                country=country[:2].upper() if country else "",
                social_handle=page_name,
                social_url=f"https://facebook.com/{page_name}",
                social_followers=followers,
                social_bio=about[:500],
                discovery_context=f"Facebook page: {page_name}",
                notes=f"Facebook page | {followers:,} followers | {website}",
            )
            leads.append(lead)
            logger.debug(f"Facebook lead: {name} ({followers:,} followers)")

        except Exception as e:
            logger.debug(f"Failed to scrape Facebook page '{page_name}': {e}")

        return leads

    def _search_pages(self, keyword: str) -> list:
        """
        Search Facebook public pages by keyword.
        facebook-scraper doesn't have native search; we use Google to find FB pages.
        """
        leads = []
        try:
            from googlesearch import search
        except ImportError:
            logger.warning("googlesearch-python not installed; skipping FB page search")
            return []

        query = f"site:facebook.com/pages {keyword} charcoal"
        try:
            results = list(search(query, num_results=5, sleep_interval=2))
            for url in results:
                # Extract page name from URL: facebook.com/pages/page-name/id
                parts = url.rstrip("/").split("/")
                if "pages" in parts:
                    idx = parts.index("pages")
                    if idx + 1 < len(parts):
                        page_slug = parts[idx + 1]
                        batch = self._scrape_page(page_slug)
                        leads.extend(batch)
                        time.sleep(random.uniform(*REQUEST_DELAY))
        except Exception as e:
            logger.warning(f"Facebook page search failed for '{keyword}': {e}")

        return leads

    def scrape(self) -> list:
        """Scrape Facebook pages related to charcoal business."""
        leads = []

        for keyword in SEED_PAGE_KEYWORDS:
            logger.debug(f"Facebook: searching pages for '{keyword}'")
            try:
                batch = self._search_pages(keyword)
                leads.extend(batch)
                time.sleep(random.uniform(8, 15))
                if len(leads) >= self.max_results_per_run:
                    break
            except Exception as e:
                logger.warning(f"Facebook scrape error for '{keyword}': {e}")

        logger.info(f"Facebook scraper: {len(leads)} leads found")
        return leads
