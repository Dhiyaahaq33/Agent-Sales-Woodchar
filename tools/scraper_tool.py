"""B2B directory scrapers — no login required, rate-limited."""
import logging
import random
import time

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

try:
    from fake_useragent import UserAgent
    _ua = UserAgent()
    def _random_ua() -> str:
        return _ua.random
except Exception:
    def _random_ua() -> str:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )


def _get_browser_html(url: str, timeout: int = 20) -> str | None:
    """Fallback fetch via headless Chromium (Playwright) — bypasses basic bot-detection
    (403/405 from plain requests) that blocks non-browser User-Agents."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=_random_ua())
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        logger.debug("Playwright fallback failed for %s: %s", url, exc)
        return None


def _get(url: str, timeout: int = 12) -> BeautifulSoup | None:
    """Rate-limited GET with rotating User-Agent. Falls back to headless browser
    on 403/405 (bot-detection block). Returns soup or None."""
    time.sleep(random.uniform(config.SCRAPER_DELAY_MIN, config.SCRAPER_DELAY_MAX))
    try:
        resp = requests.get(url, headers={"User-Agent": _random_ua()}, timeout=timeout)
        if resp.status_code in (403, 405):
            logger.info("Blocked (%d) on %s — retrying via headless browser...", resp.status_code, url)
            html = _get_browser_html(url)
            if html:
                return BeautifulSoup(html, "lxml")
            return None
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        logger.warning("Scrape failed %s: %s", url, exc)
        return None


def scrape_tradeindia(max_pages: int = 3) -> list[dict]:
    """Scrape charcoal importer listings from TradeIndia."""
    leads: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"https://www.tradeindia.com/importers/charcoal/{page}/"
        soup = _get(url)
        if not soup:
            continue
        for card in soup.select(".company-detail, .company-info, .dir-info"):
            name = card.select_one(".company-name, h2, h3, .cname")
            country = card.select_one(".country, .location, .cloc")
            href = card.select_one("a[href*='http']")
            email_a = card.select_one("a[href^='mailto:']")
            if not name:
                continue
            leads.append({
                "company_name": name.get_text(strip=True),
                "country": country.get_text(strip=True) if country else "",
                "website": href["href"] if href and href.get("href") else "",
                "email": email_a["href"].replace("mailto:", "") if email_a else "",
                "source": url,
            })
    logger.info("TradeIndia: %d leads", len(leads))
    return leads


def scrape_exporthub(max_pages: int = 3) -> list[dict]:
    """Scrape buyer RFQs from ExportHub."""
    leads: list[dict] = []
    for keyword in ["charcoal", "bbq+charcoal", "wood+charcoal", "industrial+charcoal"]:
        url = f"https://www.exporthub.com/buyer-list/?q={keyword}"
        soup = _get(url)
        if not soup:
            continue
        for item in soup.select(".buyer-item, .rfq-item, .company-card"):
            name = item.select_one(".company-name, .buyer-name, h3, h2")
            country = item.select_one(".country, .location, .flag-text")
            if not name:
                continue
            leads.append({
                "company_name": name.get_text(strip=True),
                "country": country.get_text(strip=True) if country else "",
                "website": "",
                "email": "",
                "source": url,
                "industry": keyword.replace("+", " "),
            })
    logger.info("ExportHub: %d leads", len(leads))
    return leads


def scrape_kompass(max_pages: int = 2) -> list[dict]:
    """Scrape Kompass company directory."""
    leads: list[dict] = []
    url = "https://us.kompass.com/searchCompany?text=charcoal+importer"
    soup = _get(url)
    if not soup:
        return leads
    for card in soup.select(".company-card, .company-result, .k-result"):
        name = card.select_one(".company-name, h2, h3, .name")
        country = card.select_one(".country, .location")
        site = card.select_one("a.website, a[href*='http']")
        if not name:
            continue
        leads.append({
            "company_name": name.get_text(strip=True),
            "country": country.get_text(strip=True) if country else "",
            "website": site.get("href", "") if site else "",
            "email": "",
            "source": url,
        })
    logger.info("Kompass: %d leads", len(leads))
    return leads


def scrape_google(queries: list[str] = None) -> list[dict]:
    """Use googlesearch-python to find charcoal importer websites."""
    if queries is None:
        queries = [
            "charcoal importer company contact email",
            "bbq charcoal wholesale buyer importer",
            "industrial charcoal importer Middle East",
            "charcoal import Japan South Korea buyer",
            "wood charcoal Germany Netherlands importer",
        ]
    leads: list[dict] = []
    try:
        from googlesearch import search
        _SKIP = {"google", "youtube", "wikipedia", "facebook", "twitter", "linkedin"}
        for query in queries:
            for url in search(query, num_results=10, sleep_interval=3):
                if any(s in url for s in _SKIP):
                    continue
                domain = url.split("/")[2].replace("www.", "")
                company = domain.split(".")[0].replace("-", " ").title()
                leads.append({
                    "company_name": company,
                    "website": url,
                    "email": "",
                    "country": "",
                    "source": f"Google: {query}",
                })
            time.sleep(random.uniform(3, 6))
    except Exception as exc:
        logger.warning("Google search failed: %s", exc)
    logger.info("Google search: %d leads", len(leads))
    return leads


def scrape_all() -> list[dict]:
    """Run all scrapers; return combined list."""
    combined: list[dict] = []
    for fn in (scrape_tradeindia, scrape_exporthub, scrape_kompass, scrape_google):
        try:
            combined.extend(fn())
        except Exception as exc:
            logger.error("%s failed: %s", fn.__name__, exc)
    logger.info("Total raw leads collected: %d", len(combined))
    return combined
