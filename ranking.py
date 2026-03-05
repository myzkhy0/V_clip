"""
ranking.py — Calculate view-growth rankings for 24 h / 7 d / 30 d periods.

For each tracked video the growth is:

    growth = latest_view_count − view_count_at(now − period)

If no historical snapshot exists for the period, growth defaults to 0.
"""

import logging
from datetime import datetime, timedelta, timezone

from db import get_connection

logger = logging.getLogger(__name__)

# Period name → (hours, target table)
PERIODS: dict[str, tuple[int, str]] = {
    "daily":   (24,  "daily_ranking"),
    "weekly":  (168, "weekly_ranking"),    # 7 × 24
    "monthly": (720, "monthly_ranking"),   # 30 × 24
}


def _calculate_ranking(period_name: str, period_hours: int, table: str) -> None:
    """
    Compute view-growth ranking for *period_name* and write results to *table*.

    Algorithm (pure SQL for efficiency):
      1. For every video with stats, find the latest view_count.
      2. Find the view_count closest to (now − period_hours).
      3. Growth = latest − historical.
      4. Rank by growth DESC.
    """
    now_utc = datetime.now(timezone.utc)
    period_start = now_utc - timedelta(hours=period_hours)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── Clear previous ranking for this calculation run ──────
            cur.execute(f"DELETE FROM {table}")

            # ── Compute and insert rankings ──────────────────────────
            # The query:
            #   • latest_stats  : most recent snapshot per video
            #   • old_stats     : snapshot closest to period_start per video
            #   • growth        : latest.view_count - COALESCE(old.view_count, 0)
            cur.execute(
                f"""
                WITH latest_stats AS (
                    SELECT DISTINCT ON (video_id)
                        video_id,
                        view_count
                    FROM video_stats
                    ORDER BY video_id, timestamp DESC
                ),
                old_stats AS (
                    SELECT DISTINCT ON (video_id)
                        video_id,
                        view_count
                    FROM video_stats
                    WHERE timestamp <= %s
                    ORDER BY video_id, ABS(EXTRACT(EPOCH FROM (timestamp - %s))) ASC
                ),
                growth AS (
                    SELECT
                        l.video_id,
                        l.view_count - COALESCE(o.view_count, 0) AS view_growth
                    FROM latest_stats l
                    LEFT JOIN old_stats o ON l.video_id = o.video_id
                    JOIN videos v ON v.video_id = l.video_id
                    WHERE COALESCE(NULLIF(v.group_name, ''), 'other') <> 'other'
                      AND (v.title LIKE %s OR v.tags_text LIKE %s)
                      AND v.content_type = 'shorts'
                ),
                ranked AS (
                    SELECT
                        video_id,
                        view_growth,
                        ROW_NUMBER() OVER (ORDER BY view_growth DESC) AS rank
                    FROM growth
                    WHERE view_growth > 0
                )
                INSERT INTO {table} (video_id, view_growth, rank, calculated_at)
                SELECT video_id, view_growth, rank, %s
                FROM ranked
                """,
                (period_start, period_start, "%切り抜き%", "%切り抜き%", now_utc),
            )

            row_count = cur.rowcount
        conn.commit()
        logger.info(
            "%s ranking: inserted %d row(s) into %s",
            period_name,
            row_count,
            table,
        )
    finally:
        conn.close()


def run_rankings() -> None:
    """Calculate all ranking periods."""
    logger.info("=== Ranking calculation started ===")
    for name, (hours, table) in PERIODS.items():
        try:
            _calculate_ranking(name, hours, table)
        except Exception:
            logger.exception("Error computing %s ranking", name)
    logger.info("=== Ranking calculation finished ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_rankings()

