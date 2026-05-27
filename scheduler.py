"""APScheduler orchestrator v2 — semua agent terdaftar dengan jadwal PRD v2.

Jadwal (WIB / Asia/Jakarta):
  06:30  MarketAgent     — morning briefing + trigger evaluation
  08:00  Scraper + Enrichment
  09:00  OutreachAgent   — cold email (max 200/day)
  Every 2h MonitorAgent  — inbox classification
  14:00  FollowUpAgent   — touch 2-6
  18:00  ReporterAgent   — daily Telegram summary
  Every 30m SheetsSync   — Google Sheets CRM
  Sunday 03:00 ML retrain + A/B evaluation
  02:00  DB backup
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.enrichment_agent import EnrichmentAgent
from agents.followup_agent import FollowUpAgent
from agents.market_agent import MarketAgent
from agents.monitor_agent import MonitorAgent
from agents.outreach_agent import OutreachAgent
from agents.reporter_agent import ReporterAgent
from agents.scraper_agent import ScraperAgent
import db.database as db
from tools import sheets_sync, scoring_ml, ab_tester

logger = logging.getLogger(__name__)

_scheduler = BlockingScheduler(timezone="Asia/Jakarta")


def _run_safe(name: str, fn):
    """Wrap agent in try/except — one crash won't kill the scheduler."""
    def wrapper(*args, **kwargs):
        # Check pause flag from Telegram /stop command
        from tools.telegram_tool import is_paused
        if is_paused() and name not in ("ReporterAgent", "DatabaseBackup", "MarketAgent"):
            logger.info("=== %s SKIPPED (paused by /stop) ===", name)
            return
        logger.info("=== %s starting ===", name)
        try:
            result = fn(*args, **kwargs)
            logger.info("=== %s finished: %s ===", name, result)
        except Exception as exc:
            logger.exception("=== %s CRASHED: %s ===", name, exc)
    return wrapper


def _morning_pipeline():
    """06:30 — Market intel, then trigger evaluation."""
    MarketAgent().run()


def _scraper_then_enrichment():
    """08:00 — Scraper discovers leads, Enrichment scores them."""
    ScraperAgent().run()
    EnrichmentAgent().run()


def _weekly_ml_tasks():
    """Sunday 03:00 — ML retrain + A/B test evaluation."""
    scoring_ml.run_weekly_retrain()
    ab_tester.run_weekly_evaluation()


def build_scheduler() -> BlockingScheduler:
    """Register all jobs and return configured scheduler."""

    # 06:30 — Market Intelligence (before everyone else)
    _scheduler.add_job(
        _run_safe("MarketAgent", _morning_pipeline),
        CronTrigger(hour=6, minute=30),
        id="market_intel",
        name="Market Intelligence Agent",
        replace_existing=True,
    )

    # 08:00 — Scraper + Enrichment chain
    _scheduler.add_job(
        _run_safe("ScraperAgent+EnrichmentAgent", _scraper_then_enrichment),
        CronTrigger(hour=8, minute=0),
        id="scraper_enrichment",
        name="Scraper + Enrichment",
        replace_existing=True,
    )

    # 09:00 — Outreach
    _scheduler.add_job(
        _run_safe("OutreachAgent", OutreachAgent().run),
        CronTrigger(hour=9, minute=0),
        id="outreach",
        name="Outreach Agent",
        replace_existing=True,
    )

    # Every 2 hours — Monitor inbox
    _scheduler.add_job(
        _run_safe("MonitorAgent", MonitorAgent().run),
        IntervalTrigger(hours=2),
        id="monitor",
        name="Monitor Agent",
        replace_existing=True,
    )

    # 14:00 — Follow-up
    _scheduler.add_job(
        _run_safe("FollowUpAgent", FollowUpAgent().run),
        CronTrigger(hour=14, minute=0),
        id="followup",
        name="Follow-up Agent",
        replace_existing=True,
    )

    # 18:00 — Daily reporter
    _scheduler.add_job(
        _run_safe("ReporterAgent", ReporterAgent().run),
        CronTrigger(hour=18, minute=0),
        id="reporter",
        name="Reporter Agent",
        replace_existing=True,
    )

    # Every 30 minutes — Google Sheets sync
    _scheduler.add_job(
        _run_safe("SheetsSync", sheets_sync.run),
        IntervalTrigger(minutes=30),
        id="sheets_sync",
        name="Google Sheets Sync",
        replace_existing=True,
    )

    # Sunday 03:00 — ML retrain + A/B evaluation
    _scheduler.add_job(
        _run_safe("WeeklyML", _weekly_ml_tasks),
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="weekly_ml",
        name="Weekly ML Retrain + A/B Eval",
        replace_existing=True,
    )

    # 02:00 daily — DB backup
    _scheduler.add_job(
        _run_safe("DatabaseBackup", db.backup_db),
        CronTrigger(hour=2, minute=0),
        id="db_backup",
        name="Database Backup",
        replace_existing=True,
    )

    logger.info("Scheduler v2 configured with %d jobs.", len(_scheduler.get_jobs()))
    return _scheduler
