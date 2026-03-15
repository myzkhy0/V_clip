"""
scheduler.py — APScheduler-based entry point for the VTuber Clip Ranking System.

Jobs:
  • Search discovery (collector): JST cron (default 06:00, once daily)
  • Channel update (collector): JST cron (every N hours, anchored to search hour)
  • Stats + ranking: JST cron (every N hours, anchored to search hour)

Usage:
  python scheduler.py              # run continuously with APScheduler
  python scheduler.py --init-db    # create tables and exit
"""

import logging
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from collector import run_collector
from config import (
    CHANNEL_UPDATE_INTERVAL_HOURS,
    SEARCH_CRON_HOURS_JST,
    SEARCH_CRON_MINUTE_JST,
    STATS_INTERVAL_HOURS,
)
from db import init_db
from ranking import run_rankings
from stats_collector import run_stats_collector

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")
_scheduler: BlockingScheduler | None = None
_STATS_RETRY_JOB_ID = "vclip_stats_ranking_retry_once"


def _parse_primary_search_hour(value: str) -> int:
    """Return the first valid hour (0-23) from SEARCH_CRON_HOURS_JST."""
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            return hour
    return 6


def _build_cron_hours(anchor_hour: int, interval_hours: int) -> str:
    """
    Build comma-separated cron hours by stepping interval_hours from anchor_hour.
    Example: anchor=6, interval=4 -> "2,6,10,14,18,22".
    """
    interval = max(1, interval_hours)
    seen: set[int] = set()
    hours: list[int] = []
    hour = anchor_hour % 24
    while hour not in seen:
        seen.add(hour)
        hours.append(hour)
        hour = (hour + interval) % 24
    return ",".join(str(h) for h in sorted(hours))


def _exclude_hour(hours_csv: str, target_hour: int) -> str:
    """Remove target_hour from a comma-separated hour list, if present."""
    kept = [h.strip() for h in (hours_csv or "").split(",") if h.strip()]
    kept = [h for h in kept if h != str(target_hour)]
    return ",".join(kept)


def _schedule_stats_ranking_retry(trigger_name: str) -> None:
    """Schedule one fallback stats/ranking retry 10 minutes later."""
    if _scheduler is None:
        logger.warning("Scheduler not initialized; cannot schedule fallback retry.")
        return
    run_at = datetime.now(JST) + timedelta(minutes=10)
    _scheduler.add_job(
        stats_ranking_pipeline,
        "date",
        run_date=run_at,
        id=_STATS_RETRY_JOB_ID,
        replace_existing=True,
        kwargs={"trigger_label": f"{trigger_name} fallback+10m"},
    )
    logger.warning(
        "Scheduled fallback stats/ranking retry at %s (JST).",
        run_at.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _run_stats_and_rankings(trigger_name: str) -> None:
    """Run stats collection and ranking calculation in sequence."""
    logger.info("---- Triggering stats/ranking after %s ----", trigger_name)
    stats_ok = False
    try:
        run_stats_collector()
        stats_ok = True
    except Exception:
        logger.exception("Stats collector failed")
        # 1) immediate in-job retry once
        try:
            logger.warning("Retrying stats collector immediately once after failure.")
            time.sleep(2)
            run_stats_collector()
            stats_ok = True
            logger.info("Stats collector immediate retry succeeded.")
        except Exception:
            logger.exception("Stats collector immediate retry failed")
            # 6) schedule one fallback retry +10 minutes
            _schedule_stats_ranking_retry(trigger_name)

    try:
        run_rankings()
    except Exception:
        logger.exception("Ranking calculation failed")
    if not stats_ok:
        logger.warning(
            "Ranking ran without fresh stats snapshot (stats collector failed twice)."
        )


def search_pipeline() -> None:
    """Execute daily discovery pipeline (channel + keyword)."""
    logger.info("======== Search pipeline start ========")
    try:
        run_collector(include_channel_search=True, include_keyword_search=True, run_seed=True)
    except Exception:
        logger.exception("Collector failed")
    _run_stats_and_rankings("search pipeline")
    logger.info("======== Search pipeline end ========")


def channel_update_pipeline() -> None:
    """Execute hourly channel update pipeline (channels only)."""
    logger.info("======== Channel update pipeline start ========")
    try:
        run_collector(include_channel_search=True, include_keyword_search=False, run_seed=False)
    except Exception:
        logger.exception("Channel update collector failed")
    logger.info("======== Channel update pipeline end ========")


def stats_ranking_pipeline(trigger_label: str = "stats/ranking schedule") -> None:
    """Execute stats/ranking pipeline only."""
    logger.info("======== Stats/Ranking pipeline start ========")
    _run_stats_and_rankings(trigger_label)
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

    search_hour = _parse_primary_search_hour(SEARCH_CRON_HOURS_JST)
    channel_hours = _build_cron_hours(search_hour, CHANNEL_UPDATE_INTERVAL_HOURS)
    stats_hours = _build_cron_hours(search_hour, STATS_INTERVAL_HOURS)
    # Search slot already runs stats/ranking; avoid duplicate runs at the same time.
    channel_hours = _exclude_hour(channel_hours, search_hour)
    stats_hours = _exclude_hour(stats_hours, search_hour)

    logger.info(
        "Starting scheduler (search JST %s:%02d, channel update cron=%s:%02d, stats/ranking cron=%s:%02d).",
        SEARCH_CRON_HOURS_JST,
        SEARCH_CRON_MINUTE_JST,
        channel_hours or "(disabled)",
        SEARCH_CRON_MINUTE_JST,
        stats_hours or "(disabled)",
        SEARCH_CRON_MINUTE_JST,
    )

    scheduler = BlockingScheduler(timezone=JST)
    global _scheduler
    _scheduler = scheduler
    scheduler.add_job(
        search_pipeline,
        "cron",
        hour=SEARCH_CRON_HOURS_JST,
        minute=SEARCH_CRON_MINUTE_JST,
        id="vclip_search_pipeline",
    )

    if channel_hours:
        scheduler.add_job(
            channel_update_pipeline,
            "cron",
            hour=channel_hours,
            minute=SEARCH_CRON_MINUTE_JST,
            id="vclip_channel_update_pipeline",
        )
    else:
        logger.warning("Channel update cron disabled after excluding search hour.")

    if stats_hours:
        scheduler.add_job(
            stats_ranking_pipeline,
            "cron",
            hour=stats_hours,
            minute=SEARCH_CRON_MINUTE_JST,
            id="vclip_stats_ranking_pipeline",
        )
    else:
        logger.warning("Stats/ranking cron disabled after excluding search hour.")

    # Run stats/ranking immediately at startup; search and channel update wait for schedule.
    stats_ranking_pipeline()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
