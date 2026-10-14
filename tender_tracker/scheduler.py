"""Long-running scheduler: runs the pipeline daily (default 08:00 IST)."""

from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import load_config
from .pipeline import run_once

log = logging.getLogger(__name__)


def daily_job(config_path: Path) -> None:
    # Reload each time so edits made in the dashboard apply without a restart.
    cfg = load_config(config_path)
    run_once(cfg)


def start(config_path: Path, run_now: bool = False) -> None:
    cfg = load_config(config_path)
    sched = cfg.schedule
    scheduler = BlockingScheduler(timezone=sched.timezone)
    scheduler.add_job(
        daily_job,
        CronTrigger(hour=sched.hour, minute=sched.minute, timezone=sched.timezone),
        args=[config_path],
        id="daily_gem_run",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3 * 3600,  # still run if the host was briefly down at 08:00
    )
    log.info("Scheduler started: daily at %02d:%02d %s", sched.hour, sched.minute, sched.timezone)
    if run_now:
        daily_job(config_path)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
