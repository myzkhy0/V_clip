"""
stats_collector.py — Collect view / like / comment snapshots for tracked videos.

- High frequency: videos published within TRACK_DAYS.
- Low frequency: videos currently in weekly/monthly TOP100 (at most once per 24h).
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
    - Low-frequency target: videos in weekly/monthly TOP100,
      if no snapshot exists in the last 24 hours.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=TRACK_DAYS)
    daily_cutoff = now_utc - timedelta(hours=24)
    rows = fetchall(
        """
        WITH ranked_candidates AS (
            SELECT video_id
            FROM weekly_ranking_shorts
            WHERE rank <= 100
              AND calculated_at = (SELECT MAX(calculated_at) FROM weekly_ranking_shorts)
            UNION
            SELECT video_id
            FROM weekly_ranking_video
            WHERE rank <= 100
              AND calculated_at = (SELECT MAX(calculated_at) FROM weekly_ranking_video)
            UNION
            SELECT video_id
            FROM monthly_ranking_shorts
            WHERE rank <= 100
              AND calculated_at = (SELECT MAX(calculated_at) FROM monthly_ranking_shorts)
            UNION
            SELECT video_id
            FROM monthly_ranking_video
            WHERE rank <= 100
              AND calculated_at = (SELECT MAX(calculated_at) FROM monthly_ranking_video)
        )
        SELECT v.video_id
        FROM videos v
        WHERE v.published_at >= %s
           OR NOT EXISTS (
                SELECT 1
                FROM video_stats s
                WHERE s.video_id = v.video_id
           )
           OR (
                v.video_id IN (SELECT rc.video_id FROM ranked_candidates rc)
                AND NOT EXISTS (
                    SELECT 1
                    FROM video_stats s
                    WHERE s.video_id = v.video_id
                      AND s.timestamp >= %s
                )
           )
        """
        ,
        (cutoff, daily_cutoff),
    )
    return [r["video_id"] for r in rows]

def _bulk_insert_stats(stats_rows: list[tuple]) -> None:
    """Insert multiple stat snapshots in a single transaction."""
    if not stats_rows:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'video_stats'
                  AND column_name = 'comment_count'
                LIMIT 1
                """
            )
            has_comment_count = cur.fetchone() is not None

            if has_comment_count:
                cur.executemany(
                    """
                    INSERT INTO video_stats (video_id, view_count, like_count, comment_count)
                    VALUES (%s, %s, %s, %s)
                    """,
                    stats_rows,
                )
            else:
                legacy_rows = [(vid, views, likes) for vid, views, likes, _comments in stats_rows]
                cur.executemany(
                    """
                    INSERT INTO video_stats (video_id, view_count, like_count)
                    VALUES (%s, %s, %s)
                    """,
                    legacy_rows,
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
        (d["video_id"], d["view_count"], d["like_count"], d.get("comment_count", 0))
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
