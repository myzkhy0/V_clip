"""
youtube_client.py — YouTube Data API v3 wrapper.

Provides helpers to search for videos by channel / keyword and to
retrieve video details (duration, view count, like count, comment count).
"""

import json
import logging
import re
import ssl
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    SEARCH_MAX_RESULTS,
    VIDEO_BATCH_SIZE,
    YOUTUBE_API_KEY,
    YOUTUBE_DAILY_SEARCH_UNIT_LIMIT,
    YOUTUBE_QUOTA_STATE_FILE,
    YOUTUBE_SEARCH_UNIT_COST,
)

logger = logging.getLogger(__name__)
_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_REQUEST_RETRY_MAX_ATTEMPTS = 4
_REQUEST_RETRY_BASE_SECONDS = 1.0
_REQUEST_RETRY_MAX_SECONDS = 8.0


class QuotaExceededError(RuntimeError):
    """Raised when YouTube Data API daily quota is exceeded."""


# ── Lazy-init API resource ──────────────────────────────────────────
# googleapiclient Resource keeps an internal HTTP transport that is not
# thread-safe. APScheduler can run jobs concurrently, so keep one client
# per thread to avoid cross-thread transport reuse.
_youtube_local = threading.local()


def _get_youtube():
    """Return (and cache) the youtube API resource."""
    youtube = getattr(_youtube_local, "resource", None)
    if youtube is None:
        if not YOUTUBE_API_KEY:
            raise RuntimeError("YOUTUBE_API_KEY is not set. Check your .env file.")
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        _youtube_local.resource = youtube
    return youtube


def _is_quota_exceeded_http_error(exc: HttpError) -> bool:
    """Return True when HttpError indicates quota exhaustion."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status != 403:
        return False
    text = str(exc).lower()
    return "quota" in text and "exceeded" in text


def _is_transient_request_error(exc: Exception) -> bool:
    """Return True for transient transport errors worth retrying."""
    if isinstance(exc, ssl.SSLError):
        return True
    text = str(exc).lower()
    transient_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "server not found",
        "wrong version number",
        "decryption failed or bad record mac",
        "tls",
        "ssl",
    )
    return any(marker in text for marker in transient_markers)


def _retry_sleep_seconds(attempt: int) -> float:
    """Exponential backoff in seconds for request retries."""
    return min(_REQUEST_RETRY_MAX_SECONDS, _REQUEST_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))


def _execute_request(request, context: str):
    """Execute a googleapiclient request with normalized quota errors and retries."""
    for attempt in range(1, _REQUEST_RETRY_MAX_ATTEMPTS + 1):
        try:
            return request.execute()
        except HttpError as exc:
            if _is_quota_exceeded_http_error(exc):
                raise QuotaExceededError(
                    f"YouTube API quota exceeded during {context}."
                ) from exc
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status in _TRANSIENT_HTTP_STATUSES and attempt < _REQUEST_RETRY_MAX_ATTEMPTS:
                sleep_s = _retry_sleep_seconds(attempt)
                logger.warning(
                    "Transient HttpError during %s (status=%s, attempt %d/%d). Retrying in %.1fs.",
                    context,
                    status,
                    attempt,
                    _REQUEST_RETRY_MAX_ATTEMPTS,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            raise
        except Exception as exc:
            if _is_transient_request_error(exc) and attempt < _REQUEST_RETRY_MAX_ATTEMPTS:
                sleep_s = _retry_sleep_seconds(attempt)
                logger.warning(
                    "Transient API transport error during %s (%s, attempt %d/%d). Retrying in %.1fs.",
                    context,
                    exc.__class__.__name__,
                    attempt,
                    _REQUEST_RETRY_MAX_ATTEMPTS,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            raise


def _quota_state_path() -> Path:
    """Return path for local quota usage state."""
    return Path(YOUTUBE_QUOTA_STATE_FILE)


def _quota_day_key(now_utc: datetime | None = None) -> str:
    """Return day key in America/Los_Angeles timezone (YouTube quota reset basis)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return now_utc.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _load_quota_state() -> dict:
    """Load local quota tracking state."""
    path = _quota_state_path()
    if not path.exists():
        return {"day_key": _quota_day_key(), "search_units_used": 0}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Quota state file is invalid. Resetting: %s", path)
        return {"day_key": _quota_day_key(), "search_units_used": 0}

    if payload.get("day_key") != _quota_day_key():
        return {"day_key": _quota_day_key(), "search_units_used": 0}

    used = int(payload.get("search_units_used", 0))
    return {"day_key": payload["day_key"], "search_units_used": max(0, used)}


def _save_quota_state(state: dict) -> None:
    """Persist local quota tracking state."""
    path = _quota_state_path()
    path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def reserve_search_quota(context: str = "search.list") -> None:
    """Reserve YouTube search quota units and raise if daily guard is exceeded."""
    if YOUTUBE_DAILY_SEARCH_UNIT_LIMIT <= 0:
        return

    state = _load_quota_state()
    next_used = state["search_units_used"] + YOUTUBE_SEARCH_UNIT_COST

    if next_used > YOUTUBE_DAILY_SEARCH_UNIT_LIMIT:
        raise RuntimeError(
            "Daily search quota guard hit "
            f"({state['search_units_used']}/{YOUTUBE_DAILY_SEARCH_UNIT_LIMIT}). "
            "Skip additional search.list calls until quota reset."
        )

    state["search_units_used"] = next_used
    _save_quota_state(state)
    logger.info(
        "Quota guard: reserved %d unit(s) for %s (%d/%d today)",
        YOUTUBE_SEARCH_UNIT_COST,
        context,
        state["search_units_used"],
        YOUTUBE_DAILY_SEARCH_UNIT_LIMIT,
    )


# ── ISO 8601 duration → seconds ─────────────────────────────────────
_ISO_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _parse_duration(iso: str) -> int:
    """Convert ISO 8601 duration (e.g. 'PT1M30S') into total seconds."""
    m = _ISO_DUR_RE.match(iso)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_channel_identifier(identifier: str) -> str:
    """
    Resolve a seed identifier into a canonical channel ID.

    Supports plain channel IDs and @handles.
    """
    if identifier.startswith("UC"):
        return identifier

    handle = identifier.lstrip("@")
    youtube = _get_youtube()

    try:
        request = youtube.channels().list(
            part="id,snippet",
            forHandle=handle,
            maxResults=1,
        )
        response = _execute_request(request, f"resolve_channel_identifier:{handle}:channels")
        items = response.get("items", [])
        if items:
            return items[0]["id"]
    except HttpError:
        logger.warning("channels.list(forHandle=%s) failed; falling back to search", handle)

    reserve_search_quota(f"resolve_channel_identifier:{handle}")
    request = youtube.search().list(
        part="snippet",
        q=handle,
        type="channel",
        maxResults=5,
    )
    response = _execute_request(request, f"resolve_channel_identifier:{handle}:search")
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Channel identifier could not be resolved: {identifier}")
    return items[0]["snippet"]["channelId"]


def get_uploads_playlist_id(channel_id: str) -> str | None:
    """Fetch uploads playlist ID for a channel via channels.list(contentDetails)."""
    youtube = _get_youtube()
    request = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
        maxResults=1,
    )
    response = _execute_request(request, f"channels.list:uploads:{channel_id}")
    items = response.get("items", [])
    if not items:
        return None
    return (
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )


# ── Search helpers ───────────────────────────────────────────────────

def search_by_channel(
    channel_id: str,
    published_after: datetime,
    max_results: int = SEARCH_MAX_RESULTS,
    uploads_playlist_id: str | None = None,
) -> list[str]:
    """Return recent video IDs from channel uploads playlist after *published_after*."""
    youtube = _get_youtube()
    video_ids: list[str] = []
    page_token: str | None = None

    playlist_id = uploads_playlist_id or get_uploads_playlist_id(channel_id)
    if not playlist_id:
        logger.warning("uploads playlist not found for channel %s", channel_id)
        return []

    while True:
        request = youtube.playlistItems().list(
            part="contentDetails,snippet",
            playlistId=playlist_id,
            maxResults=min(max_results, 50),
            pageToken=page_token,
        )
        response = _execute_request(request, f"playlistItems.list:{channel_id}")

        reached_older = False
        for item in response.get("items", []):
            published = (
                item.get("contentDetails", {}).get("videoPublishedAt")
                or item.get("snippet", {}).get("publishedAt", "")
            )
            published_at = _parse_datetime(published)
            if published_at and published_at < published_after:
                reached_older = True
                continue

            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = response.get("nextPageToken")
        if reached_older or not page_token or len(video_ids) >= max_results:
            break

    deduped = list(dict.fromkeys(video_ids))
    logger.info(
        "search_by_channel(%s via uploads %s): found %d videos",
        channel_id,
        playlist_id,
        len(deduped),
    )
    return deduped[:max_results]


def search_by_keyword(
    keyword: str,
    published_after: datetime,
    max_results: int = SEARCH_MAX_RESULTS,
) -> list[str]:
    """Return video IDs matching *keyword* published after *published_after*."""
    youtube = _get_youtube()
    video_ids: list[str] = []
    page_token: str | None = None

    while True:
        reserve_search_quota(f"search_by_keyword:{keyword}")
        request = youtube.search().list(
            part="id",
            q=keyword,
            type="video",
            order="date",
            publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=min(max_results, 50),
            pageToken=page_token,
        )
        response = _execute_request(request, f"search_by_keyword:{keyword}")

        for item in response.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = response.get("nextPageToken")
        if not page_token or len(video_ids) >= max_results:
            break

    logger.info("search_by_keyword('%s'): found %d videos", keyword, len(video_ids))
    return video_ids


def _fetch_channel_profile_map(channel_ids: list[str]) -> dict[str, dict]:
    """Fetch channel profile fields (icon URL, subscriber count) by channel ID."""
    youtube = _get_youtube()
    profile_map: dict[str, dict] = {}
    unique_ids = [cid for cid in dict.fromkeys(channel_ids) if cid]

    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        request = youtube.channels().list(
            part="snippet,statistics",
            id=",".join(batch),
            maxResults=50,
        )
        response = _execute_request(request, "channels.list:profile_map")

        for item in response.get("items", []):
            channel_id = item.get("id", "")
            thumbnails = item.get("snippet", {}).get("thumbnails", {})
            stats = item.get("statistics", {})
            icon_url = (
                thumbnails.get("default", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or ""
            )
            subscriber_count = int(stats.get("subscriberCount", 0) or 0)
            if channel_id:
                profile_map[channel_id] = {
                    "icon_url": icon_url,
                    "subscriber_count": subscriber_count,
                }

    return profile_map


# ── Video details ────────────────────────────────────────────────────

def get_video_details(video_ids: list[str]) -> list[dict]:
    """
    Fetch details for a list of video IDs.

    Returns a list of dicts:
        {
            "video_id": str,
            "title": str,
            "channel_id": str,
            "channel_name": str,
            "published_at": datetime,
            "duration_seconds": int,
            "view_count": int,
            "like_count": int,
            "comment_count": int,
            "tags_text": str,
            "channel_icon_url": str,
            "channel_subscriber_count": int,
            "description": str,
            "live_broadcast_content": str,
            "live_actual_start_time": datetime | None,
            "live_actual_end_time": datetime | None,
        }

    Handles batching in chunks of VIDEO_BATCH_SIZE (max 50).
    """
    youtube = _get_youtube()
    raw_items: list[dict] = []

    for i in range(0, len(video_ids), VIDEO_BATCH_SIZE):
        batch = video_ids[i : i + VIDEO_BATCH_SIZE]
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics,liveStreamingDetails",
            id=",".join(batch),
        )
        response = _execute_request(request, "videos.list:details")
        raw_items.extend(response.get("items", []))

    channel_ids = [item.get("snippet", {}).get("channelId", "") for item in raw_items]
    profile_map = _fetch_channel_profile_map(channel_ids)

    results: list[dict] = []
    for item in raw_items:
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        live_details = item.get("liveStreamingDetails", {})
        tags = snippet.get("tags", [])
        channel_id = snippet.get("channelId", "")

        published_str = snippet.get("publishedAt", "")
        published_at = _parse_datetime(published_str) or datetime.now(timezone.utc)

        results.append(
            {
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "channel_id": channel_id,
                "channel_name": snippet.get("channelTitle", ""),
                "published_at": published_at,
                "duration_seconds": _parse_duration(content.get("duration", "PT0S")),
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "tags_text": " ".join(tags),
                "channel_icon_url": str(profile_map.get(channel_id, {}).get("icon_url") or ""),
                "channel_subscriber_count": int(profile_map.get(channel_id, {}).get("subscriber_count", 0) or 0),
                "description": snippet.get("description", ""),
                "live_broadcast_content": snippet.get("liveBroadcastContent", ""),
                "live_actual_start_time": _parse_datetime(live_details.get("actualStartTime", "")),
                "live_actual_end_time": _parse_datetime(live_details.get("actualEndTime", "")),
            }
        )

    logger.info("get_video_details: fetched %d / %d", len(results), len(video_ids))
    return results


# ── Example usage (standalone) ──────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    # Example: search by keyword
    ids = search_by_keyword("vtuber clip", cutoff, max_results=5)
    print("Video IDs:", ids)

    if ids:
        details = get_video_details(ids)
        for d in details:
            print(
                f"  {d['video_id']}  {d['duration_seconds']:>4}s  "
                f"{d['view_count']:>8} views  {d['title'][:60]}"
            )


