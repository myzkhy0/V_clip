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

HISTORY_TABLE_SUFFIX = "_history"
HISTORY_RANK_LIMIT = 100

CLIP_KEYWORD_PATTERN = "%切り抜き%"
VSPO_GROUP_NAME = "VSPO"
VSPO_PERMISSION_PATTERN = "%ぶいすぽっ！許諾番号%"


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


def _build_exclude_filter(excluded_channel_ids: list[str]) -> tuple[str, tuple]:
    if not excluded_channel_ids:
        return "", ()
    return " AND NOT (v.channel_id = ANY(%s))", (excluded_channel_ids,)


def _growth_expression(is_strict_daily: bool) -> str:
    if is_strict_daily:
        return """
                            CASE
                                WHEN (v.published_at + interval '9 hours')::date = ((%s AT TIME ZONE 'Asia/Tokyo')::date)
                                    THEN l.view_count - COALESCE(f.view_count, l.view_count)
                                ELSE l.view_count - o.view_count
                            END
        """
    return "l.view_count - COALESCE(o.view_count, f.view_count, l.view_count)"


def _build_ranking_sql(
    table: str,
    exclude_clause: str,
    growth_expr: str,
    is_strict_daily: bool,
) -> str:
    if is_strict_daily:
        is_today_expr = (
            "(v.published_at + interval '9 hours')::date = "
            "((NOW() AT TIME ZONE 'Asia/Tokyo')::date)"
        )
        ranked_where = "WHERE view_growth > 0 OR is_today_jst"
        ranked_order = "ORDER BY view_growth DESC, latest_view_count DESC"
    else:
        is_today_expr = "FALSE"
        ranked_where = "WHERE view_growth > 0"
        ranked_order = "ORDER BY view_growth DESC"

    return f"""
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
                            {growth_expr} AS view_growth,
                            l.view_count AS latest_view_count,
                            {is_today_expr} AS is_today_jst
                        FROM latest_stats l
                        LEFT JOIN old_stats o ON l.video_id = o.video_id
                        LEFT JOIN first_stats f ON l.video_id = f.video_id
                        JOIN videos v ON v.video_id = l.video_id
                        WHERE (
                              v.title LIKE %s
                           OR v.tags_text LIKE %s
                           OR (COALESCE(v.group_name, '') = %s AND COALESCE(v.description_text, '') LIKE %s)
                          )
                          AND v.content_type = %s
                          {exclude_clause}
                    ),
                    ranked AS (
                        SELECT
                            video_id,
                            view_growth,
                            ROW_NUMBER() OVER ({ranked_order}) AS rank
                        FROM growth
                        {ranked_where}
                    )
                    INSERT INTO {table} (video_id, view_growth, rank, calculated_at)
                    SELECT video_id, view_growth, rank, %s
                    FROM ranked
                """


def _build_ranking_params(
    *,
    period_start: datetime,
    now_utc: datetime,
    content_type: str,
    excluded_params: tuple,
    is_strict_daily: bool,
) -> tuple:
    params: list[object] = [period_start]
    if is_strict_daily:
        params.append(now_utc)
    params.extend([
        CLIP_KEYWORD_PATTERN,
        CLIP_KEYWORD_PATTERN,
        VSPO_GROUP_NAME,
        VSPO_PERMISSION_PATTERN,
        content_type,
    ])
    params.extend(excluded_params)
    params.append(now_utc)
    return tuple(params)


def _iter_ranking_tasks():
    for period_name, period_hours in PERIODS.items():
        for content_type, table in RANKING_TABLES[period_name].items():
            history_table = f"{table}{HISTORY_TABLE_SUFFIX}"
            yield period_name, period_hours, content_type, table, history_table


def _ensure_history_table(cur, history_table: str) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {history_table} (
            video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
            view_growth   BIGINT      NOT NULL DEFAULT 0,
            rank          INTEGER     NOT NULL,
            calculated_at TIMESTAMP   NOT NULL,
            PRIMARY KEY (video_id, calculated_at)
        )
        """
    )
    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{history_table}_rank_calculated
            ON {history_table} (rank, calculated_at)
        """
    )


def _calculate_ranking(
    period_name: str,
    period_hours: int,
    content_type: str,
    table: str,
    history_table: str,
) -> None:
    """
    Compute view-growth ranking for one period and one content type.

    Strict daily mode (hybrid):
      - videos published today (JST): provisional growth (latest - first)
      - videos published before today (JST): requires old snapshot at/before (now - 24h)

    Non-strict mode (weekly/monthly and optional daily fallback):
      - falls back to first snapshot when historical cutoff snapshot is missing
    """
    now_utc = datetime.now(timezone.utc)
    period_start = now_utc - timedelta(hours=period_hours)
    is_strict_daily = period_name == "daily" and DAILY_STRICT_24H_DIFF

    excluded_channel_ids = _load_excluded_channel_ids()
    exclude_clause, excluded_params = _build_exclude_filter(excluded_channel_ids)
    growth_expr = _growth_expression(is_strict_daily)
    sql = _build_ranking_sql(table, exclude_clause, growth_expr, is_strict_daily)
    params = _build_ranking_params(
        period_start=period_start,
        now_utc=now_utc,
        content_type=content_type,
        excluded_params=excluded_params,
        is_strict_daily=is_strict_daily,
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_history_table(cur, history_table)
            cur.execute(f"DELETE FROM {table}")
            cur.execute(sql, params)
            row_count = cur.rowcount
            cur.execute(
                f"""
                INSERT INTO {history_table} (video_id, view_growth, rank, calculated_at)
                SELECT video_id, view_growth, rank, calculated_at
                FROM {table}
                WHERE rank <= %s
                ON CONFLICT (video_id, calculated_at) DO UPDATE
                SET
                    view_growth = EXCLUDED.view_growth,
                    rank = EXCLUDED.rank
                """,
                (HISTORY_RANK_LIMIT,),
            )
            history_row_count = cur.rowcount

        conn.commit()
        strict_suffix = " (strict24h+today-provisional)" if is_strict_daily else ""
        logger.info(
            "%s/%s ranking%s: inserted %d row(s) into %s, upserted %d history row(s) into %s (top %d)",
            period_name,
            content_type,
            strict_suffix,
            row_count,
            table,
            history_row_count,
            history_table,
            HISTORY_RANK_LIMIT,
        )
    finally:
        conn.close()


def run_rankings() -> None:
    """Calculate all ranking periods for shorts and video separately."""
    logger.info("=== Ranking calculation started ===")
    for period_name, period_hours, content_type, table, history_table in _iter_ranking_tasks():
        try:
            _calculate_ranking(period_name, period_hours, content_type, table, history_table)
        except Exception:
            logger.exception("Error computing %s/%s ranking", period_name, content_type)
    logger.info("=== Ranking calculation finished ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_rankings()

