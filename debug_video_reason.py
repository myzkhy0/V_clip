"""
debug_video_reason.py

Diagnose why a specific YouTube video was not included in ranking data.

Usage:
  .venv\\Scripts\\python.exe debug_video_reason.py <video_id>
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from collector import _classify_content_type, _is_valid_clip
from config import SEARCH_KEYWORDS, TRACK_DAYS
from db import fetchone
from youtube_client import get_video_details


def _yn(value: bool) -> str:
    return "YES" if value else "NO"


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python debug_video_reason.py <video_id>")
        raise SystemExit(2)

    video_id = sys.argv[1].strip()
    if not video_id:
        print("video_id is empty")
        raise SystemExit(2)

    details = get_video_details([video_id])
    if not details:
        print(f"[ERROR] video_id={video_id} was not returned by videos.list")
        raise SystemExit(1)
    d = details[0]

    title = d.get("title", "")
    tags_text = d.get("tags_text", "")
    text = f"{title} {tags_text}".lower()
    published_at = d.get("published_at")
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_DAYS)
    within_track_days = bool(published_at and published_at >= cutoff)
    has_clip_keyword = "切り抜き" in text
    classified = _classify_content_type(d)
    valid_clip = _is_valid_clip(d)

    db_video = fetchone(
        """
        SELECT video_id, content_type, group_name, published_at
        FROM videos
        WHERE video_id = %s
        """,
        (video_id,),
    )
    db_channel = fetchone(
        """
        SELECT channel_id, channel_name, is_tracked, empty_streak, paused_until
        FROM channels
        WHERE channel_id = %s
        """,
        (d.get("channel_id", ""),),
    )

    matching_keywords = [kw for kw in SEARCH_KEYWORDS if kw.lower() in text]

    print("=== Video Diagnose ===")
    print(f"video_id: {video_id}")
    print(f"title: {title}")
    print(f"channel: {d.get('channel_name', '')} ({d.get('channel_id', '')})")
    print(f"published_at(UTC): {published_at}")
    print(f"duration_seconds: {d.get('duration_seconds', 0)}")
    print("")
    print("=== Filter Results ===")
    print(f"within TRACK_DAYS({TRACK_DAYS}): {_yn(within_track_days)}")
    print(f"has '切り抜き' in title/tags: {_yn(has_clip_keyword)}")
    print(f"_is_valid_clip: {_yn(valid_clip)}")
    print(f"classified content_type: {classified}")
    print("")
    print("=== Discovery Hints ===")
    print(f"keyword matched (exact include): {_yn(len(matching_keywords) > 0)}")
    if matching_keywords:
        print("matched keywords:")
        for kw in matching_keywords:
            print(f"  - {kw}")
    else:
        print("matched keywords: none")
    print("")
    print("=== DB State ===")
    print(f"video already stored: {_yn(db_video is not None)}")
    if db_video:
        print(f"stored content_type: {db_video['content_type']}")
        print(f"stored group_name: {db_video['group_name']}")
        print(f"stored published_at: {db_video['published_at']}")
    print(f"channel row exists: {_yn(db_channel is not None)}")
    if db_channel:
        print(f"is_tracked: {db_channel['is_tracked']}")
        print(f"empty_streak: {db_channel['empty_streak']}")
        print(f"paused_until: {db_channel['paused_until']}")


if __name__ == "__main__":
    main()
