"""
ranking.py — Calculate view-growth rankings for 24 h / 7 d / 30 d periods.

For each tracked video the growth is:

    growth = latest_view_count - view_count_at(now - period)

Daily ranking can run in strict mode (DAILY_STRICT_24H_DIFF=1),
which requires an old snapshot at or before (now - 24h).
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DAILY_STRICT_24H_DIFF, EXCLUDED_CHANNELS_FILE
from db import get_connection

logger = logging.getLogger(__name__)

# Period name -> hours
PERIODS: dict[str, int] = {
    "daily": 24,
    "weekly": 168,    # 7 x 24
    "monthly": 720,   # 30 x 24
}

# Period/content_type -> target table
RANKING_TABLES: dict[str, dict[str, str]] = {
    "daily": {
        "shorts": "daily_ranking_shorts",
        "video": "daily_ranking_video",
    },
    "weekly": {
        "shorts": "weekly_ranking_shorts",
        "video": "weekly_ranking_video",
    },
    "monthly": {
        "shorts": "monthly_ranking_shorts",
        "video": "monthly_ranking_video",
    },
}


def _load_excluded_channel_ids() -> list[str]:
    """Load manually excluded channel IDs from a text file."""
    path = Path(EXCLUDED_CHANNELS_FILE)
    if not path.exists():
        return []

    channel_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        channel_ids.append(value)

    # de-duplicate while preserving order
    return list(dict.fromkeys(channel_ids))


def _calculate_ranking(period_name: str, period_hours: int, content_type: str, table: str) -> None:
    """
    Compute view-growth ranking for one period and one content type.

    Strict daily mode:
      - requires an old snapshot at or before (now - 24h)
      - does not fall back to first snapshot

    Non-strict mode (weekly/monthly and optional daily fallback):
      - falls back to first snapshot when historical cutoff snapshot is missing
    """
    now_utc = datetime.now(timezone.utc)
    period_start = now_utc - timedelta(hours=period_hours)
    is_strict_daily = period_name == "daily" and DAILY_STRICT_24H_DIFF

    excluded_channel_ids = _load_excluded_channel_ids()
    exclude_clause = ""
    excluded_params: tuple = ()
    if excluded_channel_ids:
        exclude_clause = " AND NOT (v.channel_id = ANY(%s))"
        excluded_params = (excluded_channel_ids,)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")

            if is_strict_daily:
                sql = f"""
                    WITH latest_stats AS (
                        SELECT DISTINCT ON (video_id)
                            video_id,
                            view_count,
                            timestamp AS latest_ts
                        FROM video_stats
                        ORDER BY video_id, timestamp DESC
                    ),
                    old_stats AS (
                        SELECT DISTINCT ON (video_id)
                            video_id,
                            view_count,
                            timestamp AS old_ts
                        FROM video_stats
                        WHERE timestamp <= %s
                        ORDER BY video_id, timestamp DESC
                    ),
                    growth AS (
                        SELECT
                            l.video_id,
                            l.view_count - o.view_count AS view_growth
                        FROM latest_stats l
                        JOIN old_stats o ON l.video_id = o.video_id
                        JOIN videos v ON v.video_id = l.video_id
                        WHERE COALESCE(NULLIF(v.group_name, ''), 'other') <> 'other'
                          AND (v.title LIKE %s OR v.tags_text LIKE %s)
                          AND v.content_type = %s
                          {exclude_clause}
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
                """
                params = (
                    period_start,
                    "%切り抜き%",
                    "%切り抜き%",
                    content_type,
                    *excluded_params,
                    now_utc,
                )
            else:
                sql = f"""
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
                        ORDER BY video_id, timestamp DESC
                    ),
                    first_stats AS (
                        SELECT DISTINCT ON (video_id)
                            video_id,
                            view_count
                        FROM video_stats
                        ORDER BY video_id, timestamp ASC
                    ),
                    growth AS (
                        SELECT
                            l.video_id,
                            l.view_count - COALESCE(o.view_count, f.view_count, l.view_count) AS view_growth
                        FROM latest_stats l
                        LEFT JOIN old_stats o ON l.video_id = o.video_id
                        LEFT JOIN first_stats f ON l.video_id = f.video_id
                        JOIN videos v ON v.video_id = l.video_id
                        WHERE COALESCE(NULLIF(v.group_name, ''), 'other') <> 'other'
                          AND (v.title LIKE %s OR v.tags_text LIKE %s)
                          AND v.content_type = %s
                          {exclude_clause}
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
                """
                params = (
                    period_start,
                    "%切り抜き%",
                    "%切り抜き%",
                    content_type,
                    *excluded_params,
                    now_utc,
                )

            cur.execute(sql, params)
            row_count = cur.rowcount

        conn.commit()
        strict_suffix = " (strict24h)" if is_strict_daily else ""
        logger.info(
            "%s/%s ranking%s: inserted %d row(s) into %s",
            period_name,
            content_type,
            strict_suffix,
            row_count,
            table,
        )
    finally:
        conn.close()


def run_rankings() -> None:
    """Calculate all ranking periods for shorts and video separately."""
    logger.info("=== Ranking calculation started ===")
    for period_name, period_hours in PERIODS.items():
        for content_type in ("shorts", "video"):
            table = RANKING_TABLES[period_name][content_type]
            try:
                _calculate_ranking(period_name, period_hours, content_type, table)
            except Exception:
                logger.exception("Error computing %s/%s ranking", period_name, content_type)
    logger.info("=== Ranking calculation finished ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_rankings()
