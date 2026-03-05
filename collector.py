"""
collector.py — Discover new VTuber clip videos and store them.

Workflow:
  1. Load channels from the DB (seed them on first run).
  2. Search each channel for recent videos.
  3. Search YouTube by keyword.
  4. De-duplicate video IDs.
  5. Fetch video details & filter by duration.
  6. Insert new videos into the `videos` table.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import (
    SEARCH_KEYWORDS,
    SEED_CHANNELS,
    GROUP_KEYWORDS,
    MIN_DURATION_SECONDS,
    MAX_DURATION_SECONDS,
    TRACK_DAYS,
)
from db import fetchall, execute_many
from youtube_client import (
    QuotaExceededError,
    search_by_channel,
    search_by_keyword,
    get_video_details,
    resolve_channel_identifier,
)

logger = logging.getLogger(__name__)


# ── Seed channels (first-run helper) ────────────────────────────────

def seed_channels() -> None:
    """Insert SEED_CHANNELS into the `channels` table if they don't exist."""
    if not SEED_CHANNELS:
        return

    resolved_rows: list[tuple[str, str, str]] = []
    for channel_identifier, channel_name, group_name in SEED_CHANNELS:
        try:
            channel_id = resolve_channel_identifier(channel_identifier)
            resolved_rows.append((channel_id, channel_name, group_name))
        except Exception:
            logger.exception("Failed to resolve seed channel %s", channel_identifier)

    if not resolved_rows:
        logger.warning("No seed channels could be resolved.")
        return

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (channel_id) DO NOTHING
    """
    execute_many(query, resolved_rows)
    logger.info("Seeded %d channel(s).", len(resolved_rows))


# ── Load channels from DB ───────────────────────────────────────────

def load_channels() -> list[dict]:
    """Return all rows from the `channels` table."""
    return fetchall("SELECT channel_id, channel_name, group_name FROM channels")


# ── Discover videos ─────────────────────────────────────────────────

def discover_videos() -> list[str]:
    """
    Collect video IDs from:
      • channel-based search
      • keyword-based search
    Returns a de-duplicated list of video IDs.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_DAYS)
    all_ids: set[str] = set()
    quota_guard_hit = False

    # 1) Channel-based
    channels = load_channels()
    for ch in channels:
        if quota_guard_hit:
            break
        try:
            ids = search_by_channel(ch["channel_id"], cutoff)
            all_ids.update(ids)
            logger.info(
                "Channel %s (%s): %d videos",
                ch["channel_name"],
                ch["group_name"],
                len(ids),
            )
        except QuotaExceededError as exc:
            quota_guard_hit = True
            logger.warning("Stopping further searches: %s", exc)
        except RuntimeError as exc:
            if "quota guard" in str(exc).lower():
                quota_guard_hit = True
                logger.warning("Stopping further searches: %s", exc)
            else:
                logger.exception("Error searching channel %s", ch["channel_id"])
        except Exception:
            logger.exception("Error searching channel %s", ch["channel_id"])

    # 2) Keyword-based
    for kw in SEARCH_KEYWORDS:
        if quota_guard_hit:
            break
        try:
            ids = search_by_keyword(kw, cutoff)
            all_ids.update(ids)
            logger.info("Keyword '%s': %d videos", kw, len(ids))
        except QuotaExceededError as exc:
            quota_guard_hit = True
            logger.warning("Stopping further searches: %s", exc)
        except RuntimeError as exc:
            if "quota guard" in str(exc).lower():
                quota_guard_hit = True
                logger.warning("Stopping further searches: %s", exc)
            else:
                logger.exception("Error searching keyword '%s'", kw)
        except Exception:
            logger.exception("Error searching keyword '%s'", kw)

    logger.info("Total unique video IDs discovered: %d", len(all_ids))
    return list(all_ids)

def _is_valid_clip(detail: dict) -> bool:
    """Return True if duration is valid and the video is explicitly a clip."""
    d = detail["duration_seconds"]
    text = " ".join(
        [
            detail.get("title", ""),
            detail.get("tags_text", ""),
        ]
    )
    has_clip_keyword = "切り抜き" in text
    return MIN_DURATION_SECONDS <= d <= MAX_DURATION_SECONDS and has_clip_keyword


def _channel_group_map(channels: list[dict]) -> dict[str, str]:
    """Build a channel_id → group_name lookup."""
    return {ch["channel_id"]: ch["group_name"] for ch in channels}


def _infer_group_name(detail: dict, group_map: dict[str, str]) -> str:
    """Infer a group name from channel mapping, title, tags, and channel name."""
    known_group = group_map.get(detail["channel_id"], "")
    if known_group:
        return known_group

    haystack = " ".join(
        [
            detail.get("title", ""),
            detail.get("tags_text", ""),
            detail.get("channel_name", ""),
        ]
    ).lower()

    for group_name, keywords in GROUP_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            return group_name

    return "other"


def store_new_videos(details: list[dict]) -> int:
    """Insert new videos, skipping duplicates. Returns count inserted."""
    channels = load_channels()
    group_map = _channel_group_map(channels)

    query = """
        INSERT INTO videos
            (video_id, title, channel_id, channel_name, group_name,
             published_at, duration_seconds, tags_text, channel_icon_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (video_id) DO UPDATE SET
            title = EXCLUDED.title,
            channel_id = EXCLUDED.channel_id,
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            published_at = EXCLUDED.published_at,
            duration_seconds = EXCLUDED.duration_seconds,
            tags_text = EXCLUDED.tags_text,
            channel_icon_url = EXCLUDED.channel_icon_url
    """
    rows: list[tuple] = []
    for d in details:
        group_name = _infer_group_name(d, group_map)
        rows.append(
            (
                d["video_id"],
                d["title"],
                d["channel_id"],
                d["channel_name"],
                group_name,
                d["published_at"],
                d["duration_seconds"],
                d.get("tags_text", ""),
                d.get("channel_icon_url", ""),
            )
        )

    if rows:
        execute_many(query, rows)
    logger.info("Stored %d new video(s).", len(rows))
    return len(rows)


# ── Main entry point ────────────────────────────────────────────────
def run_collector() -> None:
    """Full collection pipeline."""
    logger.info("=== Collector started ===")

    # Ensure seed channels exist
    seed_channels()

    # Discover video IDs
    video_ids = discover_videos()
    if not video_ids:
        logger.info("No new videos discovered.")
        return

    # Fetch details in batches
    details = get_video_details(video_ids)

    # Filter by duration
    valid = [d for d in details if _is_valid_clip(d)]
    logger.info(
        "Duration filter: %d / %d passed (%ds–%ds)",
        len(valid),
        len(details),
        MIN_DURATION_SECONDS,
        MAX_DURATION_SECONDS,
    )

    # Store
    store_new_videos(valid)
    logger.info("=== Collector finished ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_collector()



