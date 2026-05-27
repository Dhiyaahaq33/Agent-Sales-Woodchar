"""
agents/social_scrapers/linkedin_scraper.py — LinkedIn People/Company Scraper
PT Cahaya Woodchar International — PRD v2.1

⚠️  HIGHEST BAN RISK — Test last, use with extreme caution.
    LinkedIn actively bans scrapers and dummy accounts.
    Uses linkedin-api (unofficial) with a dummy account.
    Daily limit: ~50 requests max before soft-ban risk.

Finds:
  - Import/Export managers in target industries
  - Purchasing managers at hookah lounges, BBQ chains
  - Charcoal distributors on LinkedIn

Required env:
  LINKEDIN_EMAIL=dummy@email.com
  LINKEDIN_PASSWORD=your_password
  SOCIAL_LINKEDIN_ENABLED=true (default: false — requires manual enable)
"""

import logging
import os
import time
import random
from agents.social_scrapers import SocialScraperBase, is_charcoal_relevant, extract_email_from_text

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = [
    "charcoal importer",
    "hookah lounge owner",
    "shisha supplier",
    "BBQ restaurant supply manager",
    "coal import manager",
    "charcoal distributor",
]

TARGET_INDUSTRIES = [
    "Food & Beverages",
    "Import and Export",
    "Wholesale",
    "Restaurants",
    "Retail",
]

TARGET_TITLES = [
    "import manager", "purchasing manager", "procurement",
    "supply chain", "owner", "founder", "director",
    "buyer", "sourcing", "trading",
]

MAX_RESULTS_PER_SEARCH = 10
REQUEST_DELAY = (5, 12)  # Be slow to avoid detection


class LinkedInScraper(SocialScraperBase):
    platform_name = "LinkedIn"
    max_results_per_run = 30

    def _check_enabled(self) -> bool:
        """LinkedIn is disabled by default — must explicitly enable."""
        import os
        global_flag = os.getenv("SOCIAL_SCRAPING_ENABLED", "false").lower() == "true"
        linkedin_flag = os.getenv("SOCIAL_LINKEDIN_ENABLED", "false").lower() == "true"
        return global_flag and linkedin_flag

    def _login(self):
        """Login to LinkedIn with dummy account."""
        email = os.getenv("LINKEDIN_EMAIL", "")
        password = os.getenv("LINKEDIN_PASSWORD", "")
        if not email or not password:
            raise ValueError("LINKEDIN_EMAIL/PASSWORD not set in .env")

        try:
            from linkedin_api import Linkedin
        except ImportError:
            raise ImportError("linkedin-api not installed. Run: pip install linkedin-api")

        api = Linkedin(email, password)
        logger.info(f"LinkedIn: logged in as {email}")
        return api

    def _search_people(self, api, keyword: str) -> list:
        """Search LinkedIn people by keyword."""
        leads = []
        try:
            results = api.search_people(
                keywords=keyword,
                limit=MAX_RESULTS_PER_SEARCH,
                industries=["Food & Beverages", "Import and Export", "Wholesale"],
            )

            for person in results:
                try:
                    time.sleep(random.uniform(*REQUEST_DELAY))

                    urn_id = person.get("urn_id", "")
                    if not urn_id:
                        continue

                    # Get full profile
                    profile = api.get_profile(urn_id=urn_id)
                    if not profile:
                        continue

                    first = profile.get("firstName", "")
                    last = profile.get("lastName", "")
                    headline = profile.get("headline", "")
                    summary = profile.get("summary", "")
                    location = profile.get("locationName", "")
                    company_name = ""
                    title = ""

                    # Get current company from experience
                    experiences = profile.get("experience", [])
                    if experiences:
                        current = experiences[0]
                        company_name = current.get("companyName", "")
                        title = current.get("title", "")

                    combined = f"{headline} {summary} {title} {company_name} {keyword}"
                    if not is_charcoal_relevant(combined):
                        # At least check title relevance
                        if not any(t in title.lower() for t in TARGET_TITLES):
                            continue

                    contact_name = f"{first} {last}".strip()
                    profile_url = f"https://linkedin.com/in/{profile.get('public_id', urn_id)}"

                    # LinkedIn profiles don't expose email directly
                    email = extract_email_from_text(summary) or ""

                    # Infer country from location
                    country = self._guess_country(location)

                    lead = self._make_lead(
                        company=company_name or contact_name,
                        email=email,
                        country=country,
                        social_handle=profile.get("public_id", ""),
                        social_url=profile_url,
                        social_followers=0,  # LinkedIn doesn't expose connections count easily
                        social_bio=f"{headline} | {summary[:300]}",
                        discovery_context=f"LinkedIn search: {keyword}",
                        notes=(
                            f"LinkedIn | {title} at {company_name} | "
                            f"Location: {location} | Keyword: {keyword}"
                        ),
                        contact_name=contact_name,
                    )
                    leads.append(lead)
                    logger.debug(f"LinkedIn lead: {contact_name} @ {company_name}")

                except Exception as e:
                    logger.debug(f"Error processing LinkedIn person: {e}")
                    continue

        except Exception as e:
            logger.warning(f"LinkedIn search failed for '{keyword}': {e}")

        return leads

    def _guess_country(self, location: str) -> str:
        """Infer country from location string."""
        if not location:
            return ""
        loc_lower = location.lower()
        mapping = {
            "japan": "JP", "australia": "AU", "germany": "DE",
            "united kingdom": "GB", "uk": "GB",
            "united states": "US", "usa": "US",
            "uae": "AE", "dubai": "AE", "abu dhabi": "AE",
            "saudi arabia": "SA", "qatar": "QA", "kuwait": "KW",
            "singapore": "SG", "malaysia": "MY",
            "india": "IN", "turkey": "TR",
            "south korea": "KR", "korea": "KR",
            "france": "FR", "netherlands": "NL",
        }
        for keyword, code in mapping.items():
            if keyword in loc_lower:
                return code
        return ""

    def scrape(self) -> list:
        """Search LinkedIn for charcoal import/supply professionals."""
        leads = []

        try:
            api = self._login()
        except Exception as e:
            logger.error(f"LinkedIn login failed: {e}")
            return []

        keywords_today = random.sample(SEARCH_KEYWORDS, min(3, len(SEARCH_KEYWORDS)))
        logger.warning(
            "LinkedIn scraping active — LIMIT: 3 searches/run, 50 requests/day max. "
            "DO NOT increase limits."
        )

        for keyword in keywords_today:
            logger.debug(f"LinkedIn: searching '{keyword}'")
            try:
                batch = self._search_people(api, keyword)
                leads.extend(batch)
                time.sleep(random.uniform(15, 30))  # Longer pause between LinkedIn searches
                if len(leads) >= self.max_results_per_run:
                    break
            except Exception as e:
                logger.warning(f"LinkedIn search error: {e}")

        logger.info(f"LinkedIn scraper: {len(leads)} leads found")
        return leads
