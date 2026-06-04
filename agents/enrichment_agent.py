"""
agents/enrichment_agent.py — Deep Enrichment Agent v3.0
PT Cahaya Woodchar International

Upgrades from v2.0:
  - LLM-based website content analysis (industry, company size, import signals)
  - Extended keyword scoring with weighted categories
  - Social bio enrichment (for social-sourced leads)
  - Country risk pre-check (skip BLOCKED countries)
  - send_window_start stored per lead based on country
  - VectorMemory indexing of enriched leads
  - lead_enrichment table for detailed data
  - Score components stored for transparency
"""
import json
import logging
import re
from pathlib import Path

import requests

import db.database as db
import config
from tools.llm_router import call as llm_call
from tools.send_time_optimizer import SendTimeOptimizer
from tools.country_risk import get_country_risk

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")

# Keyword categories with weights
_KEYWORD_SCORES = {
    "charcoal_direct": {
        "keywords": {"charcoal", "coconut shell charcoal", "lump charcoal", "briquette",
                     "activated carbon", "hookah charcoal", "shisha coal", "bbq charcoal"},
        "score": 30,
    },
    "related_industry": {
        "keywords": {"bbq", "grilling", "barbecue", "shisha", "hookah", "biomass",
                     "carbon", "wood fuel", "restaurant supply", "food service"},
        "score": 20,
    },
    "import_signals": {
        "keywords": {"import", "importer", "wholesale", "distributor", "buyer",
                     "trading", "supply", "procurement", "sourcing", "international"},
        "score": 20,
    },
    "business_signals": {
        "keywords": {"company", "ltd", "llc", "gmbh", "co.", "corp", "enterprise",
                     "export", "global", "international", "group"},
        "score": 10,
    },
    "contact_present": {
        "keywords": {"contact us", "our team", "ceo", "director", "manager",
                     "email us", "call us", "send inquiry"},
        "score": 10,
    },
}

_LLM_ENRICHMENT_PROMPT = """Analyze this website text from a potential charcoal buyer and extract business intelligence.

Company: {company}
Country: {country}
Website text (first 2000 chars):
{text}

Return ONLY valid JSON:
{{
  "company_size": "micro|small|medium|large|unknown",
  "primary_business": "one sentence describing what they do",
  "import_probability": 0.0-1.0,
  "charcoal_relevance": 0.0-1.0,
  "contact_title": "CEO|Purchasing Manager|Import Manager|Owner|Other|Unknown",
  "import_signals": ["list", "of", "relevant", "phrases", "found"],
  "product_mentions": ["products", "they", "deal", "with"],
  "estimated_volume": "small(<20MT)|medium(20-100MT)|large(100MT+)|unknown",
  "recommended_approach": "one sentence on how to pitch to this buyer"
}}"""


class EnrichmentAgent:
    def __init__(self):
        self._sto = SendTimeOptimizer()
        self._cr = get_country_risk()

    def run(self) -> dict:
        leads = db.get_leads_by_status("NEW")
        logger.info("[EnrichmentAgent] Processing %d NEW leads...", len(leads))

        enriched = dead = blocked = 0

        for lead in leads:
            try:
                result = self._enrich_lead(lead)
                if result["blocked"]:
                    blocked += 1
                elif result["status"] == "ENRICHED":
                    enriched += 1
                else:
                    dead += 1
            except Exception as e:
                logger.error("Enrichment failed for lead %d: %s", lead.get("id"), e)
                dead += 1

        logger.info(
            "[EnrichmentAgent] Done — enriched=%d, dead=%d, blocked=%d",
            enriched, dead, blocked
        )
        return {"enriched": enriched, "dead": dead, "blocked": blocked}

    def _enrich_lead(self, lead: dict) -> dict:
        """Full enrichment pipeline for a single lead."""
        lead_id = lead["id"]
        company = lead.get("company") or lead.get("company_name", "")
        country = lead.get("country", "")

        # ── Step 0: OFAC/country risk check ────────────────────────────
        if self._cr.is_blocked(country):
            self._cr.update_lead_risk(lead_id, country)
            logger.warning("BLOCKED: %s (%s)", company, country)
            return {"status": "DEAD", "blocked": True}

        risk_info = self._cr.get_risk(country)
        risk_level = risk_info.get("risk", "")

        score = 0
        score_components = {}

        # ── Step 1: Email validation ────────────────────────────────────
        email = lead.get("email", "")
        if email and _EMAIL_RE.match(email.strip()):
            score += 20
            score_components["email_valid"] = 20

        # ── Step 2: Social lead enrichment (no website needed) ──────────
        social_score = self._score_social_signals(lead)
        score += social_score
        if social_score > 0:
            score_components["social_signals"] = social_score

        # ── Step 3: Website enrichment ──────────────────────────────────
        website = lead.get("website", "")
        enrichment_data = {}
        if website:
            site_text = self._scrape_site(website)
            if site_text:
                keyword_score, hit_categories = self._score_keywords(site_text)
                score += keyword_score
                score_components.update(hit_categories)

                # LLM deep enrichment (only if some keywords hit)
                if keyword_score >= 20:
                    enrichment_data = self._llm_enrich(company, country, site_text)
                    if enrichment_data:
                        # Boost score based on LLM analysis
                        import_prob = enrichment_data.get("import_probability", 0)
                        relevance = enrichment_data.get("charcoal_relevance", 0)
                        llm_boost = int((import_prob + relevance) * 15)
                        score += llm_boost
                        score_components["llm_boost"] = llm_boost

        # ── Step 4: Set optimal send window ────────────────────────────
        send_start = self._sto.get_best_hour(country)

        # ── Step 5: Finalize ────────────────────────────────────────────
        score = min(score, 100)
        status = "ENRICHED" if score >= config.LEAD_SCORE_THRESHOLD else "DEAD"
        notes = json.dumps(score_components)

        update_kwargs = {
            "score": score,
            "notes": notes,
            "status": status,
            "country_risk": risk_level,
            "send_window_start": send_start,
            "send_window_end": send_start + 4,
        }

        db.update_lead(lead_id, **update_kwargs)

        # ── Step 6: Save detailed enrichment to lead_enrichment table ──
        if enrichment_data:
            self._save_enrichment(lead_id, enrichment_data, site_text if website else "")

        # ── Step 7: Index in VectorMemory ───────────────────────────────
        if status == "ENRICHED":
            try:
                from tools.vector_memory import get_vector_memory
                fresh = db.get_lead_by_id(lead_id)
                if fresh:
                    get_vector_memory().upsert_lead(fresh)
            except Exception as e:
                logger.debug("VectorMemory index failed: %s", e)

        logger.debug(
            "Lead %d (%s) → %s | score=%d | components=%s",
            lead_id, company, status, score, score_components
        )
        return {"status": status, "score": score, "blocked": False}

    def _score_social_signals(self, lead: dict) -> int:
        """Score a social-sourced lead based on its social profile data."""
        score = 0
        bio = (lead.get("social_bio") or "").lower()
        platform = lead.get("social_platform") or ""
        followers = lead.get("social_followers") or 0
        discovery = (lead.get("discovery_context") or "").lower()

        combined = f"{bio} {discovery}"
        for cat_name, cat in _KEYWORD_SCORES.items():
            if any(kw in combined for kw in cat["keywords"]):
                score += cat["score"] // 2  # Half weight for social vs website

        # Follower count signal (more followers = more established business)
        if followers >= 10000:
            score += 15
        elif followers >= 1000:
            score += 10
        elif followers >= 500:
            score += 5

        # YouTube has email in description more often
        if platform == "YouTube" and lead.get("email"):
            score += 10

        return min(score, 40)  # Cap social score at 40

    def _scrape_site(self, url: str) -> str:
        """Scrape website text content."""
        if not url:
            return ""
        if not url.startswith("http"):
            url = "https://" + url
        try:
            from bs4 import BeautifulSoup
            r = requests.get(
                url, timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ChatBot/1.0)"},
                allow_redirects=True,
            )
            if r.status_code >= 400:
                return ""
            soup = BeautifulSoup(r.text, "lxml")
            # Remove scripts and styles
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True).lower()
            return text[:5000]
        except Exception as e:
            logger.debug("Site scrape failed for %s: %s", url[:60], e)
            return ""

    def _score_keywords(self, text: str) -> tuple:
        """Score text by keyword categories. Returns (total_score, {category: score})."""
        total = 0
        components = {}
        for cat_name, cat in _KEYWORD_SCORES.items():
            if any(kw in text for kw in cat["keywords"]):
                total += cat["score"]
                components[cat_name] = cat["score"]
        return total, components

    def _llm_enrich(self, company: str, country: str, text: str) -> dict:
        """Use LLM to extract structured enrichment from website text."""
        try:
            prompt = _LLM_ENRICHMENT_PROMPT.format(
                company=company,
                country=country,
                text=text[:2000],
            )
            raw = llm_call(prompt, expect_json=True)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("```")
            return json.loads(raw)
        except Exception as e:
            logger.debug("LLM enrichment failed: %s", e)
            return {}

    def _save_enrichment(self, lead_id: int, data: dict, site_text: str):
        """Save detailed enrichment data to lead_enrichment table."""
        try:
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO lead_enrichment
                       (lead_id, company_size_est, import_signals, product_mentions,
                        contact_title, website_desc, enriched_at)
                       VALUES (?,?,?,?,?,?,datetime('now'))""",
                    (
                        lead_id,
                        data.get("company_size", "unknown"),
                        json.dumps(data.get("import_signals", [])),
                        json.dumps(data.get("product_mentions", [])),
                        data.get("contact_title", ""),
                        data.get("primary_business", "")[:500],
                    )
                )
        except Exception as e:
            logger.debug("Could not save lead_enrichment: %s", e)
