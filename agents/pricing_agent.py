"""
agents/pricing_agent.py — Dynamic FOB Pricing Engine
PT Cahaya Woodchar International — v3.0

Calculates competitive FOB Lampung prices for quotations based on:
  - Product type and grade
  - Order quantity (MT) — volume discount tiers
  - Destination country — freight adjustment
  - Payment terms — T/T vs L/C premium
  - Market conditions — USD/IDR rate adjustment
  - Competitor price intelligence — ensure we're competitive

Pricing structure:
  Base cost (production + overhead) → Freight to Bandar Lampung port →
  FOB price → Destination freight estimate (informational only) →
  Suggested retail/wholesale margin for buyer

Pricing tiers (volume discounts):
  20-49 MT:   Standard price
  50-99 MT:   -3%
  100-199 MT: -5%
  200+ MT:    -8% (requires COO approval)

Payment terms adjustment:
  T/T 30%+70% before shipment: Standard
  L/C at sight: +2% (bank fee coverage)
  D/P 60 days: +3% (credit risk premium)

Usage:
  from agents.pricing_agent import PricingAgent
  pa = PricingAgent()
  quote = pa.calculate(product_id="CWI-001", quantity_mt=50, destination="JP", payment="TT")
"""

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).parent.parent / "data" / "product_catalog.json"

# Base FOB prices (USD/MT) — COO fills actual values
# These are fallback values if catalog has FILL_BY_COO
BASE_PRICES_USD_MT = {
    "CWI-001": 280,  # Halaban Lump Wood — premium
    "CWI-002": 240,  # Mix Hardwood — standard
    "CWI-003": 260,  # BBQ Briquette
    "CWI-004": 180,  # Sawdust — economy
    "CWI-005": 320,  # Coconut Shell — premium shisha
}

# Volume discount tiers: (min_mt, max_mt, discount_pct)
VOLUME_TIERS = [
    (200, float("inf"), 0.08),
    (100, 199.99,       0.05),
    (50,   99.99,       0.03),
    (20,   49.99,       0.00),  # MOQ = 20 MT
]

# Payment term adjustments
PAYMENT_ADJUSTMENTS = {
    "TT":       0.00,   # T/T — standard
    "LC":       0.02,   # L/C — +2%
    "DP60":     0.03,   # D/P 60 days — +3%
    "CASH":    -0.01,   # Full upfront — -1% discount
}

# Estimated freight reference (USD/MT) to buyer's port — informational
# Actual freight to be confirmed with forwarder
FREIGHT_REFERENCE_USD_MT = {
    "JP": 45, "KR": 40, "AU": 55, "SG": 20, "MY": 18,
    "IN": 35, "AE": 38, "SA": 42, "DE": 70, "NL": 70,
    "GB": 75, "US": 90, "TR": 50, "EG": 45,
    "DEFAULT": 50,
}

# Market condition adjustments (updated weekly from USD/IDR rate)
IDR_BREAKEVEN_RATE = 15_800  # IDR per USD breakeven
IDR_RANGE_ADJUSTMENT = 0.02   # 2% price shift per 500 IDR deviation


class PricingAgent:
    def __init__(self):
        self._catalog = self._load_catalog()
        self._usd_idr_rate = self._get_usd_idr_rate()

    def _load_catalog(self) -> dict:
        """Load product catalog from JSON."""
        try:
            data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            return {p["product_id"]: p for p in data.get("products", [])}
        except Exception as e:
            logger.warning("Could not load product catalog: %s", e)
            return {}

    def _get_usd_idr_rate(self) -> float:
        """Get current USD/IDR exchange rate."""
        try:
            from tools.gemini_tool import call_gemini
            # Try from market_intel table (most recent)
            from db.database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    """SELECT usd_idr_rate FROM market_intel
                       WHERE usd_idr_rate IS NOT NULL
                       ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()
            if row:
                return float(row["usd_idr_rate"])
        except Exception:
            pass
        return 16_000.0  # Fallback default

    def _get_base_price(self, product_id: str) -> float:
        """Get base FOB price for a product."""
        product = self._catalog.get(product_id, {})
        price_str = product.get("price_usd_per_mt", "FILL_BY_COO")
        if price_str and price_str != "FILL_BY_COO":
            try:
                return float(str(price_str).replace("$", "").replace(",", ""))
            except ValueError:
                pass
        # Fallback to hardcoded base prices
        return BASE_PRICES_USD_MT.get(product_id, 250.0)

    def _apply_volume_discount(self, base_price: float, quantity_mt: float) -> tuple:
        """Apply volume discount tier. Returns (discounted_price, discount_pct)."""
        for min_mt, max_mt, discount in VOLUME_TIERS:
            if min_mt <= quantity_mt <= max_mt:
                return base_price * (1 - discount), discount
        if quantity_mt < 20:
            logger.warning("Quantity %.1f MT is below MOQ of 20 MT", quantity_mt)
        return base_price, 0.0

    def _apply_payment_adjustment(self, price: float, payment_terms: str) -> tuple:
        """Apply payment terms adjustment. Returns (adjusted_price, adjustment_pct)."""
        adj = PAYMENT_ADJUSTMENTS.get(payment_terms.upper(), 0.0)
        return price * (1 + adj), adj

    def _apply_currency_adjustment(self, price: float) -> tuple:
        """
        Adjust price based on current USD/IDR rate vs breakeven.
        If IDR is weak (high USD/IDR), we can offer slightly better prices.
        If IDR is strong, we need to protect margin.
        """
        rate_diff = self._usd_idr_rate - IDR_BREAKEVEN_RATE
        adjustment = (rate_diff / 500) * IDR_RANGE_ADJUSTMENT
        adjustment = max(-0.04, min(0.04, adjustment))  # Cap at ±4%
        return price * (1 + adjustment), adjustment

    def calculate(self, product_id: str, quantity_mt: float,
                  destination: str = "DEFAULT", payment_terms: str = "TT",
                  include_freight: bool = True) -> dict:
        """
        Calculate FOB price quotation.

        Args:
            product_id: e.g. "CWI-001"
            quantity_mt: Order quantity in metric tons
            destination: Country code e.g. "JP"
            payment_terms: "TT", "LC", "DP60", "CASH"
            include_freight: Whether to include freight reference

        Returns:
            dict with pricing breakdown
        """
        product = self._catalog.get(product_id, {"name": product_id})
        product_name = product.get("name", product_id)

        # Step 1: Base price
        base = self._get_base_price(product_id)

        # Step 2: Volume discount
        after_volume, vol_discount = self._apply_volume_discount(base, quantity_mt)

        # Step 3: Payment terms
        after_payment, pay_adj = self._apply_payment_adjustment(after_volume, payment_terms)

        # Step 4: Currency adjustment
        after_currency, curr_adj = self._apply_currency_adjustment(after_payment)

        # Step 5: Round to nearest $5 for clean quotes
        fob_per_mt = round(after_currency / 5) * 5

        # Total
        total_fob = round(fob_per_mt * quantity_mt, 2)

        # Freight reference (informational)
        freight_per_mt = FREIGHT_REFERENCE_USD_MT.get(
            destination.upper() if destination else "DEFAULT",
            FREIGHT_REFERENCE_USD_MT["DEFAULT"]
        )
        total_freight_est = round(freight_per_mt * quantity_mt, 2)
        cfr_per_mt_est = fob_per_mt + freight_per_mt

        result = {
            "product_id": product_id,
            "product_name": product_name,
            "quantity_mt": quantity_mt,
            "destination": destination,
            "payment_terms": payment_terms,
            "base_price_usd_mt": base,
            "volume_discount_pct": round(vol_discount * 100, 1),
            "fob_price_usd_mt": fob_per_mt,
            "total_fob_usd": total_fob,
            "freight_ref_usd_mt": freight_per_mt if include_freight else None,
            "cfr_est_usd_mt": cfr_per_mt_est if include_freight else None,
            "total_freight_est_usd": total_freight_est if include_freight else None,
            "usd_idr_rate": self._usd_idr_rate,
            "valid_until": (date.today() + timedelta(days=7)).isoformat(),
            "requires_coo_approval": quantity_mt >= 200,
            "moq_mt": 20,
            "notes": (
                f"FOB Bandar Lampung. Payment: {payment_terms}. "
                f"Volume discount: {round(vol_discount * 100)}%. "
                + ("⚠️ Quantity ≥ 200 MT requires COO approval." if quantity_mt >= 200 else "")
            ),
        }

        # Save to pricing_cache
        self._cache_result(result)
        return result

    def _cache_result(self, result: dict):
        """Save pricing calculation to pricing_cache table."""
        try:
            from db.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO pricing_cache
                       (product_id, quantity_mt, destination, payment_terms,
                        price_usd_per_mt, total_usd, freight_est_usd, valid_until)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (result["product_id"], result["quantity_mt"], result["destination"],
                     result["payment_terms"], result["fob_price_usd_mt"],
                     result["total_fob_usd"], result.get("total_freight_est_usd"),
                     result["valid_until"])
                )
        except Exception as e:
            logger.debug("Could not cache pricing result: %s", e)

    def format_quotation_text(self, result: dict, buyer_name: str = "") -> str:
        """Format pricing result as a readable quotation block for emails."""
        vol_disc = result.get("volume_discount_pct", 0)
        discount_line = f"  Volume discount: -{vol_disc}%\n" if vol_disc > 0 else ""
        freight_line = ""
        if result.get("cfr_est_usd_mt"):
            freight_line = (
                f"  Freight reference: ~${result['freight_ref_usd_mt']}/MT\n"
                f"  *Estimated CFR {result['destination']}: ~${result['cfr_est_usd_mt']}/MT*\n"
            )

        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"QUOTATION — {buyer_name or 'Valued Customer'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Product:  {result['product_name']}\n"
            f"Quantity: {result['quantity_mt']:.0f} MT\n"
            f"  Base price: ${result['base_price_usd_mt']}/MT\n"
            f"{discount_line}"
            f"  *FOB Lampung: ${result['fob_price_usd_mt']}/MT*\n"
            f"  *Total FOB: ${result['total_fob_usd']:,.0f} USD*\n"
            f"{freight_line}"
            f"Payment:  {result['payment_terms']}\n"
            f"Validity: {result['valid_until']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Port: Bandar Lampung, Indonesia\n"
            f"Incoterm: FOB\n"
            f"MOQ: {result['moq_mt']} MT\n"
        )

    def compare_with_competitors(self, product_id: str, quantity_mt: float) -> dict:
        """
        Compare our FOB price with latest competitor prices from competitor_history.
        Returns competitive position.
        """
        our_price = self.calculate(product_id, quantity_mt)["fob_price_usd_mt"]
        try:
            from db.database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT competitor_name, price_usd_per_mt, scraped_at
                       FROM competitor_history
                       WHERE product_type LIKE ?
                       ORDER BY scraped_at DESC
                       LIMIT 5""",
                    (f"%{product_id[-3:].lower()}%",)
                ).fetchall()
        except Exception:
            rows = []

        if not rows:
            return {"our_price": our_price, "position": "unknown", "competitors": []}

        comp_prices = [r["price_usd_per_mt"] for r in rows if r["price_usd_per_mt"]]
        avg_comp = sum(comp_prices) / len(comp_prices) if comp_prices else our_price
        diff_pct = round((our_price - avg_comp) / avg_comp * 100, 1)

        position = "competitive" if diff_pct <= 5 else "above_market" if diff_pct > 5 else "below_market"
        return {
            "our_price": our_price,
            "avg_competitor_price": round(avg_comp, 2),
            "difference_pct": diff_pct,
            "position": position,
            "competitors": [dict(r) for r in rows],
        }

    def get_all_products_summary(self, quantity_mt: float = 40.0,
                                  destination: str = "JP") -> list:
        """Quick pricing summary for all products (for Telegram /price command)."""
        results = []
        for pid in self._catalog or BASE_PRICES_USD_MT:
            q = self.calculate(pid, quantity_mt, destination)
            results.append({
                "product": q["product_name"],
                "fob_per_mt": q["fob_price_usd_mt"],
                "total": q["total_fob_usd"],
            })
        return results
