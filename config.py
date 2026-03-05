"""
config.py — Central configuration for the VTuber Clip Ranking System.

Reads secrets from environment variables (.env supported via python-dotenv).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── YouTube Data API v3 ─────────────────────────────────────────────
YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")

# ── PostgreSQL ───────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/vclip",
)

# ── Video filtering ─────────────────────────────────────────────────
MIN_DURATION_SECONDS: int = 10
MAX_DURATION_SECONDS: int = 180   # 3 minutes

# ── Tracking window ─────────────────────────────────────────────────
TRACK_DAYS: int = 7               # Only collect stats for videos < 7 days old

# ── Search keywords ──────────────────────────────────────────────────
SEARCH_KEYWORDS: list[str] = [
    "ホロライブ 切り抜き",
    "にじさんじ 切り抜き",
    "ぶいすぽ 切り抜き",
    "ぶいすぽっ 切り抜き",
    "ネオポルテ 切り抜き",
    "ゆにれいど 切り抜き",
    "ななしいんく 切り抜き",
    "のりプロ 切り抜き",
    "ミリプロ 切り抜き",
    "あおぎり高校 切り抜き",
    "どっとライブ 切り抜き",
]

# ── Group detection keywords ──────────────────────────────────────────
GROUP_KEYWORDS: dict[str, list[str]] = {
    "hololive": [
        "hololive",
        "ホロライブ",
        "hololive production",
    ],
    "nijisanji": [
        "nijisanji",
        "にじさんじ",
        "2434",
    ],
    "VSPO": [
        "vspo",
        "vspo!",
        "ぶいすぽ",
        "ぶいすぽっ",
        "ぶいすぽっ！",
    ],
    "Neo-Porte": [
        "neo-porte",
        "neo porte",
        "ネオポルテ",
    ],
    "UniReid": [
        "uniraid",
        "uni reid",
        "ゆにれいど",
        "ゆにれいど！",
    ],
    "774inc": [
        "774inc",
        "774 inc",
        "ななしいんく",
        "774inc.",
    ],
    "NoriPro": [
        "のりプロ",
        "noripro",
        "nori pro",
    ],
    "MilliPro": [
        "ミリプロ",
        "millipro",
        "milli pro",
    ],
    "Aogiri": [
        "あおぎり高校",
        "aogiri",
        "aogiri high school",
    ],
    "DotLive": [
        "どっとライブ",
        ".live",
        "dotlive",
        "dot live",
    ],
}

# ── Default seed channels ────────────────────────────────────────────
# Each entry: (channel_id, channel_name, group_name)
# Add real channel IDs before first run.
SEED_CHANNELS: list[tuple[str, str, str]] = [
    # ── hololive ──
    ("UCJFZiqLMntJufDCHc6bQixg", "hololive ホロライブ", "hololive"),
    # ── nijisanji ──
    ("UCX7YkU9nEeaoZbkVLVajcMg", "にじさんじ", "nijisanji"),
    # ── VSPO ──
    ("UCuI5XaO-6VkOEhHao6ij7JA", "ぶいすぽっ!", "VSPO"),
    # ── Neo-Porte ──
    ("UC4yiPSMCYlHIEbeBbiCsOjg", "Neo-Porte", "Neo-Porte"),
    # ── UniReid ──
    ("@Uniraid_VTuber", "ゆにれいど！〖公式〗異世界VTuberプロジェクト", "UniReid"),
    # ── 774inc ──
    ("@774inc_official", "ななしいんく公式", "774inc"),
    # ── indie examples (replace with real IDs) ──
    # ("UCxxxxxxx", "Indie VTuber Name", "indie"),
]

# ── YouTube API search parameters ────────────────────────────────────
SEARCH_MAX_RESULTS: int = 50      # per request (API max is 50)
VIDEO_BATCH_SIZE: int = 50        # videos.list max per call
YOUTUBE_SEARCH_UNIT_COST: int = int(os.getenv("YOUTUBE_SEARCH_UNIT_COST", "100"))
YOUTUBE_DAILY_SEARCH_UNIT_LIMIT: int = int(
    os.getenv("YOUTUBE_DAILY_SEARCH_UNIT_LIMIT", "8000")
)
YOUTUBE_QUOTA_STATE_FILE: str = os.getenv(
    "YOUTUBE_QUOTA_STATE_FILE", ".youtube_quota_state.json"
)

# ── Scheduler ────────────────────────────────────────────────────────
COLLECTION_INTERVAL_MINUTES: int = 240  # run every 4 hours

