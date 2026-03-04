"""
scheduler.py — APScheduler-based entry point for the VTuber Clip Ranking System.

Runs the full pipeline every COLLECTION_INTERVAL_MINUTES (default: 60 min):
  1. Discover new videos   (collector)
  2. Collect stats          (stats_collector)
  3. Calculate rankings     (ranking)

Usage:
  python scheduler.py              # run continuously with APScheduler
  python scheduler.py --once       # run the pipeline once and exit
  python scheduler.py --init-db    # create tables and exit
"""

import sys
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from config import COLLECTION_INTERVAL_MINUTES
from db import init_db
from collector import run_collector
from stats_collector import run_stats_collector
from ranking import run_rankings

logger = logging.getLogger(__name__)


def pipeline() -> None:
    """Execute the full data-collection-and-ranking pipeline."""
    logger.info("======== Pipeline start ========")
    try:
        run_collector()
    except Exception:
        logger.exception("Collector failed")

    try:
        run_stats_collector()
    except Exception:
        logger.exception("Stats collector failed")

    try:
        run_rankings()
    except Exception:
        logger.exception("Ranking calculation failed")

    logger.info("======== Pipeline end ========")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── CLI flags ────────────────────────────────────────────────────
    if "--init-db" in sys.argv:
        init_db()
        print("Database initialized. Exiting.")
        return

    if "--once" in sys.argv:
        pipeline()
        return

    # ── Continuous scheduling ────────────────────────────────────────
    logger.info(
        "Starting scheduler (interval: %d min).", COLLECTION_INTERVAL_MINUTES
    )
    scheduler = BlockingScheduler()
    scheduler.add_job(
        pipeline,
        "interval",
        minutes=COLLECTION_INTERVAL_MINUTES,
        id="vclip_pipeline",
        next_run_time=None,  # first run immediately handled below
    )

    # Run once immediately at startup
    pipeline()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
