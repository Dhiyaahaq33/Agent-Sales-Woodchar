"""
agents/social_scrapers/tiktok_scraper.py — TikTok Business Account Scraper
PT Cahaya Woodchar International — PRD v2.1

Uses TikTokApi (unofficial) to find business accounts posting charcoal,
hookah, BBQ content. Focus on accounts with shop links or bio emails.

⚠️  TikTokApi requires Playwright browsers installed:
    python -m playwright install chromium

⚠️  TikTok aggressively detects bots. Run max once per day, random hour.

Required env:
  SOCIAL_TIKTOK_ENABLED=true
"""

import logging
import os
import time
import random
import asyncio
from agents.social_scrapers import SocialScraperBase, is_charcoal_relevant, extract_email_from_text

logger = logging.getLogger(__name__)

SEARCH_HASHTAGS = [
    "hookahabusiness",
    "charcoalsupplier",
    "shishabar",
    "bbqbusiness",
    "charcoalbbq",
    "hukkabar",
    "charcoalbriquette",
    "hookahlounge",
]

MIN_FOLLOWERS = 500
MAX_VIDEOS_PER_HASHTAG = 20
REQUEST_DELAY = (2, 6)


class TikTokScraper(SocialScraperBase):
    platform_name = "TikTok"
    max_results_per_run = 40

    def scrape(self) -> list:
        """Run async TikTok scrape in sync context."""
        try:
            return asyncio.run(self._async_scrape())
        except RuntimeError:
            # Already in event loop (e.g. Jupyter) — use thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._async_scrape())
                return future.result()

    async def _async_scrape(self) -> list:
        """Main async scrape logic."""
        leads = []
        try:
            from TikTokApi import TikTokApi
        except ImportError:
            logger.error("TikTokApi not installed. Run: pip install TikTokApi && python -m playwright install chromium")
            return []

        ms_token = os.getenv("TIKTOK_MS_TOKEN", "")  # Optional: reduces detection

        async with TikTokApi() as api:
            if ms_token:
                await api.create_sessions(
                    ms_tokens=[ms_token],
                    num_sessions=1,
                    sleep_after=3
                )
            else:
                await api.create_sessions(num_sessions=1, sleep_after=3)

            seen_user_ids = set()
            hashtags_today = random.sample(SEARCH_HASHTAGS, min(4, len(SEARCH_HASHTAGS)))

            for hashtag in hashtags_today:
                logger.debug(f"TikTok: scraping #{hashtag}")
                try:
                    tag = api.hashtag(name=hashtag)
                    async for video in tag.videos(count=MAX_VIDEOS_PER_HASHTAG):
                        try:
                            author = video.author
                            if not author:
                                continue

                            user_id = author.user_id or author.username
                            if user_id in seen_user_ids:
                                continue
                            seen_user_ids.add(user_id)

                            # Get full user info
                            await asyncio.sleep(random.uniform(*REQUEST_DELAY))
                            user_info = await author.info()
                            if not user_info:
                                continue

                            stats = user_info.get("stats", {})
                            follower_count = stats.get("followerCount", 0)
                            if follower_count < MIN_FOLLOWERS:
                                continue

                            user_data = user_info.get("user", {})
                            bio = user_data.get("signature", "")
                            nickname = user_data.get("nickname", "")
                            username = user_data.get("uniqueId", "")
                            bio_link = user_data.get("bioLink", {})
                            website = bio_link.get("link", "") if bio_link else ""

                            combined = f"{nickname} {bio} {hashtag}"
                            if not is_charcoal_relevant(combined):
                                continue

                            email = extract_email_from_text(bio) or extract_email_from_text(website) or ""

                            lead = self._make_lead(
                                company=nickname or username,
                                email=email,
                                country="",
                                social_handle=f"@{username}",
                                social_url=f"https://tiktok.com/@{username}",
                                social_followers=follower_count,
                                social_bio=bio,
                                discovery_context=f"#{hashtag}",
                                notes=(
                                    f"TikTok | {follower_count:,} followers | "
                                    f"#{hashtag} | {website or 'no website'}"
                                ),
                                contact_name=nickname,
                            )
                            leads.append(lead)
                            logger.debug(f"TikTok lead: @{username} ({follower_count:,} followers)")

                            if len(leads) >= self.max_results_per_run:
                                break

                        except Exception as e:
                            logger.debug(f"Error processing TikTok video: {e}")
                            continue

                except Exception as e:
                    logger.warning(f"TikTok hashtag #{hashtag} failed: {e}")

                await asyncio.sleep(random.uniform(10, 20))
                if len(leads) >= self.max_results_per_run:
                    break

        logger.info(f"TikTok scraper: {len(leads)} leads found")
        return leads
