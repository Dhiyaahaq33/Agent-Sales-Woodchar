"""
agents/outreach_agent.py — Outreach Agent v3.0
PT Cahaya Woodchar International

Upgrades from v2.0:
  - Warmup manager: respects daily send limits (20→200/day progressive)
  - Send time optimizer: skips sends outside buyer's business hours
  - Offering master template: uses templates/offering_master.txt as base
  - Vector memory context: enriches prompts with similar successful leads
  - Email tracker: injects Bit.ly links + tracking pixel
  - Gmail rotator: distributes sends across multiple Gmail accounts
  - OFAC check: blocks sends to sanctioned countries
  - A/B subject line selection: uses subject_lines.json per segment
  - Deliverability check: domain rate limit + blacklist before sending
"""
import json
import logging
import random
import time
from pathlib import Path

import db.database as db
import config
from tools.llm_router import call as llm_call
from tools.warmup_manager import get_warmup_manager
from tools.send_time_optimizer import SendTimeOptimizer
from tools.gmail_rotator import get_gmail_rotator
from tools.email_tracker import get_email_tracker

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "product_catalog.json"
_OFFERING_MASTER_PATH = Path(__file__).parent.parent / "templates" / "offering_master.txt"
_SUBJECT_LINES_PATH = Path(__file__).parent.parent / "templates" / "subject_lines.json"


def _load_catalog_summary() -> str:
    try:
        catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        items = catalog.get("products", [])
        return "\n".join(
            f"- {p['name']} (ID:{p['product_id']}): FC {p.get('fixed_carbon_pct', '')}%, "
            f"MOQ {p.get('moq_mt', '')} MT, Price {p.get('price_usd_per_mt', 'TBD')} USD/MT"
            for p in items
        )
    except Exception as exc:
        logger.warning("Could not load catalog: %s", exc)
        return "Halaban Lump, Coconut Shell, BBQ Briquette, Mix Hardwood — MOQ 20 MT."


def _load_offering_master() -> str:
    """Load the proven email template as reference."""
    try:
        return _OFFERING_MASTER_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_subject_lines() -> dict:
    """Load A/B subject line pairs."""
    try:
        return json.loads(_SUBJECT_LINES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_segment(lead: dict) -> str:
    """Determine which subject line segment fits this lead."""
    country = (lead.get("country") or "").upper()
    notes = (lead.get("notes") or "").lower()
    social_bio = (lead.get("social_bio") or "").lower()
    discovery = (lead.get("discovery_context") or "").lower()
    combined = f"{notes} {social_bio} {discovery}"

    if any(kw in combined for kw in ["hookah", "shisha", "hukka", "narghile"]):
        return "shisha_hookah"
    if any(kw in combined for kw in ["bbq", "barbecue", "grill", "steakhouse", "restaurant"]):
        if country in ("JP", "KR"):
            return "japan_korea_premium"
        if country in ("AU", "NZ"):
            return "australia_bbq"
        return "bbq_restaurant"
    if country in ("DE", "NL", "FR", "GB", "SE", "NO", "DK"):
        return "europe_eco"
    if country in ("JP", "KR"):
        return "japan_korea_premium"
    if country in ("AU",):
        return "australia_bbq"
    if country in ("IN",):
        return "india_volume"
    if any(kw in combined for kw in ["wholesale", "distributor", "trading", "import"]):
        return "distributor_trader"
    social_platform = (lead.get("social_platform") or "").lower()
    if social_platform == "instagram":
        return "social_instagram"
    if social_platform == "linkedin":
        return "social_linkedin"
    if social_platform in ("twitter", "x"):
        return "social_twitter"
    return "general_importer"


def _pick_subject(lead: dict, subject_lines: dict, use_variant_b: bool = False) -> str:
    """Pick an A/B subject line for this lead's segment."""
    segment = _classify_segment(lead)
    data = subject_lines.get(segment, subject_lines.get("general_importer", {}))
    variant = "B" if use_variant_b else "A"
    subject_list = data.get(variant, [])
    if subject_list:
        return random.choice(subject_list)
    # Fallback
    return "Charcoal Supply from Indonesia — PT Cahaya Woodchar"


_PROMPT_TEMPLATE = """You are Muhammad Yahya, COO of PT Cahaya Woodchar International.
Write a personalized cold email to a potential charcoal buyer.

Buyer company: {company}
Country: {country}
Segment: {segment}
Lead source: {source}
Lead notes: {notes}

Reference template (keep structure, personalize greeting + opening hook):
{template}

Our products:
{catalog}

{vector_context}

Rules (STRICTLY FOLLOW):
1. Keep the Muhammad Yahya signature, WhatsApp numbers, website EXACTLY as in template
2. ONLY personalize: greeting, first 2 sentences (the opening hook)
3. Use their company name and country naturally in the hook
4. Maximum 200 words total
5. Do NOT mention pricing — invite them to request a quote
6. Keep visual separators (────────────────) in template
7. Output ONLY the email body (no subject line, no explanation)"""


class OutreachAgent:
    def __init__(self):
        self._warmup = get_warmup_manager()
        self._send_time = SendTimeOptimizer()
        self._rotator = get_gmail_rotator()
        self._tracker = get_email_tracker()
        self._subject_lines = _load_subject_lines()
        self._offering_master = _load_offering_master()
        self._catalog = _load_catalog_summary()

    def run(self) -> dict:
        # Check warmup limit
        if not self._warmup.can_send():
            wm_status = self._warmup.get_status()
            logger.info(
                "[OutreachAgent] Warmup limit reached: %d/%d today",
                wm_status["sent_today"], wm_status["daily_limit"]
            )
            return {"sent": 0, "failed": 0, "limit_reached": True}

        # Check deliverability pause
        try:
            from tools.deliverability import is_paused_for_bounces
            if is_paused_for_bounces():
                logger.warning("[OutreachAgent] PAUSED due to high bounce rate")
                return {"sent": 0, "failed": 0, "bounce_pause": True}
        except ImportError:
            pass

        leads = db.get_leads_by_status("ENRICHED")
        qualified = [l for l in leads if l.get("score", 0) >= config.LEAD_SCORE_THRESHOLD]

        # OFAC filter
        qualified = self._filter_blocked_countries(qualified)

        # Sort by optimal send time (countries in business hours first)
        qualified = self._send_time.get_optimal_batch_order(qualified)

        limit = self._warmup.get_limit()
        sent_today = db.count_sent_today()
        remaining = limit - sent_today

        logger.info(
            "[OutreachAgent] %d qualified leads, %d/%d slots used today",
            len(qualified), sent_today, limit
        )

        sent = failed = skipped_time = 0

        for lead in qualified[:remaining]:
            if not lead.get("email"):
                failed += 1
                continue

            # Deliverability checks
            try:
                from tools.deliverability import is_blacklisted, check_domain_rate_limit
                if is_blacklisted(lead["email"]):
                    logger.info("Blacklisted: %s", lead["email"])
                    failed += 1
                    continue
                if not check_domain_rate_limit(lead["email"]):
                    logger.debug("Domain rate limited: %s", lead["email"])
                    skipped_time += 1
                    continue
            except ImportError:
                pass

            # Send time check (skip if outside business hours for this country)
            country = lead.get("country", "")
            if not self._send_time.should_send_now(country):
                logger.debug(
                    "Skip (outside send window): %s (%s)",
                    lead.get("company") or lead.get("company_name"), country
                )
                skipped_time += 1
                continue

            # Generate personalized email
            body = self._generate_email(lead)
            if not body:
                failed += 1
                continue

            # Pick subject line (A/B test assignment)
            from tools.ab_tester import assign_variant
            use_b = assign_variant(lead["id"]) == "B"
            subject = _pick_subject(lead, self._subject_lines, use_variant_b=use_b)

            # Inject tracking
            tracking_url = self._tracker.inject_tracked_links(body, outreach_id=0)
            pixel_id = ""

            # Send via Gmail rotator
            success = self._rotator.send_email(
                to=lead["email"],
                subject=subject,
                body=body,
            )

            if success:
                # Log to DB
                outreach_id = db.log_outreach(
                    lead_id=lead["id"],
                    touch_number=1,
                    subject=subject,
                    body=body[:500],
                )
                # Inject tracking pixel (now that we have outreach_id)
                if outreach_id:
                    pixel_id, _ = self._tracker.inject_tracking(body, outreach_id, lead["id"])
                    if pixel_id:
                        try:
                            with db.get_conn() as conn:
                                conn.execute(
                                    "UPDATE outreach SET pixel_id=? WHERE id=?",
                                    (pixel_id, outreach_id)
                                )
                        except Exception:
                            pass

                db.update_lead(lead["id"], status="OUTREACH")
                sent += 1

                company = lead.get("company") or lead.get("company_name", "")
                logger.info("✉️  Sent → %s (%s) | %s", lead["email"], company, subject)

                # Update VectorMemory
                try:
                    from tools.vector_memory import get_vector_memory
                    lead_fresh = db.get_lead_by_id(lead["id"])
                    if lead_fresh:
                        get_vector_memory().upsert_lead(lead_fresh)
                except Exception:
                    pass
            else:
                failed += 1

            # Random delay
            time.sleep(random.uniform(config.EMAIL_DELAY_MIN, config.EMAIL_DELAY_MAX))

        logger.info(
            "[OutreachAgent] Done — sent=%d, failed=%d, time_skipped=%d",
            sent, failed, skipped_time
        )
        return {"sent": sent, "failed": failed, "time_skipped": skipped_time}

    def _generate_email(self, lead: dict) -> str:
        """Generate personalized email body using LLM + offering master template."""
        company = lead.get("company") or lead.get("company_name", "")
        country = lead.get("country", "their market")
        segment = _classify_segment(lead)
        source = lead.get("source", "")
        notes = lead.get("notes", "")

        # Get vector memory personalization context
        vector_context = ""
        try:
            from tools.vector_memory import get_vector_memory
            vm = get_vector_memory()
            query = f"{company} {country} charcoal {segment}"
            ctx = vm.get_personalization_context(query)
            if ctx:
                vector_context = f"Similar successful leads for reference:\n{ctx}"
        except Exception:
            pass

        prompt = _PROMPT_TEMPLATE.format(
            company=company,
            country=country,
            segment=segment,
            source=source,
            notes=notes[:200],
            template=self._offering_master[:2000] if self._offering_master else
                     "Professional charcoal export offer email",
            catalog=self._catalog[:1000],
            vector_context=vector_context,
        )

        try:
            body = llm_call(prompt)
            return body.strip()
        except Exception as exc:
            logger.error("LLM email generation failed for lead %d: %s", lead.get("id"), exc)
            return ""

    def _filter_blocked_countries(self, leads: list) -> list:
        """Remove leads from OFAC-blocked countries."""
        try:
            from tools.country_risk import get_country_risk
            cr = get_country_risk()
            filtered = []
            for lead in leads:
                country = lead.get("country", "")
                if cr.is_blocked(country):
                    logger.warning(
                        "Skipping BLOCKED country lead: %s (%s)",
                        lead.get("company") or lead.get("company_name"), country
                    )
                    db.update_lead(lead["id"], status="DEAD",
                                   notes=f"BLOCKED country: {country}")
                else:
                    # Update country_risk column
                    risk_info = cr.get_risk(country)
                    db.update_lead(lead["id"], country_risk=risk_info.get("risk", ""))
                    filtered.append(lead)
            return filtered
        except Exception:
            return leads
