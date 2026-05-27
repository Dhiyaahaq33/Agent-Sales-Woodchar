"""
agents/social_scrapers/youtube_scraper.py — YouTube Channel Scraper
PT Cahaya Woodchar International — PRD v2.1

Uses YouTube Data API v3 (official, free tier: 10,000 units/day).

Searches for channels that:
  - Post BBQ / hookah / shisha / charcoal content
  - Have enough followers to be a real business (≥500 subs)
  - May be importers, distributors, or BBQ supply businesses

Required env:
  YOUTUBE_API_KEY=AIza...

Cost estimate: ~100 quota units per search query (10 queries = 1,000 units/day ← safe)
"""

import logging
import os
from agents.social_scrapers import SocialScraperBase, is_charcoal_relevant, extract_email_from_text

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "hookah charcoal supplier",
    "shisha charcoal wholesale",
    "BBQ charcoal restaurant supply",
    "lump charcoal importer",
    "coconut charcoal business",
    "charcoal BBQ restaurant",
    "charcoal briquette wholesale",
]

MIN_SUBSCRIBERS = 500
MAX_RESULTS_PER_QUERY = 10


class YouTubeScraper(SocialScraperBase):
    platform_name = "YouTube"
    max_results_per_run = 70  # 7 queries × 10 results

    def _get_youtube_client(self):
        api_key = os.getenv("YOUTUBE_API_KEY", "")
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY not set in .env")
        try:
            from googleapiclient.discovery import build
            return build("youtube", "v3", developerKey=api_key)
        except ImportError:
            raise ImportError("google-api-python-client not installed. Run: pip install google-api-python-client")

    def _search_channels(self, youtube, query: str) -> list:
        """Search YouTube channels matching a query."""
        try:
            response = youtube.search().list(
                q=query,
                type="channel",
                part="snippet",
                maxResults=MAX_RESULTS_PER_QUERY,
                order="relevance",
            ).execute()
            return response.get("items", [])
        except Exception as e:
            logger.warning(f"YouTube search failed for '{query}': {e}")
            return []

    def _get_channel_details(self, youtube, channel_id: str) -> dict:
        """Get channel statistics and full description."""
        try:
            response = youtube.channels().list(
                id=channel_id,
                part="snippet,statistics,brandingSettings"
            ).execute()
            items = response.get("items", [])
            return items[0] if items else {}
        except Exception as e:
            logger.warning(f"Failed to get channel details for {channel_id}: {e}")
            return {}

    def _parse_country_from_channel(self, channel: dict) -> str:
        """Extract country code from channel branding settings."""
        try:
            branding = channel.get("brandingSettings", {})
            country = branding.get("channel", {}).get("country", "")
            return country.upper()[:2] if country else ""
        except Exception:
            return ""

    def scrape(self) -> list:
        """Search YouTube for charcoal-related channels and extract lead data."""
        leads = []
        try:
            youtube = self._get_youtube_client()
        except Exception as e:
            logger.error(f"YouTube client init failed: {e}")
            return []

        seen_channel_ids = set()

        for query in SEARCH_QUERIES:
            logger.debug(f"YouTube search: '{query}'")
            items = self._search_channels(youtube, query)

            for item in items:
                try:
                    channel_id = item.get("id", {}).get("channelId", "")
                    if not channel_id or channel_id in seen_channel_ids:
                        continue
                    seen_channel_ids.add(channel_id)

                    snippet = item.get("snippet", {})
                    channel_title = snippet.get("channelTitle", "")
                    description = snippet.get("description", "")

                    # Get full channel details for subscriber count + email
                    details = self._get_channel_details(youtube, channel_id)
                    if not details:
                        continue

                    stats = details.get("statistics", {})
                    sub_count = int(stats.get("subscriberCount", 0))
                    if sub_count < MIN_SUBSCRIBERS:
                        continue

                    full_description = details.get("snippet", {}).get("description", description)
                    custom_url = details.get("snippet", {}).get("customUrl", "")

                    # Check relevance
                    combined_text = f"{channel_title} {full_description}"
                    if not is_charcoal_relevant(combined_text):
                        continue

                    # Try to extract email from description
                    email = extract_email_from_text(full_description)
                    country = self._parse_country_from_channel(details)
                    channel_url = f"https://youtube.com/@{custom_url.lstrip('@')}" if custom_url else \
                                  f"https://youtube.com/channel/{channel_id}"

                    lead = self._make_lead(
                        company=channel_title,
                        email=email or "",
                        country=country,
                        social_handle=custom_url or channel_id,
                        social_url=channel_url,
                        social_followers=sub_count,
                        social_bio=full_description[:500],
                        discovery_context=f"YouTube search: {query}",
                        notes=f"YouTube channel | {sub_count:,} subscribers | Matched: {query}",
                    )
                    leads.append(lead)
                    logger.debug(f"YouTube lead: {channel_title} ({sub_count:,} subs) [{country}]")

                except Exception as e:
                    logger.warning(f"Error processing YouTube channel item: {e}")
                    continue

        logger.info(f"YouTube scraper: {len(leads)} relevant channels found")
        return leads
