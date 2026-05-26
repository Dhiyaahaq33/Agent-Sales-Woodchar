"""Enrichment Agent — verifies and scores NEW leads before outreach."""
import logging
import re

import requests

import db.database as db
import config

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")

_CHARCOAL_KW = {
    "charcoal", "barbeque", "bbq", "grilling", "charbon", "holzkohle",
    "carbon", "coal", "briquette", "wood fuel", "biomass",
}
_IMPORT_KW = {
    "import", "importer", "wholesale", "distributor", "buyer",
    "trading", "supply", "procurement", "sourcing",
}


def _email_valid(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def _site_reachable(url: str) -> bool:
    if not url:
        return False
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = requests.head(url, timeout=5, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


def _scrape_site_text(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "lxml")
        return soup.get_text(" ", strip=True).lower()
    except Exception:
        return ""


def _keyword_hit(text: str, keywords: set) -> bool:
    return any(kw in text for kw in keywords)


class EnrichmentAgent:
    """Reads NEW leads, scores them, updates status to ENRICHED or DEAD."""

    def run(self) -> dict:
        """Process all leads with status=NEW."""
        leads = db.get_leads_by_status("NEW")
        logger.info("[EnrichmentAgent] Processing %d NEW leads...", len(leads))

        enriched = 0
        dead = 0
        for lead in leads:
            score = 0
            notes = []

            if _email_valid(lead.get("email", "")):
                score += 20
                notes.append("email_valid")

            site_live = _site_reachable(lead.get("website", ""))
            if site_live:
                score += 15
                notes.append("site_live")

            text = _scrape_site_text(lead.get("website", ""))

            if _keyword_hit(text, _CHARCOAL_KW):
                score += 30
                notes.append("charcoal_keywords")

            if _keyword_hit(text, _IMPORT_KW):
                score += 20
                notes.append("import_keywords")

            # Crude contact-name detection
            for phrase in ("contact us", "our team", "meet our", "ceo", "director"):
                if phrase in text:
                    score += 15
                    notes.append("contact_found")
                    break

            update = {
                "score": min(score, 100),
                "notes": ", ".join(notes),
                "status": "ENRICHED" if score >= config.LEAD_SCORE_THRESHOLD else "DEAD",
            }
            db.update_lead(lead["id"], **update)

            if update["status"] == "ENRICHED":
                enriched += 1
            else:
                dead += 1
                logger.debug(
                    "Lead %d (%s) scored %d — marked DEAD",
                    lead["id"], lead["company_name"], score,
                )

        logger.info(
            "[EnrichmentAgent] Done — enriched=%d, dead=%d", enriched, dead
        )
        return {"enriched": enriched, "dead": dead}
