"""
agents/social_scrapers/instagram_scraper.py — Instagram Business Scraper
PT Cahaya Woodchar International — PRD v2.1

Uses instagrapi library with a dedicated dummy Instagram account.
Searches hashtags related to charcoal, hookah, BBQ businesses.

⚠️  Rate limit: Instagram detects bots. Use dummy account, not your real account.
⚠️  Max ~200 requests/day before soft-ban risk. Scraper self-limits.

Required env:
  INSTAGRAM_USERNAME=your_dummy_account
  INSTAGRAM_PASSWORD=your_dummy_password
  SOCIAL_INSTAGRAM_ENABLED=true
"""

import logging
import os
import time
import random
from agents.social_scrapers import (
    SocialScraperBase, is_charcoal_relevant, extract_email_from_text, CHARCOAL_HASHTAGS
)

logger = logging.getLogger(__name__)

TARGET_HASHTAGS = [
    "charcoalbbq", "hookahlounge", "shishabar", "charcoalsupplier",
    "bbqrestaurant", "hukkabar", "charcoalbusiness", "charcoalbriquette",
    "lumpcharcoal", "hookahwholesale", "charcoalgrill", "bbqsupply",
]

MAX_ACCOUNTS_PER_HASHTAG = 15
MIN_FOLLOWERS = 200
REQUEST_DELAY_SECONDS = (3, 8)  # random delay between requests


class InstagramScraper(SocialScraperBase):
    platform_name = "Instagram"
    max_results_per_run = 50

    def _login(self):
        """Login to Instagram with dummy account credentials."""
        username = os.getenv("INSTAGRAM_USERNAME", "")
        password = os.getenv("INSTAGRAM_PASSWORD", "")
        if not username or not password:
            raise ValueError("INSTAGRAM_USERNAME/PASSWORD not set in .env")

        try:
            from instagrapi import Client
        except ImportError:
            raise ImportError("instagrapi not installed. Run: pip install instagrapi")

        cl = Client()
        # Settings to reduce detection
        cl.delay_range = list(REQUEST_DELAY_SECONDS)

        session_file = os.path.join(
            os.path.dirname(__file__), f"../../db/instagram_session_{username}.json"
        )
        session_file = os.path.normpath(session_file)

        try:
            if os.path.exists(session_file):
                cl.load_settings(session_file)
                cl.login(username, password)
                logger.info(f"Instagram: loaded existing session for {username}")
            else:
                cl.login(username, password)
                cl.dump_settings(session_file)
                logger.info(f"Instagram: fresh login for {username}")
        except Exception as e:
            logger.warning(f"Instagram session load failed, trying fresh login: {e}")
            cl.login(username, password)

        return cl

    def _scrape_hashtag(self, cl, hashtag: str) -> list:
        """Get recent top posts from a hashtag and extract account info."""
        leads = []
        try:
            medias = cl.hashtag_medias_top(hashtag, amount=MAX_ACCOUNTS_PER_HASHTAG)
            seen_user_ids = set()

            for media in medias:
                try:
                    user_id = media.user.pk
                    if user_id in seen_user_ids:
                        continue
                    seen_user_ids.add(user_id)

                    # Fetch full user profile
                    time.sleep(random.uniform(*REQUEST_DELAY_SECONDS))
                    user = cl.user_info(user_id)

                    # Filter: must be business/creator account with enough followers
                    if user.follower_count < MIN_FOLLOWERS:
                        continue
                    if not user.is_business and not user.is_verified:
                        continue

                    bio = user.biography or ""
                    full_name = user.full_name or ""
                    username = user.username or ""

                    # Check relevance
                    combined = f"{full_name} {bio} #{hashtag}"
                    if not is_charcoal_relevant(combined) and not any(
                        tag.lstrip("#") in hashtag for tag in ["hookah", "shisha", "bbq", "charcoal"]
                    ):
                        continue

                    email = user.public_email or extract_email_from_text(bio) or ""
                    country = user.public_phone_country_code or ""
                    profile_url = f"https://instagram.com/{username}"

                    lead = self._make_lead(
                        company=full_name or username,
                        email=email,
                        country=country,
                        social_handle=f"@{username}",
                        social_url=profile_url,
                        social_followers=user.follower_count,
                        social_bio=bio,
                        discovery_context=f"#{hashtag}",
                        notes=(
                            f"Instagram {'business' if user.is_business else 'creator'} account | "
                            f"{user.follower_count:,} followers | #{hashtag}"
                        ),
                        contact_name=full_name,
                    )
                    leads.append(lead)
                    logger.debug(f"Instagram lead: @{username} ({user.follower_count:,} followers)")

                except Exception as e:
                    logger.debug(f"Error processing Instagram user in #{hashtag}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Instagram hashtag scrape failed for #{hashtag}: {e}")

        return leads

    def scrape(self) -> list:
        """Login and scrape target hashtags."""
        leads = []
        try:
            cl = self._login()
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            return []

        # Rotate through hashtags with delays to avoid rate limiting
        hashtags_today = random.sample(TARGET_HASHTAGS, min(6, len(TARGET_HASHTAGS)))
        logger.info(f"Instagram: scraping {len(hashtags_today)} hashtags")

        for hashtag in hashtags_today:
            logger.debug(f"Instagram: scraping #{hashtag}")
            batch = self._scrape_hashtag(cl, hashtag)
            leads.extend(batch)
            # Longer pause between hashtags
            time.sleep(random.uniform(10, 20))
            if len(leads) >= self.max_results_per_run:
                break

        logger.info(f"Instagram scraper: {len(leads)} leads found")
        return leads
