"""
scheduler.py — APScheduler-based entry point for the VTuber Clip Ranking System.

Jobs:
  • Search discovery (collector): JST cron (default 06:00, 18:00)
  • Stats + ranking: interval (default every 4 hours)

Usage:
  python scheduler.py              # run continuously with APScheduler
  python scheduler.py --init-db    # create tables and exit
"""

import logging
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from collector import run_collector
from config import (
    SEARCH_CRON_HOURS_JST,
    SEARCH_CRON_MINUTE_JST,
    STATS_INTERVAL_HOURS,
)
from db import init_db
from ranking import run_rankings
from stats_collector import run_stats_collector

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


def search_pipeline() -> None:
    """Execute search/discovery pipeline only."""
    logger.info("======== Search pipeline start ========")
    try:
        run_collector()
    except Exception:
        logger.exception("Collector failed")
    logger.info("======== Search pipeline end ========")


def stats_ranking_pipeline() -> None:
    """Execute stats/ranking pipeline only."""
    logger.info("======== Stats/Ranking pipeline start ========")
    try:
        run_stats_collector()
    except Exception:
        logger.exception("Stats collector failed")

    try:
        run_rankings()
    except Exception:
        logger.exception("Ranking calculation failed")
    logger.info("======== Stats/Ranking pipeline end ========")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if "--init-db" in sys.argv:
        init_db()
        print("Database initialized. Exiting.")
        return

    if "--once" in sys.argv:
        logger.error(
            "--once is disabled in this environment. "
            "Run scheduler.py without flags for automated updates."
        )
        raise SystemExit(2)

    logger.info(
        "Starting scheduler (search JST %s:%02d, stats/ranking every %d hour(s)).",
        SEARCH_CRON_HOURS_JST,
        SEARCH_CRON_MINUTE_JST,
        STATS_INTERVAL_HOURS,
    )

    scheduler = BlockingScheduler(timezone=JST)
    scheduler.add_job(
        search_pipeline,
        "cron",
        hour=SEARCH_CRON_HOURS_JST,
        minute=SEARCH_CRON_MINUTE_JST,
        id="vclip_search_pipeline",
    )
    scheduler.add_job(
        stats_ranking_pipeline,
        "interval",
        hours=max(1, STATS_INTERVAL_HOURS),
        id="vclip_stats_ranking_pipeline",
        next_run_time=None,
    )

    # Run stats/ranking immediately at startup; search waits for JST cron slots.
    stats_ranking_pipeline()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
