"""
collector.py — Discover new VTuber clip videos and store them.

Workflow:
  1. Load channels from the DB (seed them on first run).
  2. Discover by channel uploads playlist (low-quota).
  3. Discover by keyword search (high-quota, only in discovery mode).
  4. De-duplicate video IDs.
  5. Fetch video details & filter by duration.
  6. Insert new videos into the `videos` table.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import (
    CHANNEL_EMPTY_STREAK_PAUSE_THRESHOLD,
    CHANNEL_PAUSE_HOURS,
    GROUP_KEYWORDS,
    MIN_DURATION_SECONDS,
    KEYWORD_ROTATION_STATE_FILE,
    KEYWORD_MAX_RESULTS_OVERRIDE,
    KEYWORD_SEARCH_BATCH_SIZE,
    SEARCH_KEYWORDS,
    SEARCH_MAX_RESULTS,
    SEED_CHANNELS,
    SHORTS_MAX_SECONDS,
    SHORTS_TAG_KEYWORD,
    TRACK_DAYS,
)
from db import execute, execute_many, fetchall
from youtube_client import (
    QuotaExceededError,
    get_uploads_playlist_id,
    get_video_details,
    resolve_channel_identifier,
    search_by_channel,
    search_by_keyword,
)

logger = logging.getLogger(__name__)


# ── Seed channels (first-run helper) ────────────────────────────────
def seed_channels() -> None:
    """Insert SEED_CHANNELS into the `channels` table if they don't exist."""
    if not SEED_CHANNELS:
        return

    resolved_rows: list[tuple[str, str, str, str, bool]] = []
    for channel_identifier, channel_name, group_name in SEED_CHANNELS:
        try:
            channel_id = resolve_channel_identifier(channel_identifier)
            uploads_playlist_id = get_uploads_playlist_id(channel_id) or ""
            resolved_rows.append((channel_id, channel_name, group_name, uploads_playlist_id, True))
        except Exception:
            logger.exception("Failed to resolve seed channel %s", channel_identifier)

    if not resolved_rows:
        logger.warning("No seed channels could be resolved.")
        return

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            uploads_playlist_id = CASE
                WHEN COALESCE(channels.uploads_playlist_id, '') = '' THEN EXCLUDED.uploads_playlist_id
                ELSE channels.uploads_playlist_id
            END,
            is_tracked = TRUE
    """
    execute_many(query, resolved_rows)
    logger.info("Seeded %d channel(s).", len(resolved_rows))


# ── Load channels from DB ───────────────────────────────────────────
def load_channels(tracked_only: bool = False) -> list[dict]:
    """Return rows from the `channels` table."""
    where_clause = "WHERE is_tracked = TRUE" if tracked_only else ""
    try:
        return fetchall(
            f"""
            SELECT
                channel_id,
                channel_name,
                group_name,
                COALESCE(uploads_playlist_id, '') AS uploads_playlist_id,
                is_tracked,
                COALESCE(empty_streak, 0) AS empty_streak,
                last_checked_at,
                paused_until
            FROM channels
            {where_clause}
            """
        )
    except Exception:
        logger.warning("channels metadata columns not available yet; using legacy channel query")
        rows = fetchall("SELECT channel_id, channel_name, group_name FROM channels")
        for row in rows:
            row["uploads_playlist_id"] = ""
            row["is_tracked"] = True
            row["empty_streak"] = 0
            row["last_checked_at"] = None
            row["paused_until"] = None
        return rows


def _update_channel_uploads_playlist(channel_id: str, uploads_playlist_id: str) -> None:
    if not uploads_playlist_id:
        return
    execute(
        """
        UPDATE channels
        SET uploads_playlist_id = %s
        WHERE channel_id = %s
        """,
        (uploads_playlist_id, channel_id),
    )



def _should_skip_channel(ch: dict, now_utc: datetime) -> bool:
    paused_until = ch.get("paused_until")
    if not paused_until:
        return False
    if paused_until.tzinfo is None:
        paused_until = paused_until.replace(tzinfo=timezone.utc)
    return paused_until > now_utc


def _mark_channel_checked(channel_id: str, found_count: int, now_utc: datetime) -> None:
    if found_count > 0:
        execute(
            """
            UPDATE channels
            SET empty_streak = 0,
                last_checked_at = %s,
                paused_until = NULL
            WHERE channel_id = %s
            """,
            (now_utc, channel_id),
        )
        return

    row = fetchall(
        "SELECT COALESCE(empty_streak, 0) AS empty_streak FROM channels WHERE channel_id = %s",
        (channel_id,),
    )
    current = int(row[0]["empty_streak"]) if row else 0
    next_streak = current + 1
    pause_until = None
    if next_streak >= CHANNEL_EMPTY_STREAK_PAUSE_THRESHOLD:
        pause_until = now_utc + timedelta(hours=max(1, CHANNEL_PAUSE_HOURS))

    execute(
        """
        UPDATE channels
        SET empty_streak = %s,
            last_checked_at = %s,
            paused_until = %s
        WHERE channel_id = %s
        """,
        (next_streak, now_utc, pause_until, channel_id),
    )

def _load_keyword_rotation_state() -> int:
    """Load keyword rotation offset from local state file."""
    path = Path(KEYWORD_ROTATION_STATE_FILE)
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("offset", 0)))
    except (OSError, ValueError, TypeError):
        return 0


def _save_keyword_rotation_state(offset: int) -> None:
    """Persist keyword rotation offset."""
    path = Path(KEYWORD_ROTATION_STATE_FILE)
    payload = {"offset": max(0, int(offset))}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _select_keywords_for_cycle(keywords: list[str]) -> list[str]:
    """Select a rotating keyword batch for this collector cycle."""
    if not keywords:
        return []

    batch_size = max(1, KEYWORD_SEARCH_BATCH_SIZE)
    if batch_size >= len(keywords):
        return keywords

    offset = _load_keyword_rotation_state() % len(keywords)
    selected = [keywords[(offset + i) % len(keywords)] for i in range(batch_size)]
    next_offset = (offset + batch_size) % len(keywords)

    try:
        _save_keyword_rotation_state(next_offset)
    except OSError:
        logger.exception("Failed to save keyword rotation state.")

    logger.info(
        "Keyword rotation: using %d/%d keyword(s), offset=%d -> %d",
        len(selected),
        len(keywords),
        offset,
        next_offset,
    )
    return selected


# ── Discover videos ─────────────────────────────────────────────────
def discover_videos(
    include_channel_search: bool = True,
    include_keyword_search: bool = True,
) -> list[str]:
    """
    Collect video IDs from channel-based and/or keyword-based search.

    Returns a de-duplicated list of video IDs.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_DAYS)
    all_ids: set[str] = set()
    quota_guard_hit = False

    # 1) Channel-based search (uploads playlist + playlistItems.list)
    if include_channel_search:
        channels = load_channels(tracked_only=True)
        now_utc = datetime.now(timezone.utc)
        for ch in channels:
            if quota_guard_hit:
                break
            if _should_skip_channel(ch, now_utc):
                logger.info(
                    "Channel %s (%s): skipped until %s",
                    ch["channel_name"],
                    ch["group_name"],
                    ch.get("paused_until"),
                )
                continue

            try:
                uploads_playlist_id = (ch.get("uploads_playlist_id") or "").strip()
                if not uploads_playlist_id:
                    uploads_playlist_id = get_uploads_playlist_id(ch["channel_id"]) or ""
                    if uploads_playlist_id:
                        _update_channel_uploads_playlist(ch["channel_id"], uploads_playlist_id)

                ids = search_by_channel(
                    ch["channel_id"],
                    cutoff,
                    uploads_playlist_id=uploads_playlist_id,
                )
                all_ids.update(ids)
                logger.info(
                    "Channel %s (%s): %d videos",
                    ch["channel_name"],
                    ch["group_name"],
                    len(ids),
                )
                _mark_channel_checked(ch["channel_id"], len(ids), now_utc)
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
    # 2) Keyword-based search (rotating subset to reduce search.list usage)
    if include_keyword_search:
        cycle_keywords = _select_keywords_for_cycle(SEARCH_KEYWORDS)
        for kw in cycle_keywords:
            if quota_guard_hit:
                break
            try:
                keyword_max_results = max(1, int(KEYWORD_MAX_RESULTS_OVERRIDE.get(kw, SEARCH_MAX_RESULTS)))
                ids = search_by_keyword(kw, cutoff, max_results=keyword_max_results)
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

    logger.info(
        "Total unique video IDs discovered: %d (channels=%s, keywords=%s)",
        len(all_ids),
        include_channel_search,
        include_keyword_search,
    )
    return list(all_ids)


# ── Filter & store ──────────────────────────────────────────────────
def _is_valid_clip(detail: dict) -> bool:
    """Return True for clip videos (shorts/video split is done separately)."""
    text = " ".join([detail.get("title", ""), detail.get("tags_text", "")]).lower()
    return "切り抜き" in text


def _classify_content_type(detail: dict) -> str:
    """Classify detail into 'shorts' or 'video'."""
    duration = int(detail.get("duration_seconds", 0))
    text = " ".join([detail.get("title", ""), detail.get("tags_text", "")]).lower()
    has_shorts_keyword = SHORTS_TAG_KEYWORD in text or " shorts" in text
    if duration >= MIN_DURATION_SECONDS and (
        duration <= SHORTS_MAX_SECONDS or has_shorts_keyword
    ):
        return "shorts"
    return "video"


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



def _upsert_discovered_channels(details: list[dict], group_map: dict[str, str]) -> None:
    """Persist discovered source channels without adding them to hourly crawl targets."""
    seen: dict[str, tuple[str, str, str, str, bool]] = {}
    for detail in details:
        channel_id = detail.get("channel_id", "")
        if not channel_id:
            continue
        inferred_group = _infer_group_name(detail, group_map)
        group_map.setdefault(channel_id, inferred_group)
        if channel_id in seen:
            continue
        seen[channel_id] = (
            channel_id,
            detail.get("channel_name", "") or "",
            inferred_group,
            "",
            False,
        )

    rows = list(seen.values())
    if not rows:
        return

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = CASE
                WHEN COALESCE(channels.group_name, '') IN ('', 'other') THEN EXCLUDED.group_name
                ELSE channels.group_name
            END,
            uploads_playlist_id = CASE
                WHEN COALESCE(channels.uploads_playlist_id, '') = '' THEN EXCLUDED.uploads_playlist_id
                ELSE channels.uploads_playlist_id
            END
    """
    execute_many(query, rows)
    logger.info("Upserted %d discovered channel(s).", len(rows))

def store_new_videos(details: list[dict]) -> int:
    """Insert new videos, skipping duplicates. Returns count inserted."""
    channels = load_channels()
    group_map = _channel_group_map(channels)
    _upsert_discovered_channels(details, group_map)

    query = """
        INSERT INTO videos
            (video_id, title, channel_id, channel_name, group_name,
             published_at, duration_seconds, tags_text, channel_icon_url, content_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (video_id) DO UPDATE SET
            title = EXCLUDED.title,
            channel_id = EXCLUDED.channel_id,
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            published_at = EXCLUDED.published_at,
            duration_seconds = EXCLUDED.duration_seconds,
            tags_text = EXCLUDED.tags_text,
            channel_icon_url = EXCLUDED.channel_icon_url,
            content_type = EXCLUDED.content_type
    """
    rows: list[tuple] = []
    for d in details:
        group_name = _infer_group_name(d, group_map)
        content_type = _classify_content_type(d)
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
                content_type,
            )
        )

    if rows:
        execute_many(query, rows)
    logger.info("Stored %d new video(s).", len(rows))
    return len(rows)


# ── Main entry point ────────────────────────────────────────────────
def run_collector(
    include_channel_search: bool = True,
    include_keyword_search: bool = True,
    run_seed: bool = True,
) -> None:
    """Full collection pipeline."""
    logger.info("=== Collector started ===")

    # Ensure seed channels exist (discovery run only)
    if run_seed:
        seed_channels()

    # Discover video IDs
    video_ids = discover_videos(
        include_channel_search=include_channel_search,
        include_keyword_search=include_keyword_search,
    )
    if not video_ids:
        logger.info("No new videos discovered.")
        return

    # Fetch details in batches
    details = get_video_details(video_ids)

    # Filter by clip keyword
    valid = [d for d in details if _is_valid_clip(d)]
    logger.info("Clip filter: %d / %d passed (contains '切り抜き')", len(valid), len(details))

    # Store
    store_new_videos(valid)
    logger.info("=== Collector finished ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_collector()



