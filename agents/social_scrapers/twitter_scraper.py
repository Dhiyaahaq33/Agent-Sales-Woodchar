"""
agents/social_scrapers/twitter_scraper.py — Twitter/X Account Scraper
PT Cahaya Woodchar International — PRD v2.1

Uses snscrape to search recent tweets without needing API key.
Finds accounts tweeting about charcoal import, hookah supply, BBQ wholesale.

⚠️  snscrape doesn't need API key but scrapes at HTML level — may break on X.com changes.
⚠️  Twitter/X increasingly blocks scrapers. Backup: use twscrape or official API v2.

Required env:
  SOCIAL_TWITTER_ENABLED=true
Optional:
  TWITTER_BEARER_TOKEN=... (for official API v2 fallback)
"""

import logging
import os
import time
import random
from agents.social_scrapers import SocialScraperBase, is_charcoal_relevant, extract_email_from_text

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "charcoal supplier import wholesale -is:retweet",
    "hookah charcoal wholesale supplier -is:retweet",
    "shisha charcoal business -is:retweet",
    "BBQ charcoal wholesale importer -is:retweet",
    "lump charcoal buyer -is:retweet",
    "coconut charcoal supplier -is:retweet",
]

MIN_FOLLOWERS = 100
MAX_TWEETS_PER_QUERY = 30
REQUEST_DELAY = (2, 5)


class TwitterScraper(SocialScraperBase):
    platform_name = "Twitter"
    max_results_per_run = 50

    def _scrape_with_snscrape(self, query: str) -> list:
        """Use snscrape to search tweets."""
        leads = []
        try:
            import snscrape.modules.twitter as sntwitter
        except ImportError:
            raise ImportError("snscrape not installed. Run: pip install snscrape")

        seen_user_ids = set()
        try:
            for i, tweet in enumerate(
                sntwitter.TwitterSearchScraper(query).get_items()
            ):
                if i >= MAX_TWEETS_PER_QUERY:
                    break

                user = tweet.user
                if not user:
                    continue

                user_id = user.id
                if user_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_id)

                followers = user.followersCount or 0
                if followers < MIN_FOLLOWERS:
                    continue

                bio = user.description or ""
                display_name = user.displayname or ""
                username = user.username or ""
                location = user.location or ""
                website = user.linkUrl or ""

                combined = f"{display_name} {bio} {tweet.rawContent}"
                if not is_charcoal_relevant(combined):
                    continue

                email = extract_email_from_text(bio) or extract_email_from_text(website) or ""

                # Infer country from location string (best-effort)
                country = self._guess_country(location)

                lead = self._make_lead(
                    company=display_name or username,
                    email=email,
                    country=country,
                    social_handle=f"@{username}",
                    social_url=f"https://twitter.com/{username}",
                    social_followers=followers,
                    social_bio=bio,
                    discovery_context=f"Twitter search: {query[:60]}",
                    notes=(
                        f"Twitter/X | {followers:,} followers | "
                        f"Location: {location or 'N/A'} | {website or 'no website'}"
                    ),
                    contact_name=display_name,
                )
                leads.append(lead)
                logger.debug(f"Twitter lead: @{username} ({followers:,} followers)")

                time.sleep(random.uniform(*REQUEST_DELAY))

        except Exception as e:
            logger.warning(f"snscrape search failed for '{query}': {e}")

        return leads

    def _guess_country(self, location: str) -> str:
        """Very rough country inference from location string."""
        if not location:
            return ""
        loc_lower = location.lower()
        mapping = {
            "japan": "JP", "tokyo": "JP", "osaka": "JP",
            "australia": "AU", "sydney": "AU", "melbourne": "AU",
            "germany": "DE", "berlin": "DE",
            "uk": "GB", "london": "GB", "england": "GB",
            "usa": "US", "united states": "US", "new york": "US",
            "uae": "AE", "dubai": "AE",
            "saudi": "SA", "riyadh": "SA",
            "india": "IN", "mumbai": "IN", "delhi": "IN",
            "singapore": "SG",
            "malaysia": "MY", "kuala lumpur": "MY",
            "turkey": "TR", "istanbul": "TR",
            "south korea": "KR", "korea": "KR", "seoul": "KR",
            "france": "FR", "paris": "FR",
            "netherlands": "NL", "amsterdam": "NL",
        }
        for keyword, code in mapping.items():
            if keyword in loc_lower:
                return code
        return ""

    def scrape(self) -> list:
        """Run Twitter searches for charcoal-related accounts."""
        leads = []
        queries_today = random.sample(SEARCH_QUERIES, min(4, len(SEARCH_QUERIES)))

        for query in queries_today:
            logger.debug(f"Twitter: searching '{query}'")
            try:
                batch = self._scrape_with_snscrape(query)
                leads.extend(batch)
                time.sleep(random.uniform(10, 20))
                if len(leads) >= self.max_results_per_run:
                    break
            except Exception as e:
                logger.warning(f"Twitter scrape error for '{query}': {e}")

        logger.info(f"Twitter scraper: {len(leads)} leads found")
        return leads
