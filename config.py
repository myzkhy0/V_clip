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
MIN_DURATION_SECONDS: int = 5
MAX_DURATION_SECONDS: int = 180   # 3 minutes
SHORTS_MAX_SECONDS: int = 180
SHORTS_TAG_KEYWORD: str = "#shorts"

# ── Tracking window ─────────────────────────────────────────────────
TRACK_DAYS: int = 7               # Only collect stats for videos < 7 days old

# ── Search keywords ──────────────────────────────────────────────────
SEARCH_KEYWORDS: list[str] = [
    "ホロライブ 切り抜き",
    "にじさんじ 切り抜き",
    "ぶいすぽ 切り抜き",
    "ネオポルテ 切り抜き",
    "ゆにれいど 切り抜き",
    "ななしいんく 切り抜き",
    "のりプロ 切り抜き",
    "ミリプロ 切り抜き",
    "あおぎり高校 切り抜き",
    "どっとライブ 切り抜き",
    "パレプロ 切り抜き",
    "ホロスターズ 切り抜き",
    "すぺしゃりて 切り抜き",
    "REJECT 切り抜き",
    "RIOT MUSIC 切り抜き",
]

KEYWORD_MAX_RESULTS_OVERRIDE: dict[str, int] = {
    "ホロライブ 切り抜き": 100,
    "にじさんじ 切り抜き": 100,
    "ぶいすぽ 切り抜き": 100,
}
KEYWORD_SEARCH_BATCH_SIZE: int = int(
    os.getenv("KEYWORD_SEARCH_BATCH_SIZE", str(len(SEARCH_KEYWORDS)))
)
KEYWORD_ROTATION_STATE_FILE: str = os.getenv(
    "KEYWORD_ROTATION_STATE_FILE",
    ".keyword_rotation_state.json",
)
KEYWORD_PUBLISHED_AFTER_HOURS: int = int(os.getenv("KEYWORD_PUBLISHED_AFTER_HOURS", "24"))
CHANNEL_EMPTY_STREAK_PAUSE_THRESHOLD: int = int(
    os.getenv("CHANNEL_EMPTY_STREAK_PAUSE_THRESHOLD", "24")
)
CHANNEL_PAUSE_HOURS: int = int(os.getenv("CHANNEL_PAUSE_HOURS", "24"))

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
    "PaletteProject": [
        "パレプロ",
        "palette project",
        "paletteproject",
    ],
    "Holostars": [
        "ホロスターズ",
        "holostars",
        "ホロスタ",
    ],
    "Specialite": [
        "すぺしゃりて",
        "specialite",
        "すぺしゃ",
    ],
    "REJECT": [
        "reject",
        "REJECT",
    ],
    "RIOTMUSIC": [
        "riot music",
        "RIOT MUSIC",
        "riotmusic",
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
# ── Manual ranking exclusions ───────────────────────────────────────
EXCLUDED_CHANNELS_FILE: str = os.getenv("EXCLUDED_CHANNELS_FILE", "excluded_channels.txt")


# ── Scheduler ────────────────────────────────────────────────────────
COLLECTION_INTERVAL_MINUTES: int = 360  # backward compatibility
SEARCH_CRON_HOURS_JST: str = os.getenv("SEARCH_CRON_HOURS_JST", "6,18")
SEARCH_CRON_MINUTE_JST: int = int(os.getenv("SEARCH_CRON_MINUTE_JST", "0"))
CHANNEL_UPDATE_INTERVAL_HOURS: int = int(os.getenv("CHANNEL_UPDATE_INTERVAL_HOURS", "8"))
STATS_INTERVAL_HOURS: int = int(os.getenv("STATS_INTERVAL_HOURS", "4"))




