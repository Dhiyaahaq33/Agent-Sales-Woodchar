"""
agents/social_scrapers/__init__.py — Social Scraper Base Class
PT Cahaya Woodchar International — PRD v2.1

Base class for all 6 platform scrapers.
Each scraper finds potential buyers posting about:
  - charcoal, hookah, shisha, BBQ, grilling, arang, coal
  - related hashtags and communities

All scrapers return a standardized list of lead dicts
which SocialAgent inserts into the DB via insert_lead().
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)

# Charcoal-relevant keywords for filtering
CHARCOAL_KEYWORDS = [
    "charcoal", "hookah", "shisha", "barbecue", "bbq", "grilling", "grill",
    "arang", "charbon", "holzkohle", "lump charcoal", "coconut charcoal",
    "restaurant supply", "wholesale charcoal", "charcoal supplier", "coal",
    "briquette", "briquettes", "brikett", "food service", "steakhouse",
    "import charcoal", "charcoal importer", "charcoal buyer", "activated carbon",
]

CHARCOAL_HASHTAGS = [
    "#charcoal", "#hookah", "#shisha", "#bbq", "#barbecue", "#grilling",
    "#charcoalgrill", "#lumpcharcoal", "#coconutcharcoal", "#bbqlife",
    "#pitmaster", "#smokedmeat", "#bbqlovers", "#charcoalbriquettes",
    "#hookahlounge", "#shishabar",
]


def is_charcoal_relevant(text: str) -> bool:
    """Check if text mentions charcoal-relevant keywords."""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in CHARCOAL_KEYWORDS)


def extract_email_from_text(text: str) -> Optional[str]:
    """Extract first email address found in text."""
    if not text:
        return None
    pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    match = re.search(pattern, text)
    return match.group(0) if match else None


def clean_url(url: str) -> str:
    """Normalize URL to remove tracking params."""
    if not url:
        return ""
    # Strip query params that are tracking only
    return url.split("?")[0].rstrip("/")


class SocialScraperBase(ABC):
    """
    Abstract base class for all social media scrapers.

    Each subclass must implement:
      - platform_name: str
      - scrape() → list[dict]

    Each returned dict must have at minimum:
      - company: str (handle, page name, or channel name)
      - email: str or ""
      - country: str (2-letter ISO or "")
      - source: str  (platform name)
      - social_platform: str
      - social_handle: str
      - social_url: str
      - discovery_context: str (hashtag or keyword that matched)
      - notes: str
    """

    platform_name: str = "unknown"
    max_results_per_run: int = 50

    def __init__(self):
        self.enabled = self._check_enabled()
        self.results = []

    def _check_enabled(self) -> bool:
        """Check if this platform's scraping is enabled via env var."""
        import os
        global_flag = os.getenv("SOCIAL_SCRAPING_ENABLED", "false").lower() == "true"
        platform_flag_key = f"SOCIAL_{self.platform_name.upper()}_ENABLED"
        platform_flag = os.getenv(platform_flag_key, "true").lower() == "true"
        return global_flag and platform_flag

    @abstractmethod
    def scrape(self) -> list:
        """
        Main scrape method. Returns list of lead dicts.
        Must handle all exceptions internally — never raises.
        """
        ...

    def run(self) -> list:
        """
        Entry point called by SocialAgent.
        Wraps scrape() with error handling and deduplication.
        """
        if not self.enabled:
            logger.info(f"{self.platform_name} scraper disabled, skipping")
            return []

        logger.info(f"Starting {self.platform_name} scraper...")
        try:
            results = self.scrape()
            seen_emails = set()
            unique = []
            for r in results:
                email = r.get("email", "").lower().strip()
                handle = r.get("social_handle", "").lower().strip()
                key = email or handle
                if key and key not in seen_emails:
                    seen_emails.add(key)
                    r.setdefault("source", f"social_{self.platform_name.lower()}")
                    r.setdefault("social_platform", self.platform_name)
                    r.setdefault("status", "NEW")
                    unique.append(r)
            logger.info(f"{self.platform_name} scraper: {len(unique)} unique leads found")
            return unique
        except Exception as e:
            logger.error(f"{self.platform_name} scraper failed: {e}")
            return []

    def _make_lead(self, *, company: str, email: str = "", country: str = "",
                   social_handle: str = "", social_url: str = "",
                   social_followers: int = 0, social_bio: str = "",
                   discovery_context: str = "", notes: str = "",
                   contact_name: str = "") -> dict:
        """Helper to build a standardized lead dict."""
        return {
            "company": company or social_handle or "Unknown",
            "contact_name": contact_name,
            "email": email,
            "country": country,
            "source": f"social_{self.platform_name.lower()}",
            "social_platform": self.platform_name,
            "social_handle": social_handle,
            "social_url": social_url,
            "social_followers": social_followers,
            "social_bio": social_bio[:500] if social_bio else "",
            "discovery_context": discovery_context,
            "notes": notes or f"Discovered via {self.platform_name} scraper",
            "status": "NEW",
            "score": 0,
        }
