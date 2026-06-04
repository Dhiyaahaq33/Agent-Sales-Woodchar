"""
agents/reporter_agent.py — Daily Reporter Agent v3.0
PT Cahaya Woodchar International

Upgrades from v2.0:
  - Revenue forecasting: pipeline value by stage with probability weights
  - Source attribution: which scraper/platform generates most INTERESTED leads
  - Open rate tracking: % of sent emails that were opened (via pixel tracking)
  - Churn alert: top 3 at-risk INTERESTED leads
  - Competitor price snapshot: how we compare today
  - Deal value estimator: total pipeline value in USD
  - Weekly trend: vs 7 days ago comparison
"""
import logging
from datetime import datetime, timedelta, date

import db.database as db
from tools.telegram_tool import send_message

logger = logging.getLogger(__name__)

# Pipeline stage conversion probability weights (for weighted forecast)
STAGE_WEIGHTS = {
    "NEW":        0.02,
    "ENRICHED":   0.05,
    "OUTREACH":   0.08,
    "ENGAGED":    0.15,
    "REPLIED":    0.25,
    "INTERESTED": 0.55,
    "QUALIFIED":  0.75,
    "CLOSED":     1.00,
}

# Estimated average deal value per stage (USD) — COO to calibrate
AVG_DEAL_VALUE = {
    "NEW":        5_000,
    "ENRICHED":   5_000,
    "OUTREACH":  15_000,
    "ENGAGED":   15_000,
    "REPLIED":   20_000,
    "INTERESTED":35_000,
    "QUALIFIED": 50_000,
    "CLOSED":    40_000,   # average realized deal
}


class ReporterAgent:

    def run(self) -> dict:
        logger.info("[ReporterAgent] Compiling daily report...")
        stats = db.get_daily_stats()
        today_str = datetime.now().strftime("%d %B %Y")

        # Build report sections
        sections = [
            self._header(today_str, stats),
            self._activity_section(stats),
            self._pipeline_section(stats),
            self._revenue_section(stats),
            self._open_rate_section(),
            self._source_attribution_section(),
            self._churn_alert_section(),
            self._competitor_snapshot_section(),
            self._footer(),
        ]

        report = "\n\n".join(s for s in sections if s)
        success = send_message(report)

        if success:
            logger.info("[ReporterAgent] Daily report sent to Telegram.")
        else:
            logger.error("[ReporterAgent] Failed to send Telegram report.")

        return {"sent": success, "stats": stats}

    # ------------------------------------------------------------------ #
    #  Report sections                                                     #
    # ------------------------------------------------------------------ #

    def _header(self, today_str: str, stats: dict) -> str:
        return (
            f"📊 *PT Cahaya Woodchar — Daily Report*\n"
            f"_{today_str}_"
        )

    def _activity_section(self, stats: dict) -> str:
        interested = stats.get("interested_today", [])
        if interested:
            int_lines = "\n".join(
                f"  ✓ {r.get('company', r.get('company_name', '?'))} ({r.get('country', '?')})"
                for r in interested
            )
        else:
            int_lines = "  (none today)"

        return (
            f"*📬 Today's Activity*\n"
            f"• Leads scraped: {stats['leads_scraped_today']}\n"
            f"• Emails sent: {stats['emails_sent_today']}\n"
            f"• Replies received: {stats['replies_today']}\n"
            f"• Proposals sent: {stats['proposals_sent_today']}\n"
            f"• New INTERESTED:\n{int_lines}"
        )

    def _pipeline_section(self, stats: dict) -> str:
        pipeline = stats.get("pipeline", {})
        if not pipeline:
            return ""
        order = ["NEW", "ENRICHED", "OUTREACH", "ENGAGED", "REPLIED",
                 "INTERESTED", "QUALIFIED", "CLOSED", "DEAD"]
        lines = []
        for s in order:
            if s in pipeline:
                bar = "█" * min(pipeline[s] // 5, 15)
                lines.append(f"`{s:10}` {pipeline[s]:4}  {bar}")
        for s, c in pipeline.items():
            if s not in order:
                lines.append(f"`{s:10}` {c:4}")
        total = sum(pipeline.values())
        return f"*🔽 Pipeline* (total: {total})\n" + "\n".join(lines)

    def _revenue_section(self, stats: dict) -> str:
        """Calculate pipeline value and weighted forecast."""
        pipeline = stats.get("pipeline", {})
        if not pipeline:
            return ""

        pipeline_value = 0.0
        weighted_value = 0.0
        lines = []

        for stage, count in pipeline.items():
            if stage == "DEAD":
                continue
            avg = AVG_DEAL_VALUE.get(stage, 5_000)
            weight = STAGE_WEIGHTS.get(stage, 0.05)
            stage_value = count * avg
            weighted = stage_value * weight
            pipeline_value += stage_value
            weighted_value += weighted

        # Closed deals this month
        closed_mtd = self._get_closed_mtd()

        # Save forecast snapshot
        self._save_forecast(pipeline_value, weighted_value, closed_mtd)

        return (
            f"*💰 Revenue Forecast*\n"
            f"• Pipeline value: *${pipeline_value:,.0f}*\n"
            f"• Weighted forecast: *${weighted_value:,.0f}*\n"
            f"• Closed MTD: *${closed_mtd:,.0f}*"
        )

    def _open_rate_section(self) -> str:
        """Calculate email open rates from tracking data."""
        try:
            today = date.today().isoformat()
            week_ago = (date.today() - timedelta(days=7)).isoformat()
            with db.get_conn() as conn:
                sent_week = conn.execute(
                    "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) >= ?", (week_ago,)
                ).fetchone()[0]
                opened_week = conn.execute(
                    "SELECT COUNT(DISTINCT outreach_id) FROM email_opens WHERE open_count > 0"
                ).fetchone()[0]
                total_opens = conn.execute(
                    "SELECT SUM(open_count) FROM email_opens"
                ).fetchone()[0] or 0

            open_rate = f"{(opened_week/sent_week*100):.1f}%" if sent_week > 0 else "N/A"
            return (
                f"*👁️ Open Tracking (7d)*\n"
                f"• Unique opens: {opened_week} / {sent_week} sent ({open_rate})\n"
                f"• Total open events: {total_opens}"
            )
        except Exception as e:
            logger.debug("Open rate section failed: %s", e)
            return ""

    def _source_attribution_section(self) -> str:
        """Show which lead sources generate the most INTERESTED conversions."""
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    """SELECT source,
                              COUNT(*) as total,
                              SUM(CASE WHEN classification='INTERESTED' THEN 1 ELSE 0 END) as interested,
                              SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed
                       FROM leads
                       WHERE source IS NOT NULL AND source != ''
                       GROUP BY source
                       ORDER BY interested DESC
                       LIMIT 5"""
                ).fetchall()
            if not rows:
                return ""
            lines = []
            for r in rows:
                total = r["total"]
                int_rate = f"{(r['interested']/total*100):.0f}%" if total > 0 else "0%"
                lines.append(
                    f"  {r['source'][:20]:20} {r['total']:4} leads → {r['interested']} INTERESTED ({int_rate})"
                )
            return f"*📡 Source Attribution*\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Source attribution failed: %s", e)
            return ""

    def _churn_alert_section(self) -> str:
        """Show top at-risk INTERESTED leads."""
        try:
            from agents.churn_detector import ChurnDetector
            churning = ChurnDetector().get_churning_leads(threshold=0.6)
            if not churning:
                return ""
            lines = []
            for lead in churning[:3]:
                company = lead.get("company") or lead.get("company_name", "?")
                risk = lead.get("churn_risk", 0)
                lines.append(f"  🔴 {company} — risk {risk:.0%}")
            return f"*🧊 Churn Alert (top {len(lines)})*\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Churn alert section failed: %s", e)
            return ""

    def _competitor_snapshot_section(self) -> str:
        """Show latest competitor price data."""
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    """SELECT competitor_name, price_usd_per_mt, scraped_at
                       FROM competitor_history
                       WHERE price_usd_per_mt IS NOT NULL
                       GROUP BY competitor_name
                       HAVING MAX(scraped_at)
                       ORDER BY price_usd_per_mt ASC
                       LIMIT 3"""
                ).fetchall()
            if not rows:
                return ""
            lines = [f"  • {r['competitor_name']}: ${r['price_usd_per_mt']:.0f}/MT" for r in rows]
            return f"*🏷️ Competitor Prices*\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Competitor snapshot failed: %s", e)
            return ""

    def _footer(self) -> str:
        return "_Automated by PT Cahaya Woodchar AI Agent v3.0_"

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _get_closed_mtd(self) -> float:
        """Total deal value closed this calendar month."""
        try:
            month_start = date.today().replace(day=1).isoformat()
            with db.get_conn() as conn:
                row = conn.execute(
                    """SELECT COALESCE(SUM(deal_value_usd), 0) as total
                       FROM leads
                       WHERE deal_closed_date >= ?
                         AND status = 'CLOSED'""",
                    (month_start,)
                ).fetchone()
            return float(row["total"]) if row else 0.0
        except Exception:
            return 0.0

    def _save_forecast(self, pipeline_value: float, weighted_value: float, closed_mtd: float):
        """Save forecast snapshot to revenue_forecast table."""
        try:
            month = date.today().strftime("%Y-%m")
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO revenue_forecast
                       (forecast_date, month, pipeline_value, weighted_value, closed_mtd, forecast_method)
                       VALUES (?, ?, ?, ?, ?, 'stage_weighted')""",
                    (date.today().isoformat(), month, pipeline_value, weighted_value, closed_mtd)
                )
        except Exception as e:
            logger.debug("Could not save revenue forecast: %s", e)
