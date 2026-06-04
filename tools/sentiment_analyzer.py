"""
tools/sentiment_analyzer.py — Advanced Reply Intent & Sentiment Analyzer
PT Cahaya Woodchar International — v3.0

Goes beyond binary INTERESTED/NOT_INTERESTED to extract:
  - urgency_score: 0-10 (10 = "we need it next month")
  - price_sensitivity: LOW/MEDIUM/HIGH
  - buying_timeline: IMMEDIATE/3_MONTHS/6_MONTHS/LONG_TERM/UNCLEAR
  - competitor_mentioned: bool (they mentioned another supplier)
  - quantity_mentioned: float or None (MT mentioned in reply)
  - product_interest: list of product types mentioned
  - key_objections: list of objections to address
  - recommended_action: what the agent should do next
  - confidence: 0.0-1.0

Used by:
  - MonitorAgent: to route HIGH urgency to NegotiationAgent immediately
  - NegotiationAgent: to calibrate response tone and pricing
  - ChurnDetector: to detect declining engagement over time
  - ReporterAgent: urgency alerts in daily report

Usage:
  from tools.sentiment_analyzer import SentimentAnalyzer
  sa = SentimentAnalyzer()
  result = sa.analyze(reply_body, lead_country="JP", lead_company="Osaka BBQ Co.")
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Urgency signal patterns (before LLM, fast rule-based pre-filter)
_URGENCY_PATTERNS = [
    (r"\b(urgent|asap|immediately|right away|rush|emergency)\b", 9),
    (r"\b(this (week|month)|next (week|month))\b", 8),
    (r"\b(soon|quickly|fast|prompt)\b", 6),
    (r"\b(interested|ready to order|place an order|purchase)\b", 5),
    (r"\b(considering|evaluating|reviewing|looking into)\b", 3),
    (r"\b(not now|later|maybe|sometime|future)\b", 1),
]

_QUANTITY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:MT|metric.?ton|ton|tonnes?|container|FCL|20.?ft|40.?ft)",
    re.IGNORECASE
)

_COMPETITOR_MENTIONS = [
    "alibaba", "made in china", "chinese supplier", "vietnam", "thai",
    "africa", "other supplier", "current supplier", "already have",
    "cheaper", "better price",
]


_ANALYSIS_PROMPT = """You are an expert B2B sales analyst for PT Cahaya Woodchar International,
an Indonesian charcoal exporter. Analyze this buyer reply and extract intent signals.

Buyer: {company} ({country})
Email reply:
---
{body}
---

Return ONLY valid JSON with these exact keys:
{{
  "classification": "INTERESTED|NOT_INTERESTED|NEEDS_INFO|OOO|UNRELATED",
  "urgency_score": 0-10,
  "price_sensitivity": "LOW|MEDIUM|HIGH",
  "buying_timeline": "IMMEDIATE|3_MONTHS|6_MONTHS|LONG_TERM|UNCLEAR",
  "competitor_mentioned": true/false,
  "quantity_mentioned_mt": null or float,
  "product_interest": ["coconut shell"|"halaban"|"briquette"|"mix hardwood"|"sawdust"],
  "key_objections": ["string1", "string2"] or [],
  "intent_summary": "one sentence",
  "recommended_action": "SEND_PROPOSAL|NEGOTIATE_PRICE|ANSWER_QUESTIONS|SCHEDULE_CALL|WAIT|CLOSE",
  "confidence": 0.0-1.0,
  "requested_info": [] or null,
  "ooo_return_date": null or "YYYY-MM-DD"
}}

Scoring guide:
- urgency_score 8-10: mentions specific near-term dates, uses urgent language
- urgency_score 5-7: clearly interested, evaluating seriously
- urgency_score 1-4: vague interest, no timeline
- urgency_score 0: not interested or spam

For recommended_action:
- SEND_PROPOSAL: buyer clearly wants pricing/quotation
- NEGOTIATE_PRICE: buyer pushing on price
- ANSWER_QUESTIONS: buyer asking specific product questions
- SCHEDULE_CALL: buyer wants to talk
- WAIT: buyer is OOO or said they'll follow up
- CLOSE: buyer ready to place order"""


class SentimentAnalyzer:
    def __init__(self):
        pass

    def _rule_based_urgency(self, body: str) -> int:
        """Fast pre-filter urgency score from pattern matching."""
        text = body.lower()
        max_score = 0
        for pattern, score in _URGENCY_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                max_score = max(max_score, score)
        return max_score

    def _extract_quantity(self, body: str) -> Optional[float]:
        """Extract mentioned quantity in metric tons."""
        matches = _QUANTITY_RE.findall(body)
        if matches:
            try:
                return float(matches[0])
            except ValueError:
                pass
        return None

    def _check_competitor_mention(self, body: str) -> bool:
        """Check if buyer mentioned competitors or alternatives."""
        text = body.lower()
        return any(mention in text for mention in _COMPETITOR_MENTIONS)

    def analyze(self, body: str, lead_country: str = "", lead_company: str = "",
                subject: str = "") -> dict:
        """
        Full analysis: LLM classification + rule-based signals.
        Returns merged result dict.
        """
        # Rule-based pre-analysis (fast, no API call)
        rule_urgency = self._rule_based_urgency(body)
        quantity = self._extract_quantity(body)
        has_competitor = self._check_competitor_mention(body)

        # Default result if LLM fails
        default_result = {
            "classification": "UNRELATED",
            "urgency_score": rule_urgency,
            "price_sensitivity": "MEDIUM",
            "buying_timeline": "UNCLEAR",
            "competitor_mentioned": has_competitor,
            "quantity_mentioned_mt": quantity,
            "product_interest": [],
            "key_objections": [],
            "intent_summary": "Could not analyze",
            "recommended_action": "WAIT",
            "confidence": 0.3,
            "requested_info": [],
            "ooo_return_date": None,
        }

        # LLM analysis
        try:
            from tools.llm_router import call as llm_call
            prompt = _ANALYSIS_PROMPT.format(
                company=lead_company or "Unknown Company",
                country=lead_country or "Unknown",
                body=body[:3000],
            )
            raw = llm_call(prompt, expect_json=True)

            # Strip markdown code fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"```(?:json)?\n?", "", raw).strip()

            result = json.loads(raw)

            # Merge rule-based signals with LLM result (rule-based takes precedence for quantities/competitors)
            if quantity and not result.get("quantity_mentioned_mt"):
                result["quantity_mentioned_mt"] = quantity
            if has_competitor and not result.get("competitor_mentioned"):
                result["competitor_mentioned"] = True
            # Average urgency scores for better accuracy
            llm_urgency = result.get("urgency_score", 0)
            result["urgency_score"] = round((llm_urgency * 0.7 + rule_urgency * 0.3))
            # Ensure all required keys exist
            for key, default in default_result.items():
                if key not in result:
                    result[key] = default
            return result

        except json.JSONDecodeError as e:
            logger.warning("Sentiment JSON parse error: %s", e)
            return default_result
        except Exception as e:
            logger.error("Sentiment analysis LLM call failed: %s", e)
            return default_result

    def is_high_urgency(self, result: dict) -> bool:
        """Returns True if this reply needs immediate COO attention."""
        return (
            result.get("urgency_score", 0) >= 7
            or result.get("recommended_action") in ("CLOSE", "SCHEDULE_CALL")
            or (result.get("quantity_mentioned_mt") or 0) >= 100
        )

    def format_for_telegram(self, result: dict, lead_company: str = "") -> str:
        """Format sentiment result for Telegram notification."""
        cls = result.get("classification", "UNKNOWN")
        urgency = result.get("urgency_score", 0)
        action = result.get("recommended_action", "WAIT")
        timeline = result.get("buying_timeline", "UNCLEAR")
        qty = result.get("quantity_mentioned_mt")

        urgency_bar = "🔴" if urgency >= 7 else "🟡" if urgency >= 4 else "🟢"

        lines = [
            f"📨 *Reply Analysis: {lead_company}*",
            f"Status: `{cls}` | {urgency_bar} Urgency: {urgency}/10",
            f"Action: `{action}` | Timeline: `{timeline}`",
        ]
        if qty:
            lines.append(f"Quantity: *{qty:.0f} MT*")
        if result.get("competitor_mentioned"):
            lines.append("⚠️ Competitor mentioned in reply")
        if result.get("key_objections"):
            lines.append(f"Objections: {', '.join(result['key_objections'][:2])}")
        lines.append(f"_{result.get('intent_summary', '')}_")
        return "\n".join(lines)

    def save_to_db(self, lead_id: int, outreach_id: int, result: dict):
        """Persist sentiment result to buyer_memory table."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO buyer_memory
                       (lead_id, memory_type, content, created_at)
                       VALUES (?, 'sentiment', ?, datetime('now'))""",
                    (lead_id, json.dumps(result))
                )
        except Exception as e:
            logger.debug("Could not save sentiment to buyer_memory: %s", e)
