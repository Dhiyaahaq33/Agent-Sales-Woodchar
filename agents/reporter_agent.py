"""Reporter Agent — compiles daily performance stats and sends to Telegram."""
import logging
from datetime import datetime

import db.database as db
from tools.telegram_tool import send_message

logger = logging.getLogger(__name__)


def _fmt_pipeline(pipeline: dict) -> str:
    if not pipeline:
        return "  (empty)"
    lines = []
    order = ["NEW", "ENRICHED", "OUTREACH", "REPLIED", "QUALIFIED", "DEAD"]
    for status in order:
        if status in pipeline:
            lines.append(f"  • {status}: {pipeline[status]}")
    for s, c in pipeline.items():
        if s not in order:
            lines.append(f"  • {s}: {c}")
    return "\n".join(lines)


def _fmt_classification(cls: dict) -> str:
    if not cls:
        return "  (none)"
    return "\n".join(f"  • {k}: {v}" for k, v in cls.items())


class ReporterAgent:
    """Sends daily summary report to the COO via Telegram."""

    def run(self) -> dict:
        logger.info("[ReporterAgent] Compiling daily report...")
        stats = db.get_daily_stats()
        today_str = datetime.now().strftime("%d %B %Y")

        interested = stats.get("interested_today", [])
        if interested:
            interested_lines = "\n".join(
                f"  ✓ {r['company_name']} ({r['country']})" for r in interested
            )
        else:
            interested_lines = "  (none today)"

        report = (
            f"📊 *PT Ekspor Arang — Daily Report*\n"
            f"_{today_str}_\n\n"
            f"*Today's Activity*\n"
            f"• Leads scraped: {stats['leads_scraped_today']}\n"
            f"• Emails sent: {stats['emails_sent_today']}\n"
            f"• Replies received: {stats['replies_today']}\n"
            f"• Proposals sent: {stats['proposals_sent_today']}\n\n"
            f"*New INTERESTED Leads*\n{interested_lines}\n\n"
            f"*Reply Classification Breakdown*\n"
            f"{_fmt_classification(stats['classification_breakdown'])}\n\n"
            f"*Full Pipeline*\n"
            f"{_fmt_pipeline(stats['pipeline'])}\n\n"
            f"_Automated report by PT Ekspor Arang AI Agent System_"
        )

        success = send_message(report)
        if success:
            logger.info("[ReporterAgent] Daily report sent to Telegram.")
        else:
            logger.error("[ReporterAgent] Failed to send Telegram report.")

        return {"sent": success, "stats": stats}
