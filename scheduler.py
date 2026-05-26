"""APScheduler orchestrator — registers all 7 agents on their defined schedules."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.enrichment_agent import EnrichmentAgent
from agents.followup_agent import FollowUpAgent
from agents.monitor_agent import MonitorAgent
from agents.outreach_agent import OutreachAgent
from agents.proposal_agent import ProposalAgent
from agents.reporter_agent import ReporterAgent
from agents.scraper_agent import ScraperAgent
import db.database as db

logger = logging.getLogger(__name__)

_scheduler = BlockingScheduler(timezone="Asia/Jakarta")


def _run_safe(name: str, fn):
    """Wrap agent run() in try/except so one failure doesn't kill the scheduler."""
    def wrapper():
        logger.info("=== %s starting ===", name)
        try:
            result = fn()
            logger.info("=== %s finished: %s ===", name, result)
        except Exception as exc:
            logger.exception("=== %s CRASHED: %s ===", name, exc)
    return wrapper


def _scraper_then_enrichment():
    """Run scraper then immediately enrich new leads."""
    ScraperAgent().run()
    EnrichmentAgent().run()


def build_scheduler() -> BlockingScheduler:
    """Register all agent jobs and return the configured scheduler."""

    # Scraper + Enrichment chain: daily 08:00 WIB
    _scheduler.add_job(
        _run_safe("ScraperAgent+EnrichmentAgent", _scraper_then_enrichment),
        CronTrigger(hour=8, minute=0),
        id="scraper_enrichment",
        name="Scraper + Enrichment",
        replace_existing=True,
    )

    # Outreach: daily 09:00 WIB
    _scheduler.add_job(
        _run_safe("OutreachAgent", OutreachAgent().run),
        CronTrigger(hour=9, minute=0),
        id="outreach",
        name="Outreach Agent",
        replace_existing=True,
    )

    # Monitor: every 2 hours
    _scheduler.add_job(
        _run_safe("MonitorAgent", MonitorAgent().run),
        IntervalTrigger(hours=2),
        id="monitor",
        name="Monitor Agent",
        replace_existing=True,
    )

    # Follow-up: daily 14:00 WIB
    _scheduler.add_job(
        _run_safe("FollowUpAgent", FollowUpAgent().run),
        CronTrigger(hour=14, minute=0),
        id="followup",
        name="Follow-up Agent",
        replace_existing=True,
    )

    # Reporter: daily 18:00 WIB
    _scheduler.add_job(
        _run_safe("ReporterAgent", ReporterAgent().run),
        CronTrigger(hour=18, minute=0),
        id="reporter",
        name="Reporter Agent",
        replace_existing=True,
    )

    # Daily DB backup: 02:00 WIB
    _scheduler.add_job(
        _run_safe("DatabaseBackup", db.backup_db),
        CronTrigger(hour=2, minute=0),
        id="db_backup",
        name="Database Backup",
        replace_existing=True,
    )

    logger.info("Scheduler configured with %d jobs.", len(_scheduler.get_jobs()))
    return _scheduler
