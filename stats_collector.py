"""
stats_collector.py — Collect hourly view / like snapshots for tracked videos.

Only videos published within the last TRACK_DAYS are updated.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import TRACK_DAYS
from db import fetchall, get_connection
from youtube_client import get_video_details

logger = logging.getLogger(__name__)


def _get_tracked_video_ids() -> list[str]:
    """
    Return video IDs to collect stats for.

    - Regular target: videos within TRACK_DAYS.
    - Safety net: videos that still have no stats at all.
      (prevents newly inserted videos from being skipped)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_DAYS)
    rows = fetchall(
        """
        SELECT v.video_id
        FROM videos v
        WHERE v.published_at >= %s
           OR NOT EXISTS (
                SELECT 1
                FROM video_stats s
                WHERE s.video_id = v.video_id
           )
        """
        ,
        (cutoff,),
    )
    return [r["video_id"] for r in rows]

def _bulk_insert_stats(stats_rows: list[tuple]) -> None:
    """Insert multiple stat snapshots in a single transaction."""
    if not stats_rows:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO video_stats (video_id, view_count, like_count)
                VALUES (%s, %s, %s)
                """,
                stats_rows,
            )
        conn.commit()
    finally:
        conn.close()


def run_stats_collector() -> None:
    """Fetch current stats for all tracked videos and store snapshots."""
    logger.info("=== Stats collector started ===")

    video_ids = _get_tracked_video_ids()
    if not video_ids:
        logger.info("No videos within tracking window.")
        return

    logger.info("Collecting stats for %d tracked video(s).", len(video_ids))

    details = get_video_details(video_ids)

    stats_rows = [
        (d["video_id"], d["view_count"], d["like_count"])
        for d in details
    ]

    _bulk_insert_stats(stats_rows)
    logger.info(
        "Inserted %d stat snapshot(s). === Stats collector finished ===",
        len(stats_rows),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_stats_collector()
