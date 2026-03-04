"""
youtube_client.py — YouTube Data API v3 wrapper.

Provides helpers to search for videos by channel / keyword and to
retrieve video details (duration, view count, like count).
"""

import re
import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import YOUTUBE_API_KEY, SEARCH_MAX_RESULTS, VIDEO_BATCH_SIZE

logger = logging.getLogger(__name__)

# ── Lazy-init API resource ──────────────────────────────────────────
_youtube = None


def _get_youtube():
    """Return (and cache) the youtube API resource."""
    global _youtube
    if _youtube is None:
        if not YOUTUBE_API_KEY:
            raise RuntimeError("YOUTUBE_API_KEY is not set. Check your .env file.")
        _youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    return _youtube


# ── ISO 8601 duration → seconds ─────────────────────────────────────
_ISO_DUR_RE = re.compile(
    r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
)


def _parse_duration(iso: str) -> int:
    """Convert ISO 8601 duration (e.g. 'PT1M30S') into total seconds."""
    m = _ISO_DUR_RE.match(iso)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


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
        response = youtube.channels().list(
            part="id,snippet",
            forHandle=handle,
            maxResults=1,
        ).execute()
        items = response.get("items", [])
        if items:
            return items[0]["id"]
    except HttpError:
        logger.warning("channels.list(forHandle=%s) failed; falling back to search", handle)

    response = youtube.search().list(
        part="snippet",
        q=handle,
        type="channel",
        maxResults=5,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Channel identifier could not be resolved: {identifier}")
    return items[0]["snippet"]["channelId"]


# ── Search helpers ───────────────────────────────────────────────────

def search_by_channel(
    channel_id: str,
    published_after: datetime,
    max_results: int = SEARCH_MAX_RESULTS,
) -> list[str]:
    """Return video IDs uploaded to *channel_id* after *published_after*."""
    youtube = _get_youtube()
    video_ids: list[str] = []
    page_token: str | None = None

    while True:
        request = youtube.search().list(
            part="id",
            channelId=channel_id,
            type="video",
            order="date",
            publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=min(max_results, 50),
            pageToken=page_token,
        )
        response = request.execute()

        for item in response.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = response.get("nextPageToken")
        if not page_token or len(video_ids) >= max_results:
            break

    logger.info("search_by_channel(%s): found %d videos", channel_id, len(video_ids))
    return video_ids


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
        request = youtube.search().list(
            part="id",
            q=keyword,
            type="video",
            order="date",
            publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=min(max_results, 50),
            pageToken=page_token,
        )
        response = request.execute()

        for item in response.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = response.get("nextPageToken")
        if not page_token or len(video_ids) >= max_results:
            break

    logger.info("search_by_keyword('%s'): found %d videos", keyword, len(video_ids))
    return video_ids


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
            "tags_text": str,
        }

    Handles batching in chunks of VIDEO_BATCH_SIZE (max 50).
    """
    youtube = _get_youtube()
    results: list[dict] = []

    for i in range(0, len(video_ids), VIDEO_BATCH_SIZE):
        batch = video_ids[i : i + VIDEO_BATCH_SIZE]
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(batch),
        )
        response = request.execute()

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            stats = item.get("statistics", {})
            tags = snippet.get("tags", [])

            published_str = snippet.get("publishedAt", "")
            try:
                published_at = datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                )
            except ValueError:
                published_at = datetime.now(timezone.utc)

            results.append(
                {
                    "video_id": item["id"],
                    "title": snippet.get("title", ""),
                    "channel_id": snippet.get("channelId", ""),
                    "channel_name": snippet.get("channelTitle", ""),
                    "published_at": published_at,
                    "duration_seconds": _parse_duration(
                        content.get("duration", "PT0S")
                    ),
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)),
                    "tags_text": " ".join(tags),
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
