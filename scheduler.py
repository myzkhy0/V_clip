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
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
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
from test_site import (
    _fetch_daily_provisional_rows,
    _fetch_latest_rankings,
    _fetch_public_hero_stats,
    _merge_daily_rows,
    _post_text_to_x_api,
)

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")
_scheduler: BlockingScheduler | None = None
_STATS_RETRY_JOB_ID = "vclip_stats_ranking_retry_once"
ENABLE_X_AUTO_POST = os.getenv("ENABLE_X_AUTO_POST", "0").strip() == "1"
X_AUTO_POST_EXCLUDED_CHANNELS_FILE = os.getenv(
    "X_AUTO_POST_EXCLUDED_CHANNELS_FILE",
    str(Path(__file__).resolve().parent / "tweet_excluded_channels.txt"),
).strip()


def _normalize_content_type(content_type: str) -> str:
    return "video" if (content_type or "").strip().lower() == "video" else "shorts"


def _target_label(content_type: str) -> str:
    return "動画" if _normalize_content_type(content_type) == "video" else "Shorts"


def _ranking_table_for(content_type: str) -> str:
    return "daily_ranking_video" if _normalize_content_type(content_type) == "video" else "daily_ranking_shorts"


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


def _truncate_text_for_x(value: str, max_len: int = 60) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _fit_x_text(text: str, max_len: int = 280) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_len:
        return normalized
    # Keep structure for TOP3 posts while shrinking title lines first.
    lines = normalized.splitlines()
    if lines and "TOP3" in lines[0]:
        for target_prefix in ("🥉", "🥈", "🥇"):
            for idx, line in enumerate(lines):
                if line.strip().startswith(target_prefix):
                    lines[idx] = _truncate_text_for_x(line, 28)
                    compact = "\n".join(lines).strip()
                    if len(compact) <= max_len:
                        return compact
                    break
        compact = "\n".join(lines).strip()
        if len(compact) <= max_len:
            return compact
    return _truncate_text_for_x(normalized, max_len)


def _detail_url(video_id: str) -> str:
    return f"https://vclipranking.com/video/{video_id}"


def _jst_month_day() -> str:
    now = datetime.now(JST)
    return f"{now.month}/{now.day}"


def _load_x_auto_post_excluded_channel_ids() -> set[str]:
    path = Path(X_AUTO_POST_EXCLUDED_CHANNELS_FILE)
    if not path.exists():
        return set()
    excluded: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        excluded.add(line)
    return excluded


def _daily_rows_for_x(content_type: str, top_n: int = 200) -> list[dict]:
    normalized = _normalize_content_type(content_type)
    _, strict_rows = _fetch_latest_rankings(_ranking_table_for(normalized), top_n=top_n)
    provisional_rows: list[dict] = []
    if normalized == "shorts":
        provisional_rows = _fetch_daily_provisional_rows("shorts", top_n=top_n)
    return _merge_daily_rows(strict_rows, provisional_rows, top_n=top_n)


def _build_overall_text(content_type: str) -> str:
    stats = _fetch_public_hero_stats()
    month_day = _jst_month_day()
    tracking = int(stats.get("tracking_videos") or 0)
    growth = int(stats.get("daily_growth_total") or 0)
    fresh = int(stats.get("new_24h") or 0)
    return (
        f"📊VCLIP全体データ（24h {month_day}）\n"
        f"トラッキング動画数: {tracking:,}\n"
        f"総再生増加: +{growth:,} / 新着動画: {fresh:,}\n"
        "#VCLIP"
    )


def _build_trending_text(content_type: str) -> str:
    label = _target_label(content_type)
    month_day = _jst_month_day()
    rows = _daily_rows_for_x(content_type=content_type, top_n=200)
    excluded_channel_ids = _load_x_auto_post_excluded_channel_ids()
    if excluded_channel_ids:
        rows = [
            row
            for row in rows
            if str(row.get("channel_id") or "").strip() not in excluded_channel_ids
        ]
    new_rows = [row for row in rows if bool(row.get("is_new"))]
    if not new_rows:
        if excluded_channel_ids:
            raise RuntimeError("急上昇候補（NEW）が見つかりません（除外チャンネル適用後）")
        raise RuntimeError("急上昇候補（NEW）が見つかりません")
    best = sorted(new_rows, key=lambda r: int(r.get("rank") or 999999))[0]
    title = _truncate_text_for_x(str(best.get("title") or ""), 60)
    rank = int(best.get("rank") or 0)
    growth = int(best.get("view_growth") or 0)
    return (
        f"🔥現在({month_day})、急上昇中の{label}です。\n\n"
        f"「{title}」\n"
        f"{_detail_url(str(best.get('video_id') or ''))}\n"
        f"24h {rank}位 再生増加 +{growth:,} #VCLIP"
    )


def _build_top3_text(content_type: str) -> str:
    label = _target_label(content_type)
    month_day = _jst_month_day()
    rows = _daily_rows_for_x(content_type=content_type, top_n=10)
    top3 = [row for row in rows if int(row.get("rank") or 0) in {1, 2, 3}]
    if len(top3) < 3:
        top3 = sorted(rows, key=lambda r: int(r.get("rank") or 999999))[:3]
    if not top3:
        raise RuntimeError("TOP3候補が見つかりません")
    rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}
    parts: list[str] = [f"🏆本日({month_day})の{label} TOP3", ""]
    for row in sorted(top3, key=lambda r: int(r.get("rank") or 999999)):
        rank = int(row.get("rank") or 0)
        title = _truncate_text_for_x(str(row.get("title") or ""), 40)
        parts.append(f"{rank_emoji.get(rank, '🏅')}{rank}位: {title}")
        parts.append(_detail_url(str(row.get("video_id") or "")))
        parts.append("")
    parts.append("#VCLIP")
    return "\n".join(parts)


def _build_likes_text(content_type: str) -> str:
    label = _target_label(content_type)
    month_day = _jst_month_day()
    rows = _daily_rows_for_x(content_type=content_type, top_n=200)
    if not rows:
        raise RuntimeError("like候補が見つかりません")
    best = sorted(
        rows,
        key=lambda r: (int(r.get("like_growth") or 0), int(r.get("view_growth") or 0)),
        reverse=True,
    )[0]
    like_growth = int(best.get("like_growth") or 0)
    title = _truncate_text_for_x(str(best.get("title") or ""), 60)
    return (
        f"❤️現在({month_day})、like数が伸びている{label}です。\n\n"
        f"「{title}」\n"
        f"{_detail_url(str(best.get('video_id') or ''))}\n"
        f"24h like +{like_growth:,} #VCLIP"
    )


def _build_comments_text(content_type: str) -> str:
    label = _target_label(content_type)
    rows = _daily_rows_for_x(content_type=content_type, top_n=200)
    if not rows:
        raise RuntimeError("コメント候補が見つかりません")
    best = sorted(
        rows,
        key=lambda r: (int(r.get("comment_growth") or 0), int(r.get("view_growth") or 0)),
        reverse=True,
    )[0]
    comment_growth = int(best.get("comment_growth") or 0)
    title = _truncate_text_for_x(str(best.get("title") or ""), 60)
    return (
        f"💬コメント数が伸びている{label}です。\n\n"
        f"「{title}」\n"
        f"{_detail_url(str(best.get('video_id') or ''))}\n"
        f"24h コメント +{comment_growth:,} #VCLIP"
    )


def _build_x_post_text(category: str, content_type: str = "shorts") -> str:
    key = (category or "").strip().lower()
    if key == "overall":
        return _build_overall_text(content_type)
    if key == "trending":
        return _build_trending_text(content_type)
    if key == "top3":
        return _build_top3_text(content_type)
    if key == "likes":
        return _build_likes_text(content_type)
    if key == "comments":
        return _build_comments_text(content_type)
    raise ValueError(f"Unsupported category: {category}")


def x_auto_post_job(category: str, content_type: str = "shorts") -> None:
    label = _target_label(content_type)
    logger.info("======== X auto post start (%s/%s) ========", category, label)
    try:
        text = _fit_x_text(_build_x_post_text(category, content_type=content_type))
        ok, status, result = _post_text_to_x_api(text)
        if not ok:
            raise RuntimeError(f"X API post failed ({status}): {result}")
        logger.info(
            "X auto post succeeded (%s/%s): %s",
            category,
            label,
            result.get("tweet_id") or "no_tweet_id",
        )
    except Exception:
        logger.exception("X auto post failed (%s/%s)", category, label)
    finally:
        logger.info("======== X auto post end (%s/%s) ========", category, label)


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

    if ENABLE_X_AUTO_POST:
        # Requested JST schedule:
        # 07:00 trending (shorts) / 12:00 trending (video) / 17:00 likes (shorts)
        # 22:00 likes (video) / 03:00 overall
        x_jobs = [
            ("trending", "shorts", 7, 0),
            ("trending", "video", 12, 0),
            ("likes", "shorts", 17, 0),
            ("likes", "video", 22, 0),
            ("overall", "shorts", 3, 0),
        ]
        for category, content_type, hour, minute in x_jobs:
            scheduler.add_job(
                x_auto_post_job,
                "cron",
                hour=hour,
                minute=minute,
                id=f"vclip_x_auto_post_{category}_{_normalize_content_type(content_type)}",
                kwargs={"category": category, "content_type": content_type},
                max_instances=1,
                coalesce=True,
                misfire_grace_time=900,
            )
        logger.info(
            "X auto post enabled: trending/shorts=07:00, trending/video=12:00, likes/shorts=17:00, likes/video=22:00, overall=03:00 (JST).",
        )
    else:
        logger.info("X auto post disabled (ENABLE_X_AUTO_POST!=1).")

    # Run stats/ranking immediately at startup; search and channel update wait for schedule.
    stats_ranking_pipeline()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
