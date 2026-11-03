"""
Standalone scraper for Google Maps *shared lists* (e.g. "arang UAE").

Not part of the price-scraping pipeline in this package — this pulls a
business directory (name/category/rating/address/phone/website) from a
Google Maps list link and dumps it to Excel, for lead/competitor research.

Usage:
    python scrapers/gmaps_list_scraper.py "<google maps share link>" [output.xlsx]

Notes:
- Google Maps list contents are loaded client-side (no public API for shared
  lists), so this drives a real Chromium via Playwright instead of curl/requests.
- List entries are JS-driven buttons, not <a href> links. Runs in two passes:
    1) click through the list to collect each place's canonical /maps/place/
       URL (fast, no scraping)
    2) goto() each URL directly and scrape the detail panel
  Doing it in two passes avoids a real bug from pass-1-only scraping: after
  clicking, Google Maps' SPA keeps the *previous* place's <h1> in the DOM
  for a moment before swapping in the new one, so scraping immediately after
  click can silently record the wrong name for a place. goto() forces a full
  navigation, so the DOM always matches the URL being scraped.
- Selectors are Google Maps' current (2026) DOM class names, which are
  unstable and can break if Google changes their frontend build.
"""

import re
import sys
import time
import random
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Windows console defaults to cp1252, which can't print Arabic/CJK/etc.
# place names that show up in this list -- force UTF-8 stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ITEM_SELECTOR = "div.BsJqK"
COORD_RE_AT = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")
COORD_RE_3D4D = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")


def force_en(url: str) -> str:
    """Force Google's UI language to English so address/country text is
    consistent and parseable (otherwise it follows request geo/IP and can
    come back in Indonesian, Arabic, etc.)."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}hl=en"

_SCROLL_JS = """() => {
    const items = document.querySelectorAll('div.BsJqK');
    if (!items.length) return false;
    let el = items[items.length - 1];
    while (el && el !== document.body) {
        const style = getComputedStyle(el);
        if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) {
            el.scrollTop = el.scrollHeight;
            return true;
        }
        el = el.parentElement;
    }
    return false;
}"""


def _accept_consent(page):
    for text in ("Accept all", "I agree", "Reject all", "Terima semua"):
        try:
            btn = page.get_by_role("button", name=text, exact=False)
            if btn.count() and btn.first.is_visible(timeout=1000):
                btn.first.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def open_list(page, list_url: str) -> None:
    page.goto(force_en(list_url), wait_until="domcontentloaded", timeout=30000)
    _accept_consent(page)
    page.wait_for_selector(ITEM_SELECTOR, timeout=20000)


def scroll_to_load_all(page) -> int:
    stagnant_rounds = 0
    last_count = -1
    while stagnant_rounds < 5:
        count = page.locator(ITEM_SELECTOR).count()
        if count == last_count:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        last_count = count
        page.evaluate(_SCROLL_JS)
        page.wait_for_timeout(700)
    return last_count


def collect_place_urls(page, list_url: str) -> list[str | None]:
    """Pass 1: click through every list entry, record its canonical place URL."""
    open_list(page, list_url)
    total = scroll_to_load_all(page)
    print(f"Found {total} places in list")

    urls: list[str | None] = []
    for i in range(total):
        items = page.locator(ITEM_SELECTOR)
        if items.count() <= i:
            scroll_to_load_all(page)
            items = page.locator(ITEM_SELECTOR)
        if items.count() <= i:
            print(f"[{i + 1}/{total}] item missing from list even after rescroll, skipping")
            urls.append(None)
            continue

        prev_url = page.url
        try:
            items.nth(i).click(timeout=5000)
            page.wait_for_function(
                "(prev) => window.location.href !== prev",
                arg=prev_url, timeout=10000,
            )
            urls.append(page.url)
            print(f"[{i + 1}/{total}] {page.url[:100]}")
        except Exception as exc:
            print(f"[{i + 1}/{total}] !! click/nav failed: {exc}")
            urls.append(None)

        try:
            page.go_back(wait_until="domcontentloaded", timeout=15000)
            page.wait_for_selector(ITEM_SELECTOR, timeout=15000)
        except PWTimeout:
            open_list(page, list_url)
        scroll_to_load_all(page)  # top back up in case scroll state was lost

        time.sleep(random.uniform(0.4, 0.9))

    return urls


def _text_or_none(page, selector: str) -> str | None:
    try:
        loc = page.locator(selector).first
        if loc.count() and loc.is_visible(timeout=800):
            return loc.inner_text(timeout=800).strip()
    except Exception:
        pass
    return None


def scrape_place_url(page, url: str | None) -> dict:
    row = {
        "name": None, "category": None, "rating": None, "review_count": None,
        "address": None, "phone": None, "website": None,
        "lat": None, "lng": None, "maps_url": url,
    }
    if not url:
        return row

    try:
        page.goto(force_en(url), wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector("h1.DUwDvf", timeout=15000)
        page.wait_for_timeout(300)
    except PWTimeout:
        return row

    row["name"] = _text_or_none(page, "h1.DUwDvf")
    row["category"] = _text_or_none(page, "button.DkEaL")

    rating_block = page.locator("div.F7nice").first
    try:
        if rating_block.count():
            spans = rating_block.locator("span[aria-hidden='true']")
            if spans.count():
                row["rating"] = spans.first.inner_text(timeout=800).strip()
            review_span = rating_block.locator("span[role='img']").last
            if review_span.count():
                label = review_span.get_attribute("aria-label") or ""
                m = re.search(r"([\d,.]+)", label)
                if m:
                    row["review_count"] = m.group(1).replace(",", "").replace(".", "")
    except Exception:
        pass

    row["address"] = _text_or_none(page, "button[data-item-id='address'] div.Io6YTe")
    row["phone"] = _text_or_none(page, "button[data-item-id^='phone:tel:'] div.Io6YTe")

    try:
        site_el = page.locator("a[data-item-id='authority']").first
        if site_el.count():
            row["website"] = site_el.get_attribute("href")
    except Exception:
        pass

    final_url = page.url
    m = COORD_RE_3D4D.search(final_url) or COORD_RE_AT.search(final_url)
    if m:
        row["lat"], row["lng"] = m.group(1), m.group(2)
    row["maps_url"] = final_url

    return row


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    list_urls = [a for a in args if a.startswith("http")]
    non_urls = [a for a in args if not a.startswith("http")]
    if not list_urls:
        print(__doc__)
        sys.exit(1)

    out_path = Path(non_urls[0]) if non_urls else Path(__file__).resolve().parent.parent / "data" / "gmaps_list_export.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()

        for list_i, list_url in enumerate(list_urls, 1):
            print(f"\n=== List {list_i}/{len(list_urls)}: {list_url} ===")
            print(f"Pass 1/2 - collecting place URLs...")
            urls = collect_place_urls(page, list_url)

            print(f"Pass 2/2 - scraping {len(urls)} place pages...")
            for i, url in enumerate(urls, 1):
                row = scrape_place_url(page, url)
                row["source_list_url"] = list_url
                if row["name"]:
                    print(f"[{i}/{len(urls)}] -> {row['name']}")
                else:
                    print(f"[{i}/{len(urls)}] !! failed to extract")
                all_rows.append(row)
                time.sleep(random.uniform(0.8, 1.6))

        browser.close()

    df = pd.DataFrame(all_rows)
    df.to_excel(out_path, index=False)

    ok = df["name"].notna().sum() if "name" in df else 0
    dupes = df["maps_url"].duplicated().sum() if "maps_url" in df else 0
    print(f"\nSaved {len(df)} rows from {len(list_urls)} list(s) "
          f"({ok} with a name extracted, {len(df) - ok} failed, "
          f"{dupes} duplicate URLs) -> {out_path}")


if __name__ == "__main__":
    main()
