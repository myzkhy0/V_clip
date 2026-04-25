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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import (
    CHANNEL_EMPTY_STREAK_PAUSE_THRESHOLD,
    CHANNEL_PAUSE_HOURS,
    COLD_MANUAL_PROTECT_FILE,
    COLD_MIN_CHANNEL_AGE_DAYS,
    COLD_MIN_INACTIVE_DAYS,
    COLD_MIN_OBSERVED_VIDEOS,
    COLD_RECENT_GROWTH_7D_MAX,
    COLD_REFRESH_HOURS,
    ENABLE_COLD_SCHEDULING,
    GROUP_KEYWORDS,
    MIN_DURATION_SECONDS,
    KEYWORD_ROTATION_STATE_FILE,
    KEYWORD_PUBLISHED_AFTER_HOURS,
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
VSPO_PERMISSION_MARKER = "ぶいすぽっ！許諾番号"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── Seed channels (first-run helper) ────────────────────────────────
def seed_channels() -> None:
    """Insert SEED_CHANNELS into the `channels` table if they don't exist."""
    if not SEED_CHANNELS:
        return

    resolved_rows: list[tuple[str, str, str, str, bool, int]] = []
    for channel_identifier, channel_name, group_name in SEED_CHANNELS:
        try:
            channel_id = resolve_channel_identifier(channel_identifier)
            uploads_playlist_id = get_uploads_playlist_id(channel_id) or ""
            resolved_rows.append((channel_id, channel_name, group_name, uploads_playlist_id, True, 0))
        except Exception:
            logger.exception("Failed to resolve seed channel %s", channel_identifier)

    if not resolved_rows:
        logger.warning("No seed channels could be resolved.")
        return

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked, subscriber_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            uploads_playlist_id = CASE
                WHEN COALESCE(channels.uploads_playlist_id, '') = '' THEN EXCLUDED.uploads_playlist_id
                ELSE channels.uploads_playlist_id
            END,
            subscriber_count = CASE
                WHEN COALESCE(EXCLUDED.subscriber_count, 0) > 0 THEN EXCLUDED.subscriber_count
                ELSE COALESCE(channels.subscriber_count, 0)
            END,
            is_tracked = TRUE
    """
    execute_many(query, resolved_rows)
    logger.info("Seeded %d channel(s).", len(resolved_rows))



def backfill_channels_from_videos(limit: int = 5000) -> int:
    """Backfill channels from existing videos and mark them tracked."""
    rows = fetchall(
        """
        SELECT DISTINCT
            channel_id,
            channel_name,
            COALESCE(group_name, 'other') AS group_name
        FROM videos
        WHERE COALESCE(channel_id, '') <> ''
        ORDER BY channel_id
        LIMIT %s
        """,
        (max(1, int(limit)),),
    )
    if not rows:
        return 0

    payload = [
        (
            row["channel_id"],
            row.get("channel_name", "") or "",
            row.get("group_name", "other") or "other",
            "",
            True,
            0,
        )
        for row in rows
    ]

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked, subscriber_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = CASE
                WHEN COALESCE(channels.group_name, '') IN ('', 'other') THEN EXCLUDED.group_name
                ELSE channels.group_name
            END,
            subscriber_count = CASE
                WHEN COALESCE(EXCLUDED.subscriber_count, 0) > 0 THEN EXCLUDED.subscriber_count
                ELSE COALESCE(channels.subscriber_count, 0)
            END,
            is_tracked = TRUE
    """
    execute_many(query, payload)
    logger.info("Backfilled %d channel(s) from videos.", len(payload))
    return len(payload)

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


def _safe_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def _days_since(ts: datetime | None, now_utc: datetime) -> float | None:
    if ts is None:
        return None
    value = ts
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now_utc - value.astimezone(timezone.utc)).total_seconds() / 86400.0


def _load_manual_protect_ids(path_value: str) -> set[str]:
    if not path_value:
        return set()
    path = Path(path_value)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            ids.add(v)
    return ids


def _load_ranking_tables() -> list[str]:
    rows = fetchall(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        GROUP BY table_name
        HAVING
            SUM(CASE WHEN column_name = 'video_id' THEN 1 ELSE 0 END) > 0
            AND SUM(CASE WHEN column_name = 'calculated_at' THEN 1 ELSE 0 END) > 0
            AND table_name LIKE '%%ranking%%'
        ORDER BY table_name
        """
    )
    return [str(r["table_name"]) for r in rows]


def _build_cold_candidate_meta(channels: list[dict], now_utc: datetime) -> dict[str, dict]:
    if not ENABLE_COLD_SCHEDULING:
        return {}

    channel_ids = [str(ch.get("channel_id") or "") for ch in channels if ch.get("channel_id")]
    if not channel_ids:
        return {}

    protected_ids = _load_manual_protect_ids(COLD_MANUAL_PROTECT_FILE)
    ranking_tables = _load_ranking_tables()
    if not ranking_tables:
        logger.warning("Cold scheduling disabled for this run: ranking tables not found.")
        return {}

    ranking_union = " UNION ALL ".join(
        f"SELECT video_id FROM {_safe_ident(t)} WHERE calculated_at >= NOW() - INTERVAL '30 days'"
        for t in ranking_tables
    )

    sql = f"""
    WITH scoped AS (
      SELECT UNNEST(%s::text[]) AS channel_id
    ),
    video_base AS (
      SELECT
        v.channel_id,
        v.video_id,
        COALESCE(v.published_at, v.added_at) AS published_at
      FROM videos v
      JOIN scoped s ON s.channel_id = v.channel_id
    ),
    video_feat AS (
      SELECT
        channel_id,
        MAX(published_at) AS latest_video_published_at,
        COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '30 days') AS recent_video_count_30d
      FROM video_base
      GROUP BY channel_id
    ),
    growth_base AS (
      SELECT
        video_id,
        GREATEST(MAX(view_count) - MIN(view_count), 0) AS view_growth
      FROM video_stats
      WHERE timestamp >= NOW() - INTERVAL '7 days'
      GROUP BY video_id
    ),
    growth_7d AS (
      SELECT
        vb.channel_id,
        COALESCE(SUM(gb.view_growth), 0) AS recent_view_growth_7d
      FROM video_base vb
      LEFT JOIN growth_base gb ON gb.video_id = vb.video_id
      WHERE vb.published_at >= NOW() - INTERVAL '7 days'
      GROUP BY vb.channel_id
    ),
    ranking_30d AS (
      SELECT
        vb.channel_id,
        COUNT(DISTINCT r.video_id) AS ranking_count_30d
      FROM ({ranking_union}) r
      JOIN video_base vb ON vb.video_id = r.video_id
      GROUP BY vb.channel_id
    )
    SELECT
      s.channel_id,
      vf.latest_video_published_at,
      COALESCE(vf.recent_video_count_30d, 0) AS recent_video_count_30d,
      COALESCE(g7.recent_view_growth_7d, 0) AS recent_view_growth_7d,
      COALESCE(r30.ranking_count_30d, 0) AS ranking_count_30d
    FROM scoped s
    LEFT JOIN video_feat vf ON vf.channel_id = s.channel_id
    LEFT JOIN growth_7d g7 ON g7.channel_id = s.channel_id
    LEFT JOIN ranking_30d r30 ON r30.channel_id = s.channel_id
    """
    feature_rows = fetchall(sql, (channel_ids,))
    feature_by_id = {str(r["channel_id"]): r for r in feature_rows}

    meta: dict[str, dict] = {}
    for ch in channels:
        cid = str(ch.get("channel_id") or "")
        if not cid:
            continue
        if cid in protected_ids:
            meta[cid] = {"is_cold": False, "reason": "manual_protect"}
            continue

        fr = feature_by_id.get(cid, {})
        ranking_30d = int(fr.get("ranking_count_30d") or 0)
        growth_7d = int(fr.get("recent_view_growth_7d") or 0)
        recent_30d = int(fr.get("recent_video_count_30d") or 0)
        latest_days = _days_since(fr.get("latest_video_published_at"), now_utc)
        channel_age_days = _days_since(ch.get("channel_added_at"), now_utc)
        is_inactive = latest_days is None or latest_days >= COLD_MIN_INACTIVE_DAYS
        observed = recent_30d >= COLD_MIN_OBSERVED_VIDEOS or (
            channel_age_days is not None and channel_age_days >= COLD_MIN_CHANNEL_AGE_DAYS
        )

        is_cold = (
            ranking_30d == 0
            and growth_7d < COLD_RECENT_GROWTH_7D_MAX
            and is_inactive
            and observed
        )
        reason = (
            f"rank30={ranking_30d}, growth7d={growth_7d}, "
            f"inactive_days={latest_days if latest_days is not None else 'none'}"
        )
        meta[cid] = {"is_cold": is_cold, "reason": reason}

    return meta


def _cold_hold_skip_reason(ch: dict, now_utc: datetime, cold_meta: dict[str, dict]) -> str | None:
    if not ENABLE_COLD_SCHEDULING:
        return None
    cid = str(ch.get("channel_id") or "")
    info = cold_meta.get(cid)
    if not info or not info.get("is_cold"):
        return None

    last_checked = ch.get("last_checked_at")
    if not last_checked:
        return None
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)

    next_allowed = last_checked + timedelta(hours=max(1, int(COLD_REFRESH_HOURS)))
    if next_allowed > now_utc:
        remaining_hours = int((next_allowed - now_utc).total_seconds() // 3600)
        return (
            f"cold_hold until {next_allowed.isoformat()} "
            f"(remaining~{remaining_hours}h; {info.get('reason', '')})"
        )
    return None


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
    now_utc = datetime.now(timezone.utc)
    cutoff_channel = now_utc - timedelta(days=TRACK_DAYS)
    cutoff_keyword = now_utc - timedelta(hours=max(1, KEYWORD_PUBLISHED_AFTER_HOURS))
    all_ids: set[str] = set()
    quota_guard_hit = False

    # 1) Channel-based search (uploads playlist + playlistItems.list)
    if include_channel_search:
        channels = load_channels(tracked_only=True)
        cold_meta: dict[str, dict] = {}
        try:
            cold_meta = _build_cold_candidate_meta(channels, now_utc)
            if ENABLE_COLD_SCHEDULING:
                cold_count = sum(1 for v in cold_meta.values() if v.get("is_cold"))
                logger.info(
                    "Cold scheduling enabled: %d/%d channel(s) are cold candidates (refresh=%sh)",
                    cold_count,
                    len(channels),
                    COLD_REFRESH_HOURS,
                )
        except Exception:
            cold_meta = {}
            logger.exception("Failed to build cold candidate metadata; proceeding without cold hold.")

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
            cold_skip_reason = _cold_hold_skip_reason(ch, now_utc, cold_meta)
            if cold_skip_reason:
                logger.info(
                    "Channel %s (%s): %s",
                    ch["channel_name"],
                    ch["group_name"],
                    cold_skip_reason,
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
                    cutoff_channel,
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
                ids = search_by_keyword(kw, cutoff_keyword, max_results=keyword_max_results)
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
def _is_valid_clip(detail: dict, inferred_group: str = "") -> bool:
    """
    Return True only when:
      1) title/tags contain '切り抜き' OR (VSPO group and description has permission marker)
      2) title/tags contain at least one search-keyword stem
         (derived from SEARCH_KEYWORDS by removing '切り抜き')
      3) not a livestream archive:
         - strong: liveStreamingDetails.actualStartTime / actualEndTime exists
         - assist: liveBroadcastContent is live/upcoming
         - fallback: text has "にライブ配信" / "に配信済み" or "ライブ配信"
    """
    text = " ".join([detail.get("title", ""), detail.get("tags_text", "")]).lower()
    description = str(detail.get("description", ""))
    combined_text = " ".join([detail.get("title", ""), detail.get("tags_text", ""), description])
    is_vspo_group = (inferred_group or "").strip().lower() == "vspo"
    has_vspo_permission = VSPO_PERMISSION_MARKER in description
    has_clip_keyword = "切り抜き" in text
    if not has_clip_keyword and not (is_vspo_group and has_vspo_permission):
        return False

    if detail.get("live_actual_start_time") or detail.get("live_actual_end_time"):
        return False

    live_broadcast_content = str(detail.get("live_broadcast_content", "")).lower()
    if live_broadcast_content in {"live", "upcoming"}:
        return False

    if "にライブ配信" in combined_text or "に配信済み" in combined_text:
        return False

    description_lower = description.lower()
    if "ライブ配信" in description_lower:
        return False

    normalized = text.replace(" ", "").replace("　", "")
    stems: set[str] = set()
    for keyword in SEARCH_KEYWORDS:
        stem = keyword.replace("切り抜き", "").strip().lower()
        if not stem:
            continue
        stems.add(stem)
        compact = stem.replace(" ", "").replace("　", "")
        if compact:
            stems.add(compact)

    return any(stem in text or stem in normalized for stem in stems)

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
    """Persist discovered source channels and include them in tracked crawl targets."""
    seen: dict[str, tuple[str, str, str, str, bool, int]] = {}
    for detail in details:
        channel_id = detail.get("channel_id", "")
        if not channel_id:
            continue
        inferred_group = _infer_group_name(detail, group_map)
        group_map.setdefault(channel_id, inferred_group)
        if channel_id in seen:
            if int(detail.get("channel_subscriber_count") or 0) > int(seen[channel_id][5] or 0):
                channel_id_val, channel_name_val, inferred_group_val, uploads_playlist_id_val, is_tracked_val, _ = seen[channel_id]
                seen[channel_id] = (
                    channel_id_val,
                    channel_name_val,
                    inferred_group_val,
                    uploads_playlist_id_val,
                    is_tracked_val,
                    int(detail.get("channel_subscriber_count") or 0),
                )
            continue
        seen[channel_id] = (
            channel_id,
            detail.get("channel_name", "") or "",
            inferred_group,
            "",
            True,
            int(detail.get("channel_subscriber_count") or 0),
        )

    rows = list(seen.values())
    if not rows:
        return

    query = """
        INSERT INTO channels (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked, subscriber_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = CASE
                WHEN COALESCE(channels.group_name, '') IN ('', 'other') THEN EXCLUDED.group_name
                ELSE channels.group_name
            END,
            uploads_playlist_id = CASE
                WHEN COALESCE(channels.uploads_playlist_id, '') = '' THEN EXCLUDED.uploads_playlist_id
                ELSE channels.uploads_playlist_id
            END,
            subscriber_count = CASE
                WHEN COALESCE(EXCLUDED.subscriber_count, 0) > 0 THEN EXCLUDED.subscriber_count
                ELSE COALESCE(channels.subscriber_count, 0)
            END,
            is_tracked = TRUE
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
             published_at, duration_seconds, tags_text, description_text, channel_icon_url, content_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (video_id) DO UPDATE SET
            title = EXCLUDED.title,
            channel_id = EXCLUDED.channel_id,
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            published_at = EXCLUDED.published_at,
            duration_seconds = EXCLUDED.duration_seconds,
            tags_text = EXCLUDED.tags_text,
            description_text = EXCLUDED.description_text,
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
                d.get("description", ""),
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

    # Ensure existing video channels are promoted to tracked targets.
    try:
        backfill_channels_from_videos()
    except Exception:
        logger.exception("Failed to backfill channels from videos.")


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

    channels = load_channels()
    group_map = _channel_group_map(channels)

    # Filter by clip keyword or VSPO permission marker
    valid = [
        d
        for d in details
        if _is_valid_clip(d, _infer_group_name(d, group_map))
    ]
    logger.info(
        "Clip filter: %d / %d passed (requires '切り抜き' + SEARCH_KEYWORDS stem, or VSPO permission marker in description)",
        len(valid),
        len(details),
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




