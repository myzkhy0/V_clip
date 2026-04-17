"""
test_site.py -- Local ranking viewer with period and group tabs.
"""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from config import EXCLUDED_CHANNELS_FILE, GROUP_KEYWORDS
from db import fetchall

logger = logging.getLogger(__name__)

HOST = os.getenv("TEST_SITE_HOST", "127.0.0.1")
PORT = int(os.getenv("TEST_SITE_PORT", "8000"))
FONT_FILE = r"C:\Users\11bs0\OneDrive\デスクトップ\NotoSansJP-VariableFont_wght.ttf"
LOGO_FILE = os.getenv("TEST_SITE_LOGO_FILE", "").strip()
FAVICON_FILE = os.getenv(
    "TEST_SITE_FAVICON_FILE",
    str(Path(__file__).resolve().parent / "assets" / "favicon.ico"),
).strip()
DEFAULT_OG_IMAGE_FILE = str(Path(__file__).resolve().parent / "assets" / "site-logo.jpg")
ADMIN_TOKEN = os.getenv("TEST_SITE_ADMIN_TOKEN", "")
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "").strip()
SITE_BASE_URL = os.getenv("TEST_SITE_BASE_URL", "").strip()
YOUTUBE_DAILY_SEARCH_UNIT_LIMIT = int(os.getenv("YOUTUBE_DAILY_SEARCH_UNIT_LIMIT", "8000"))
YOUTUBE_QUOTA_STATE_FILE = os.getenv("YOUTUBE_QUOTA_STATE_FILE", ".youtube_quota_state.json")
X_API_USER_BEARER_TOKEN = os.getenv("X_API_USER_BEARER_TOKEN", "").strip()
X_API_POST_URL = os.getenv("X_API_POST_URL", "https://api.x.com/2/tweets").strip() or "https://api.x.com/2/tweets"
X_API_TIMEOUT_SECONDS = float(os.getenv("X_API_TIMEOUT_SECONDS", "10"))
X_API_OAUTH1_CONSUMER_KEY = os.getenv("X_API_OAUTH1_CONSUMER_KEY", "").strip()
X_API_OAUTH1_CONSUMER_SECRET = os.getenv("X_API_OAUTH1_CONSUMER_SECRET", "").strip()
X_API_OAUTH1_ACCESS_TOKEN = os.getenv("X_API_OAUTH1_ACCESS_TOKEN", "").strip()
X_API_OAUTH1_ACCESS_TOKEN_SECRET = os.getenv("X_API_OAUTH1_ACCESS_TOKEN_SECRET", "").strip()
JST = timezone(timedelta(hours=9))

PERIODS: list[tuple[str, str, str, str]] = [
    ("daily", "24時間", "daily_ranking_shorts", "daily_ranking_video"),
    ("weekly", "7日", "weekly_ranking_shorts", "weekly_ranking_video"),
    ("monthly", "30日", "monthly_ranking_shorts", "monthly_ranking_video"),
]
GROUP_ORDER = [
    "all",
    "Aogiri",
    "DotLive",
    "774inc",
    "nijisanji",
    "Neo-Porte",
    "NoriPro",
    "VSPO",
    "hololive",
    "MilliPro",
    "UniReid",
    "REJECT",
    "RIOTMUSIC",
    "other",
]
GROUP_LABELS = {
    "all": "全体",
    "hololive": "ホロライブ",
    "nijisanji": "にじさんじ",
    "VSPO": "ぶいすぽっ！",
    "Neo-Porte": "ネオポルテ",
    "UniReid": "ゆにれいど",
    "774inc": "ななしいんく",
    "NoriPro": "のりプロ",
    "MilliPro": "ミリプロ",
    "Aogiri": "あおぎり高校",
    "DotLive": "どっとライブ",
    "REJECT": "REJECT",
    "RIOTMUSIC": "RIOT MUSIC",
    "other": "その他",
}

SITE_TITLE = "VCLIP | VTuber切り抜きランキング"
SITE_DESCRIPTION = (
    "VTuber切り抜きの再生数ランキング。24時間・7日・30日ごとの注目クリップを確認できます。"
)
SITE_OG_LOCALE = "ja_JP"
CHANNEL_ID_PATTERN = re.compile(r"(UC[0-9A-Za-z_-]{20,})")




def _favicon_content_type() -> str:
    suffix = Path(FAVICON_FILE or "").suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/x-icon"


def _extract_channel_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith("UC") and len(value) >= 20:
        return value
    match = CHANNEL_ID_PATTERN.search(value)
    return match.group(1) if match else ""


def _normalize_base_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def _build_head_meta(base_url: str, is_admin: bool) -> str:
    canonical_url = f"{base_url}/" if base_url else ""
    robots = "noindex, nofollow" if is_admin else "index, follow"
    og_image_url = ""
    if base_url:
        if LOGO_FILE and Path(LOGO_FILE).exists():
            og_image_url = f"{base_url}/assets/site-logo.png"
        elif Path(DEFAULT_OG_IMAGE_FILE).exists():
            og_image_url = f"{base_url}/assets/site-logo.jpg"
        elif FAVICON_FILE and Path(FAVICON_FILE).exists():
            og_image_url = f"{base_url}/assets/favicon.ico"

    tags = [
        f'<meta name="description" content="{html.escape(SITE_DESCRIPTION, quote=True)}">',
        f'<meta name="robots" content="{robots}">',
        '<link rel="icon" type="image/x-icon" href="/assets/favicon.ico">',
        '<link rel="shortcut icon" href="/assets/favicon.ico">',
    ]
    if canonical_url:
        tags.append(f'<link rel="canonical" href="{html.escape(canonical_url, quote=True)}">')

    tags.extend(
        [
            f'<meta property="og:type" content="website">',
            f'<meta property="og:site_name" content="{html.escape(SITE_TITLE, quote=True)}">',
            f'<meta property="og:title" content="{html.escape(SITE_TITLE, quote=True)}">',
            f'<meta property="og:description" content="{html.escape(SITE_DESCRIPTION, quote=True)}">',
            f'<meta property="og:locale" content="{SITE_OG_LOCALE}">',
        ]
    )
    if canonical_url:
        escaped_canonical = html.escape(canonical_url, quote=True)
        tags.append(f'<meta property="og:url" content="{escaped_canonical}">')

    if og_image_url:
        escaped_og_image = html.escape(og_image_url, quote=True)
        tags.append(f'<meta property="og:image" content="{escaped_og_image}">')
        tags.append(f'<meta property="og:image:alt" content="{html.escape(SITE_TITLE, quote=True)}">')

    website_structured_data = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_TITLE,
        "description": SITE_DESCRIPTION,
    }
    if canonical_url:
        website_structured_data["url"] = canonical_url

    tags.append(
        '<script type="application/ld+json">'
        + json.dumps(website_structured_data, ensure_ascii=False)
        + "</script>"
    )
    return "\n  ".join(tags)


def _build_sitemap_xml(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        normalized = f"http://{HOST}:{PORT}"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[str] = [
        f"<url><loc>{html.escape(normalized + '/')}</loc><lastmod>{now_iso}</lastmod></url>",
        f"<url><loc>{html.escape(normalized + '/index.html')}</loc><lastmod>{now_iso}</lastmod></url>",
    ]
    try:
        video_rows = fetchall(
            """
            SELECT video_id, published_at
            FROM videos
            WHERE video_id IS NOT NULL
            ORDER BY published_at DESC
            LIMIT 5000
            """
        )
        for row in video_rows:
            video_id = _normalize_video_id(str(row.get("video_id") or ""))
            if not video_id:
                continue
            lastmod_value = row.get("published_at")
            if isinstance(lastmod_value, datetime):
                if lastmod_value.tzinfo is None:
                    lastmod_value = lastmod_value.replace(tzinfo=timezone.utc)
                lastmod = lastmod_value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                lastmod = now_iso
            loc = f"{normalized}/video/{video_id}"
            items.append(f"<url><loc>{html.escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>")
    except Exception:
        logger.exception("Failed to build video URL entries for sitemap.xml")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{''.join(items)}"
        "</urlset>"
    )


def _build_robots_txt(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    lines = ["User-agent: *", "Allow: /"]
    if normalized:
        lines.append(f"Sitemap: {normalized}/sitemap.xml")
    return "\n".join(lines) + "\n"
def _fetch_latest_rankings(table: str, top_n: int = 100) -> tuple[datetime | None, list[dict]]:
    try:
        latest_row = fetchall(
            f"""
            SELECT calculated_at
            FROM {table}
            ORDER BY calculated_at DESC
            LIMIT 1
            """
        )
    except Exception:
        logger.exception("Failed to fetch latest ranking from table %s", table)
        return None, []

    if not latest_row:
        return None, []

    calculated_at = latest_row[0]["calculated_at"]

    previous_ids: set[str] = set()
    prev_calculated_at: datetime | None = None
    previous_row = fetchall(
        f"""
        SELECT calculated_at
        FROM {table}
        WHERE calculated_at < %s
        ORDER BY calculated_at DESC
        LIMIT 1
        """,
        (calculated_at,),
    )
    if previous_row:
        prev_calculated_at = previous_row[0]["calculated_at"]
        prev_rows = fetchall(
            f"""
            SELECT video_id
            FROM {table}
            WHERE calculated_at = %s
            """,
            (prev_calculated_at,),
        )
        previous_ids = {row["video_id"] for row in prev_rows}

    period_hours_map = {"daily": 24, "weekly": 168, "monthly": 720}
    period_key = "daily"
    if table.startswith("weekly_"):
        period_key = "weekly"
    elif table.startswith("monthly_"):
        period_key = "monthly"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=period_hours_map.get(period_key, 24))
    limit = max(1, int(top_n))
    excluded_channel_ids = _load_excluded_channel_ids()
    exclude_clause = ""
    ranking_params: list[object] = [cutoff, cutoff, cutoff, calculated_at]
    if excluded_channel_ids:
        exclude_clause = " AND NOT (v.channel_id = ANY(%s))"
        ranking_params.append(excluded_channel_ids)
    ranking_params.append(limit)
    rows = fetchall(
        f"""
        SELECT
            r.rank,
            r.view_growth,
            r.calculated_at,
            v.video_id,
            v.title,
            v.channel_id,
            v.channel_name,
            v.channel_icon_url,
            v.group_name,
            v.content_type,
            v.duration_seconds,
            v.tags_text,
            v.published_at,
            lv.view_count AS latest_view_count,
            ov.view_count AS old_view_count,
            GREATEST(
                0,
                COALESCE(ls.like_count, 0) - COALESCE(os.like_count, fs.like_count, ls.like_count, 0)
            ) AS like_growth,
            GREATEST(
                0,
                COALESCE(lc.comment_count, 0) - COALESCE(oc.comment_count, fc.comment_count, lc.comment_count, 0)
            ) AS comment_growth
        FROM {table} r
        JOIN videos v ON v.video_id = r.video_id
        LEFT JOIN LATERAL (
            SELECT view_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) lv ON TRUE
        LEFT JOIN LATERAL (
            SELECT view_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
              AND s.timestamp <= %s
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) ov ON TRUE
        LEFT JOIN LATERAL (
            SELECT like_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) ls ON TRUE
        LEFT JOIN LATERAL (
            SELECT like_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
              AND s.timestamp <= %s
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) os ON TRUE
        LEFT JOIN LATERAL (
            SELECT like_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
            ORDER BY s.timestamp ASC
            LIMIT 1
        ) fs ON TRUE
        LEFT JOIN LATERAL (
            SELECT comment_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) lc ON TRUE
        LEFT JOIN LATERAL (
            SELECT comment_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
              AND s.timestamp <= %s
            ORDER BY s.timestamp DESC
            LIMIT 1
        ) oc ON TRUE
        LEFT JOIN LATERAL (
            SELECT comment_count
            FROM video_stats s
            WHERE s.video_id = r.video_id
            ORDER BY s.timestamp ASC
            LIMIT 1
        ) fc ON TRUE
        WHERE r.calculated_at = %s
          {exclude_clause}
        ORDER BY r.rank
        LIMIT %s
        """,
        tuple(ranking_params),
    )

    prev_rank_map: dict[str, int] = {}
    if prev_calculated_at and rows:
        video_ids = [str(row.get("video_id") or "") for row in rows if row.get("video_id")]
        if video_ids:
            prev_rank_rows = fetchall(
                f"""
                SELECT video_id, rank
                FROM {table}
                WHERE calculated_at = %s
                  AND video_id = ANY(%s)
                """,
                (prev_calculated_at, video_ids),
            )
            prev_rank_map = {
                str(row.get("video_id") or ""): int(row.get("rank") or 0)
                for row in prev_rank_rows
                if row.get("video_id") and row.get("rank")
            }

    now_utc = datetime.now(timezone.utc)
    for row in rows:
        video_id = str(row.get("video_id") or "")
        row["prev_rank"] = prev_rank_map.get(video_id)
        try:
            latest_view_count = int(row.get("latest_view_count") or 0)
        except (TypeError, ValueError):
            latest_view_count = 0
        try:
            old_view_count = int(row.get("old_view_count") or 0)
        except (TypeError, ValueError):
            old_view_count = 0
        if latest_view_count > 0 and old_view_count > 0 and latest_view_count >= old_view_count:
            row["view_growth_pct"] = int(round((latest_view_count - old_view_count) * 100 / old_view_count))
        else:
            row["view_growth_pct"] = None
        published_at = row.get("published_at")
        if published_at is None:
            row["is_new"] = False
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        within_24h = (now_utc - published_at) <= timedelta(hours=24)
        row["is_new"] = row["video_id"] not in previous_ids and within_24h

    return calculated_at, rows


def _load_excluded_channel_ids() -> list[str]:
    path = Path(EXCLUDED_CHANNELS_FILE)
    if not path.exists():
        return []

    channel_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        channel_id = _extract_channel_id(value)
        if channel_id:
            channel_ids.append(channel_id)
    return list(dict.fromkeys(channel_ids))


def _fetch_daily_provisional_rows(content_type: str, top_n: int = 100) -> list[dict]:
    # Provisional daily lane:
    # - videos without a snapshot older than 24h
    # - growth = latest - first snapshot (same-day provisional)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    excluded_channel_ids = _load_excluded_channel_ids()
    exclude_clause = ""
    params: list[object] = [cutoff, "%切り抜き%", "%切り抜き%", content_type]
    if excluded_channel_ids:
        exclude_clause = " AND NOT (v.channel_id = ANY(%s))"
        params.append(excluded_channel_ids)

    limit = max(1, int(top_n))
    rows = fetchall(
        f"""
        WITH latest_stats AS (
            SELECT DISTINCT ON (video_id)
                video_id,
                view_count,
                like_count,
                comment_count,
                timestamp AS latest_ts
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
                view_count,
                like_count,
                comment_count,
                timestamp AS first_ts
            FROM video_stats
            ORDER BY video_id, timestamp ASC
        ),
        provisional AS (
            SELECT
                v.video_id,
                v.title,
                v.channel_id,
                v.channel_name,
                v.channel_icon_url,
                v.group_name,
                v.content_type,
                v.duration_seconds,
                v.tags_text,
                v.published_at,
                (l.view_count - f.view_count) AS view_growth,
                CASE
                    WHEN f.view_count > 0 THEN ROUND(((l.view_count - f.view_count)::numeric * 100) / f.view_count)
                    ELSE NULL
                END AS view_growth_pct,
                GREATEST(0, l.like_count - f.like_count) AS like_growth,
                GREATEST(0, l.comment_count - f.comment_count) AS comment_growth
            FROM latest_stats l
            JOIN first_stats f ON f.video_id = l.video_id
            LEFT JOIN old_stats o ON o.video_id = l.video_id
            JOIN videos v ON v.video_id = l.video_id
            WHERE o.video_id IS NULL
              AND COALESCE(NULLIF(v.group_name, ''), 'other') <> 'other'
              AND (v.title LIKE %s OR v.tags_text LIKE %s)
              AND v.content_type = %s
              {exclude_clause}
        ),
        ranked AS (
            SELECT
                ROW_NUMBER() OVER (ORDER BY view_growth DESC) AS rank,
                view_growth,
                video_id,
                title,
                channel_id,
                channel_name,
                channel_icon_url,
                group_name,
                content_type,
                duration_seconds,
                tags_text,
                published_at,
                like_growth
            FROM provisional
            WHERE view_growth > 0
        )
        SELECT *
        FROM ranked
        ORDER BY rank
        LIMIT %s
        """,
        (*tuple(params), limit),
    )

    now_utc = datetime.now(timezone.utc)
    for row in rows:
        row["prev_rank"] = None
        published_at = row.get("published_at")
        if published_at is None:
            row["is_new"] = False
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        row["is_new"] = (now_utc - published_at) <= timedelta(hours=24)

    return rows
def _fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    jst = timezone(timedelta(hours=9))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(jst).strftime("%Y-%m-%d %H:%M")


def _infer_group(row: dict) -> str:
    current = _sanitize_text(row.get("group_name")).strip()
    if current:
        for group_name in GROUP_ORDER:
            if group_name != "all" and current.lower() == group_name.lower():
                return group_name
        return current

    haystack = " ".join(
        [
            _sanitize_text(row.get("title", "")),
            _sanitize_text(row.get("tags_text", "")),
            _sanitize_text(row.get("channel_name", "")),
        ]
    ).lower()
    for group_name, keywords in GROUP_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            return group_name
    return "other"



def _thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _truncate_text(value: str, max_len: int = 42) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _sanitize_text(value: object) -> str:
    """Drop invalid surrogate code points from external text before HTML rendering."""
    if value is None:
        return ""
    text = str(value)
    return "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))


def _share_prefix_for_period(period_key: str, month_day: str, rank: int, content_label: str) -> str:
    normalized_label = "shorts" if content_label == "shorts" else "動画"
    if period_key == "daily":
        return f"本日({month_day})のVTuber切り抜きランキング {rank}位の{normalized_label}です！"
    if period_key == "weekly":
        return f"直近7日間のVTuber切り抜きランキング {rank}位の{normalized_label}です！"
    if period_key == "monthly":
        return f"直近30日間のVTuber切り抜きランキング {rank}位の{normalized_label}です！"
    return f"VTuber切り抜きランキング {rank}位の{normalized_label}です！"


def _rank_label_for_detail(rank_value: int | None, top_n: int = 100) -> str:
    if rank_value is None:
        return "-"
    try:
        value = int(rank_value)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    if value > top_n:
        return "Not ranked"
    return f"#{value}"


def _normalize_period_key(period_key: str | None) -> str:
    normalized = (period_key or "").strip().lower()
    return normalized if normalized in {"daily", "weekly", "monthly"} else "daily"


def _format_duration_label(duration_seconds: object) -> str:
    try:
        total = int(duration_seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _merge_daily_rows(
    strict_rows: list[dict],
    provisional_rows: list[dict],
    top_n: int = 100,
) -> list[dict]:
    merged: dict[str, dict] = {}

    def _row_growth(row: dict) -> int:
        try:
            return int(row.get("view_growth") or 0)
        except (TypeError, ValueError):
            return 0

    for source in (strict_rows, provisional_rows):
        for row in source:
            video_id = str(row.get("video_id") or "").strip()
            if not video_id:
                continue
            current = merged.get(video_id)
            if current is None or _row_growth(row) > _row_growth(current):
                next_row = dict(row)
                if current and current.get("is_new"):
                    next_row["is_new"] = bool(next_row.get("is_new")) or True
                merged[video_id] = next_row
            elif row.get("is_new"):
                current["is_new"] = True

    limit = max(1, int(top_n))
    sorted_rows = sorted(
        merged.values(),
        key=lambda r: (_row_growth(r), r.get("video_id") or ""),
        reverse=True,
    )[:limit]
    for idx, row in enumerate(sorted_rows, start=1):
        row["rank"] = idx
    return sorted_rows

def _render_cards(
    rows: list[dict],
    card_class: str = "",
    show_group: bool = True,
    period_key: str = "daily",
    content_label: str = "shorts",
) -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    cards = []
    today = datetime.now(JST)
    month_day = f"{today.month}/{today.day}"
    for row in rows:
        video_id = html.escape(row["video_id"])
        title_raw = _sanitize_text(row.get("title", ""))
        title = html.escape(title_raw)
        channel_id = html.escape(row["channel_id"])
        channel_name = html.escape(_sanitize_text(row.get("channel_name", "")))
        channel_icon_url = html.escape(_sanitize_text(row.get("channel_icon_url") or ""))
        group_name = html.escape(_infer_group(row))
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        detail_url = f"/video/{video_id}?period={period_key}"
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        title_plain = " ".join(title_raw.split())
        share_title = title_plain
        share_prefix = _share_prefix_for_period(period_key, month_day, row["rank"], content_label)
        share_detail_url = f"https://vclipranking.com/video/{video_id}"
        share_text = (
            f"{share_prefix}\n\n"
            f"{share_title}\n"
            f"{share_detail_url}\n"
            "#VCLIP"
        )
        share_url = "https://twitter.com/intent/tweet?text=" + quote(share_text, safe="")
        content_type = html.escape((row.get("content_type") or "").lower())
        published_label = ""
        published_iso = ""
        published_at = row.get("published_at")
        if isinstance(published_at, datetime):
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            published_label = published_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
            published_iso = published_at.astimezone(JST).isoformat()
        try:
            like_growth = int(row.get("like_growth") or 0)
        except (TypeError, ValueError):
            like_growth = 0
        try:
            comment_growth = int(row.get("comment_growth") or 0)
        except (TypeError, ValueError):
            comment_growth = 0
        try:
            view_growth_value = int(row.get("view_growth") or 0)
        except (TypeError, ValueError):
            view_growth_value = 0

        # Rank-specific glow classes for top 3
        rank = row["rank"]
        rank_class = ""
        if rank <= 3:
            rank_class = f" card-rank-{rank}"

        rank_badge_class = f"rank-badge rank-{rank}" if rank <= 3 else "rank-badge"
        new_badge_html = '<span class="new-badge thumb-new-badge">NEW</span>' if row.get("is_new") else ""
        duration_label = _format_duration_label(row.get("duration_seconds"))
        duration_html = f'<span class="duration-badge">{html.escape(duration_label)}</span>' if duration_label else ""
        group_pill_html = f'<span class="pill">{group_name}</span>' if show_group else ""
        try:
            current_rank = int(row.get("rank") or 0)
        except (TypeError, ValueError):
            current_rank = 0
        try:
            prev_rank = int(row.get("prev_rank") or 0)
        except (TypeError, ValueError):
            prev_rank = 0
        try:
            view_growth_pct = int(row.get("view_growth_pct") or 0)
        except (TypeError, ValueError):
            view_growth_pct = 0

        # Channel icon HTML
        icon_html = ""
        if channel_icon_url:
            icon_html = f"""
                <img class="channel-icon" src="{channel_icon_url}" alt="" loading="lazy"
                     referrerpolicy="no-referrer"
                     onerror="this.style.display='none'; this.nextElementSibling.style.display='inline-flex';">
                <span class="channel-icon-fallback" style="display:none;">ch</span>
            """
        else:
            icon_html = '<span class="channel-avatar"></span>'

        cards.append(
            f"""
            <article class="card video-card{rank_class}" data-video-id="{video_id}" data-rank="{current_rank}" data-prev-rank="{prev_rank}" data-view-growth-pct="{view_growth_pct}" data-view-growth="{view_growth_value}" data-like-growth="{like_growth}" data-comment-growth="{comment_growth}" data-published-at="{html.escape(published_iso, quote=True)}">
              <a class="thumb" href="{detail_url}"
                  data-video-id="{video_id}" data-video-title="{title}" data-content-type="{content_type}">
                <img src="{_thumbnail_url(video_id)}" alt="{title}" loading="lazy">
                <div class="{rank_badge_class}">{rank}</div>
                {new_badge_html}
                {duration_html}
              </a>
              <div class="card-meta">
                <a class="card-title" href="{detail_url}"
                   data-video-id="{video_id}" data-content-type="{content_type}">{title}</a>
                <div class="card-info card-info-top">
                  <a class="card-channel channel-link" href="{channel_url}" target="_blank" rel="noreferrer">
                    {icon_html}
                    <span class="channel-name">{channel_name}</span>
                  </a>
                  {group_pill_html}
                </div>
                <div class="card-info card-info-bottom">
                    <span class="card-metrics-stack">
                    <span class="card-views"><em class="arrow">▶</em><span class="view-growth">+{view_growth_value:,}</span></span>
                    <span class="card-likes"><span class="like-icon">❤</span><span class="like-count">+{like_growth:,}</span></span>
                  </span>
                  <span class="card-date">{html.escape(published_label)}</span>
                </div>
                <div class="card-actions">
                  <a class="card-action-link" href="{video_url}" target="_blank" rel="noreferrer" aria-label="YouTubeで開く" title="YouTubeで開く">
                    <svg class="card-action-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="1.5" y="5" width="21" height="14" rx="4.5" fill="#ff0033"></rect><polygon points="10,9 16,12 10,15" fill="#fff"></polygon></svg>
                    <span>YouTube</span>
                  </a>
                  <a class="card-action-link" href="{share_url}" target="_blank" rel="noreferrer" aria-label="Xでシェア" title="Xでシェア">
                    <svg class="card-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.9 2H22l-6.8 7.8L23 22h-6.1l-4.8-6.3L6.5 22H3.4l7.2-8.2L1 2h6.2l4.3 5.8L18.9 2zm-1.1 18h1.7L6.3 3.9H4.5z"></path></svg>
                    <span>シェア</span>
                  </a>
                  <a class="card-action-link card-detail-link" href="{detail_url}">詳細 →</a>
                </div>
              </div>
            </article>
            """
        )
    return "".join(cards)
def _render_rank_sections(
    rows: list[dict],
    show_group: bool = True,
    period_key: str = "daily",
    content_label: str = "shorts",
) -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    all_html = _render_cards(rows, show_group=show_group, period_key=period_key, content_label=content_label)
    return f"""
    <div class="cards">{all_html}</div>
    """


def _render_group_content(
    shorts_rows: list[dict],
    video_rows: list[dict],
    show_group: bool = True,
    period_key: str = "daily",
    provisional_shorts_rows: list[dict] | None = None,
    provisional_video_rows: list[dict] | None = None,
    top_n: int = 100,
) -> str:
    provisional_shorts_rows = provisional_shorts_rows or []
    provisional_video_rows = provisional_video_rows or []

    if period_key == "daily":
        display_shorts_rows = _merge_daily_rows(
            shorts_rows, provisional_shorts_rows, top_n=top_n
        )
        display_video_rows = _merge_daily_rows(
            video_rows, provisional_video_rows, top_n=top_n
        )
    else:
        display_shorts_rows = shorts_rows
        display_video_rows = video_rows

    if not display_shorts_rows and not display_video_rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    shorts_html = (
        _render_rank_sections(
            display_shorts_rows,
            show_group=show_group,
            period_key=period_key,
            content_label="shorts",
        )
        if display_shorts_rows
        else '<div class="empty">Shortsに該当する動画はありません。</div>'
    )
    video_html = (
        _render_rank_sections(
            display_video_rows,
            show_group=show_group,
            period_key=period_key,
            content_label="動画",
        )
        if display_video_rows
        else '<div class="empty">動画に該当する動画はありません。</div>'
    )

    return f"""
    <div class="content-panel" data-content-panel="shorts">{shorts_html}</div>
    <div class="content-panel" data-content-panel="video">{video_html}</div>
    """
def _build_period_payload(is_admin: bool = False) -> list[dict]:
    payload = []
    top_n = 200 if is_admin else 100
    for period_key, label, shorts_table, video_table in PERIODS:
        shorts_calculated_at, shorts_rows = _fetch_latest_rankings(shorts_table, top_n=top_n)
        video_calculated_at, video_rows = _fetch_latest_rankings(video_table, top_n=top_n)

        provisional_shorts_rows: list[dict] = []
        provisional_video_rows: list[dict] = []
        if period_key == "daily":
            provisional_shorts_rows = _fetch_daily_provisional_rows("shorts", top_n=top_n)
            provisional_video_rows = _fetch_daily_provisional_rows("video", top_n=top_n)

        grouped_shorts: dict[str, list[dict]] = defaultdict(list)
        grouped_video: dict[str, list[dict]] = defaultdict(list)
        grouped_provisional_shorts: dict[str, list[dict]] = defaultdict(list)
        grouped_provisional_video: dict[str, list[dict]] = defaultdict(list)

        grouped_shorts["all"] = shorts_rows
        grouped_video["all"] = video_rows
        grouped_provisional_shorts["all"] = provisional_shorts_rows
        grouped_provisional_video["all"] = provisional_video_rows

        for row in shorts_rows:
            grouped_shorts[_infer_group(row)].append(row)
        for row in video_rows:
            grouped_video[_infer_group(row)].append(row)
        for row in provisional_shorts_rows:
            grouped_provisional_shorts[_infer_group(row)].append(row)
        for row in provisional_video_rows:
            grouped_provisional_video[_infer_group(row)].append(row)

        available_groups = [
            group_name
            for group_name in GROUP_ORDER
            if grouped_shorts.get(group_name)
            or grouped_video.get(group_name)
            or grouped_provisional_shorts.get(group_name)
            or grouped_provisional_video.get(group_name)
        ]
        if not available_groups:
            available_groups = ["all"]
        if not is_admin:
            available_groups = ["all"]

        candidates = [dt for dt in (shorts_calculated_at, video_calculated_at) if dt is not None]
        calculated_at = max(candidates) if candidates else None

        payload.append(
            {
                "table": period_key,
                "label": label,
                "calculated_at": _fmt_datetime(calculated_at),
                "groups": {
                    group_name: _render_group_content(
                        grouped_shorts.get(group_name, []),
                        grouped_video.get(group_name, []),
                        show_group=True,
                        period_key=period_key,
                        provisional_shorts_rows=grouped_provisional_shorts.get(group_name, []),
                        provisional_video_rows=grouped_provisional_video.get(group_name, []),
                        top_n=top_n,
                    )
                    for group_name in available_groups
                },
                "available_groups": available_groups,
            }
        )
    return payload
def _load_quota_usage() -> tuple[int, int]:
    """Return (used_units, limit_units) from local quota state file."""
    limit = max(0, YOUTUBE_DAILY_SEARCH_UNIT_LIMIT)
    if limit == 0:
        return 0, 0

    path = Path(YOUTUBE_QUOTA_STATE_FILE)
    if not path.exists():
        return 0, limit

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, limit

    used = int(payload.get("search_units_used", 0))
    return max(0, used), limit


def _quota_status(used: int, limit: int) -> tuple[str, str]:
    if limit <= 0:
        return "無効", "muted"
    ratio = used / limit
    if ratio >= 1.0:
        return "上限到達", "danger"
    if ratio >= 0.8:
        return "注意", "warn"
    return "通常", "ok"


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_oauth1_header_for_x_post(url: str) -> str:
    oauth_params = {
        "oauth_consumer_key": X_API_OAUTH1_CONSUMER_KEY,
        "oauth_token": X_API_OAUTH1_ACCESS_TOKEN,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_timestamp": str(int(time.time())),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }

    def _pct(value: str) -> str:
        return quote(str(value), safe="~")

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    signing_items = sorted((k, v) for k, v in oauth_params.items())
    parameter_string = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in signing_items)
    signature_base_string = "&".join(["POST", _pct(base_url), _pct(parameter_string)])
    signing_key = f"{_pct(X_API_OAUTH1_CONSUMER_SECRET)}&{_pct(X_API_OAUTH1_ACCESS_TOKEN_SECRET)}"
    digest = hmac.new(signing_key.encode("utf-8"), signature_base_string.encode("utf-8"), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode("ascii")
    oauth_params["oauth_signature"] = signature

    header_params = ", ".join(
        f'{_pct(k)}="{_pct(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_params}"


def _post_text_to_x_api(text: str) -> tuple[bool, int, dict]:
    normalized_text = (text or "").strip()
    if not normalized_text:
        return False, 400, {"error": "text is required"}
    if len(normalized_text) > 280:
        return False, 400, {"error": "text is too long (max 280 chars)"}
    use_oauth1 = all(
        [
            X_API_OAUTH1_CONSUMER_KEY,
            X_API_OAUTH1_CONSUMER_SECRET,
            X_API_OAUTH1_ACCESS_TOKEN,
            X_API_OAUTH1_ACCESS_TOKEN_SECRET,
        ]
    )
    if not X_API_USER_BEARER_TOKEN and not use_oauth1:
        return False, 503, {
            "error": (
                "X API token is not configured "
                "(set X_API_USER_BEARER_TOKEN or OAuth1 keys)"
            )
        }

    payload = json.dumps({"text": normalized_text}, ensure_ascii=False).encode("utf-8")
    if use_oauth1:
        auth_header = _build_oauth1_header_for_x_post(X_API_POST_URL)
    else:
        auth_header = f"Bearer {X_API_USER_BEARER_TOKEN}"
    req = Request(
        X_API_POST_URL,
        data=payload,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=max(1.0, X_API_TIMEOUT_SECONDS)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            tweet_id = ((data.get("data") or {}).get("id") or "").strip()
            if tweet_id:
                return True, 200, {"tweet_id": tweet_id, "raw": data}
            return True, 200, {"raw": data}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail: dict
        try:
            detail = json.loads(raw) if raw else {}
        except ValueError:
            detail = {"raw": raw}
        return False, int(exc.code or 502), {"error": "x_api_http_error", "detail": detail}
    except URLError as exc:
        return False, 502, {"error": "x_api_connection_error", "detail": str(exc.reason)}
    except Exception as exc:
        logger.exception("Failed to post to X API")
        return False, 500, {"error": "x_api_post_failed", "detail": str(exc)}


def _fetch_admin_board_data() -> dict:
    """Fetch compact operational metrics for admin board."""
    data = {
        "channels_total": 0,
        "channels_tracked": 0,
        "videos_total": 0,
        "stats_total": 0,
        "videos_today": 0,
        "daily_shorts_rows": 0,
        "daily_video_rows": 0,
        "excluded_count": 0,
        "ranking_last_updated": None,
    }
    try:
        rows = fetchall(
            """
            SELECT
                COUNT(*) AS channels_total,
                COUNT(*) FILTER (WHERE is_tracked = TRUE) AS channels_tracked
            FROM channels
            """
        )
        if rows:
            data["channels_total"] = int(rows[0].get("channels_total") or 0)
            data["channels_tracked"] = int(rows[0].get("channels_tracked") or 0)
    except Exception:
        logger.exception("Failed to fetch channels metrics for admin board")

    try:
        rows = fetchall("SELECT COUNT(*) AS c FROM videos")
        if rows:
            data["videos_total"] = int(rows[0].get("c") or 0)
    except Exception:
        logger.exception("Failed to fetch videos count for admin board")

    try:
        rows = fetchall("SELECT COUNT(*) AS c FROM video_stats")
        if rows:
            data["stats_total"] = int(rows[0].get("c") or 0)
    except Exception:
        logger.exception("Failed to fetch stats count for admin board")

    try:
        rows = fetchall(
            """
            SELECT COUNT(*) AS c
            FROM videos
            WHERE (published_at + interval '9 hours')::date = ((NOW() AT TIME ZONE 'Asia/Tokyo')::date)
            """
        )
        if rows:
            data["videos_today"] = int(rows[0].get("c") or 0)
    except Exception:
        logger.exception("Failed to fetch today's videos count for admin board")

    try:
        rows = fetchall(
            """
            SELECT
                (SELECT COUNT(*) FROM daily_ranking_shorts WHERE calculated_at = (SELECT MAX(calculated_at) FROM daily_ranking_shorts)) AS shorts_rows,
                (SELECT COUNT(*) FROM daily_ranking_video  WHERE calculated_at = (SELECT MAX(calculated_at) FROM daily_ranking_video))  AS video_rows
            """
        )
        if rows:
            data["daily_shorts_rows"] = int(rows[0].get("shorts_rows") or 0)
            data["daily_video_rows"] = int(rows[0].get("video_rows") or 0)
    except Exception:
        logger.exception("Failed to fetch daily ranking rows for admin board")

    try:
        rows = fetchall(
            """
            SELECT MAX(ts) AS ranking_last_updated
            FROM (
                SELECT MAX(calculated_at) AS ts FROM daily_ranking_shorts
                UNION ALL
                SELECT MAX(calculated_at) AS ts FROM daily_ranking_video
                UNION ALL
                SELECT MAX(calculated_at) AS ts FROM weekly_ranking_shorts
                UNION ALL
                SELECT MAX(calculated_at) AS ts FROM weekly_ranking_video
                UNION ALL
                SELECT MAX(calculated_at) AS ts FROM monthly_ranking_shorts
                UNION ALL
                SELECT MAX(calculated_at) AS ts FROM monthly_ranking_video
            ) r
            """
        )
        if rows:
            data["ranking_last_updated"] = rows[0].get("ranking_last_updated")
    except Exception:
        logger.exception("Failed to fetch ranking_last_updated for admin board")

    try:
        data["excluded_count"] = len(_load_excluded_channel_ids())
    except Exception:
        logger.exception("Failed to count excluded channels for admin board")

    return data
def _fetch_public_hero_stats() -> dict:
    """Public hero metrics for top summary cards."""
    stats = {
        "tracking_videos": 0,
        "daily_growth_total": 0,
        "new_24h": 0,
    }

    try:
        rows = fetchall("SELECT COUNT(*) AS c FROM videos")
        if rows:
            stats["tracking_videos"] = int(rows[0].get("c") or 0)
    except Exception:
        logger.exception("Failed to fetch tracking_videos")

    try:
        rows = fetchall(
            """
            SELECT
              COALESCE((
                (SELECT COALESCE(SUM(view_growth), 0) FROM daily_ranking_shorts WHERE calculated_at = (SELECT MAX(calculated_at) FROM daily_ranking_shorts))
                +
                (SELECT COALESCE(SUM(view_growth), 0) FROM daily_ranking_video  WHERE calculated_at = (SELECT MAX(calculated_at) FROM daily_ranking_video))
              ), 0) AS total_growth
            """
        )
        if rows:
            stats["daily_growth_total"] = int(rows[0].get("total_growth") or 0)
    except Exception:
        logger.exception("Failed to fetch daily_growth_total")

    try:
        rows = fetchall(
            """
            SELECT COUNT(*) AS c
            FROM videos
            WHERE (published_at + interval '9 hours')::date = ((NOW() AT TIME ZONE 'Asia/Tokyo')::date)
            """
        )
        if rows:
            stats["new_24h"] = int(rows[0].get("c") or 0)
    except Exception:
        logger.exception("Failed to fetch new_24h")

    return stats


def render_error_page(error: Exception, base_url: str = "") -> str:
    message = html.escape(str(error) or error.__class__.__name__)
    database_url = os.getenv("DATABASE_URL", "(not set)")
    normalized_base_url = _normalize_base_url(base_url)
    head_meta = _build_head_meta(normalized_base_url, is_admin=False)

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SITE_TITLE}</title>
  {head_meta}
  <style>
    @font-face {{
      font-family: "Noto Sans JP Local";
      src: url("/assets/noto-sans-jp.ttf") format("truetype");
      font-weight: 100 900;
      font-display: swap;
    }}
    body {{
      margin: 0;
      font-family: "Noto Sans JP Local", sans-serif;
      color: #2c1d16;
      background: linear-gradient(135deg, #fbf5ef 0%, #f0e2d6 100%);
    }}
    .shell {{
      width: min(900px, calc(100% - 24px));
      margin: 24px auto 40px;
    }}
    .panel {{
      border: 1px solid rgba(44, 29, 22, 0.14);
      background: rgba(255, 250, 245, 0.94);
      box-shadow: 0 18px 40px rgba(72, 34, 22, 0.12);
      padding: 24px;
    }}
    .error {{
      padding: 14px 16px;
      background: rgba(184, 71, 46, 0.08);
      border: 1px solid rgba(184, 71, 46, 0.18);
      color: #b8472e;
      overflow-wrap: anywhere;
    }}
    code {{ font-family: Consolas, monospace; }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="panel">
      <h1>ランキングデータを読み込めませんでした</h1>
      <p>ページは起動していますが、データベースへの問い合わせに失敗しました。</p>
      <div class="error"><strong>Error:</strong> {message}</div>
      <p><code>DATABASE_URL</code>: <code>{html.escape(database_url)}</code></p>
    </section>
  </main>
</body>
</html>
"""




def render_policy_page(base_url: str = "") -> str:
    normalized_base_url = _normalize_base_url(base_url)
    head_meta = _build_head_meta(normalized_base_url, is_admin=False)
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>プライバシーポリシー | {SITE_TITLE}</title>
  {head_meta}
  <style>
    @font-face {{
      font-family: "Noto Sans JP Local";
      src: url("/assets/noto-sans-jp.ttf") format("truetype");
      font-weight: 100 900;
      font-display: swap;
    }}
    body {{
      margin: 0;
      font-family: "Noto Sans JP Local", sans-serif;
      color: #eaf2f8;
      background: linear-gradient(160deg, #081017 0%, #121b24 52%, #0b1218 100%);
    }}
    .shell {{
      width: min(900px, calc(100% - 24px));
      margin: 24px auto 40px;
    }}
    .panel {{
      border: 1px solid rgba(126, 160, 190, 0.22);
      background: rgba(16, 24, 33, 0.96);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
      padding: 24px;
      line-height: 1.75;
    }}
    h1 {{ margin-top: 0; }}
    h2 {{ margin-top: 1.5em; font-size: 1.05rem; }}
    a {{ color: #63d0ff; }}
    .back {{
      margin-top: 20px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="panel">
      <h1>プライバシーポリシー</h1>
      <p>当サイト（ぶいくりっぷ VTuber切り抜きランキング）では、サイト改善および利用状況の把握のため、Google Analyticsを利用しています。</p>

      <h2>1. 取得する情報</h2>
      <p>Google AnalyticsはCookieを利用し、閲覧ページ、アクセス元、利用環境等の情報を匿名で収集します。個人を直接特定する情報は含みません。</p>

      <h2>2. 利用目的</h2>
      <p>収集した情報は、サイトの品質向上、表示・導線改善、障害分析のために利用します。</p>

      <h2>3. 外部サービスについて</h2>
      <p>Google Analyticsにより収集される情報は、Google社の定めるプライバシーポリシーに基づいて管理されます。</p>
      <p><a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Google プライバシーポリシー</a></p>

      <h2>4. 無効化（オプトアウト）</h2>
      <p>Google Analyticsによる収集を望まない場合は、ブラウザ設定でCookieを無効化するか、Google提供のオプトアウトアドオンをご利用ください。</p>
      <p><a href="https://tools.google.com/dlpage/gaoptout?hl=ja" target="_blank" rel="noopener noreferrer">Google Analytics オプトアウト アドオン</a></p>

      <h2>5. 本ポリシーの変更</h2>
      <p>本ポリシーの内容は、必要に応じて予告なく変更することがあります。</p>

      <div class="back"><a href="/">ランキングページへ戻る</a></div>
    </section>
  </main>
</body>
</html>
"""

def render_homepage(is_admin: bool = False, base_url: str = "") -> str:
    payload = _build_period_payload(is_admin=is_admin)
    first_period = payload[0]["table"] if payload else ""
    group_labels_json = json.dumps(GROUP_LABELS, ensure_ascii=False).replace("</", "<\\/")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    hero_stats_json = json.dumps(_fetch_public_hero_stats(), ensure_ascii=False).replace("</", "<\\/")
    normalized_base_url = _normalize_base_url(base_url)
    head_meta = _build_head_meta(normalized_base_url, is_admin=is_admin)
    show_admin_meta = "true" if is_admin else "false"
    admin_html = ""
    admin_board_html = ""
    body_class = "admin-mode" if is_admin else ""
    logo_html = ""
    analytics_html = ""
    if Path(DEFAULT_OG_IMAGE_FILE).exists() or (LOGO_FILE and Path(LOGO_FILE).exists()):
        logo_html = """
          <div class="hero-logo-wrap">
            <img class="hero-logo" src="/assets/site-logo.jpg" alt="ぶいくりっぷ ロゴ" loading="eager">
          </div>
        """
    if GA_MEASUREMENT_ID:
        ga_id = html.escape(GA_MEASUREMENT_ID, quote=True)
        analytics_html = f"""
  <script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{ga_id}');
  </script>
"""
    if is_admin:
        used, limit = _load_quota_usage()
        status_label, status_class = _quota_status(used, limit)
        board = _fetch_admin_board_data()
        admin_html = f"""
          <div class="admin-quota">
            <span class="admin-pill {status_class}">API状態: {status_label}</span>
            <span class="admin-metrics">search.list {used:,} / {limit:,}</span>
          </div>
          <div class="admin-target-toggle" id="admin-target-toggle">
            <button class="admin-target-btn active" type="button" data-target-type="shorts">Shorts投稿</button>
            <button class="admin-target-btn" type="button" data-target-type="video">動画投稿</button>
          </div>
          <div id="admin-trending-picker" class="admin-trending-picker"></div>
        """
        admin_board_html = f"""
        <section class="admin-board">
          <div class="admin-board-head">
            <h2>管理ボード</h2>
            <p>運用メトリクス（リアルタイム）</p>
          </div>
          <div class="admin-metric-grid">
            <article class="admin-metric-card"><span>チャンネル（追跡/全体）</span><strong>{board['channels_tracked']:,} / {board['channels_total']:,}</strong></article>
            <article class="admin-metric-card"><span>動画総数</span><strong>{board['videos_total']:,}</strong></article>
            <article class="admin-metric-card"><span>本日公開動画（JST）</span><strong>{board['videos_today']:,}</strong></article>
            <article class="admin-metric-card"><span>統計スナップショット総数</span><strong>{board['stats_total']:,}</strong></article>
            <article class="admin-metric-card"><span>最新 daily / shorts 行数</span><strong>{board['daily_shorts_rows']:,}</strong></article>
            <article class="admin-metric-card"><span>最新 daily / video 行数</span><strong>{board['daily_video_rows']:,}</strong></article>
            <article class="admin-metric-card"><span>除外チャンネル数</span><strong>{board['excluded_count']:,}</strong></article>
            <article class="admin-metric-card"><span>最終ランキング更新（JST）</span><strong>{_fmt_datetime(board.get('ranking_last_updated'))}</strong></article>
          </div>
        </section>
        """

    return f"""<!doctype html>
<html lang="ja" id="top">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SITE_TITLE}</title>
  {head_meta}
  {analytics_html}
  <style>
    @font-face {{
      font-family: "Noto Sans JP Local";
      src: url("/assets/noto-sans-jp.ttf") format("truetype");
      font-weight: 100 900;
      font-display: swap;
    }}
    :root {{
      --bg-base: #0b0f1a;
      --bg-panel: rgba(15,20,35,0.72);
      --glass-border: rgba(100,160,240,0.10);
      --text: #e8edf4;
      --text-dim: rgba(232,237,244,0.52);
      --accent-gradient: linear-gradient(135deg,#a78bfa,#f472b6);
      --rank-gold:   rgba(255,215,0,0.7);
      --rank-silver: rgba(192,192,192,0.6);
      --rank-bronze: rgba(205,127,50,0.6);
    }}
    *,*::before,*::after {{ box-sizing:border-box; }}
    html {{ overflow-x: hidden; }}
    body {{
      margin:0;
      font-family:"Noto Sans JP Local","Hiragino Kaku Gothic ProN",sans-serif;
      color:var(--text);
      background:var(--bg-base);
      min-height:100vh;
      overflow-x:hidden;
    }}
    .bg-canvas {{
      position:fixed;inset:0;z-index:0;pointer-events:none;
      background:
        radial-gradient(ellipse 900px 500px at 15% 10%, rgba(167, 139, 250, 0.15), transparent 60%),
        radial-gradient(ellipse 700px 500px at 85% 5%, rgba(244, 114, 182, 0.12), transparent 55%),
        radial-gradient(ellipse 600px 400px at 50% 80%, rgba(34, 211, 238, 0.06), transparent 50%);
    }}
    .bg-canvas::after {{
      content:"";
      position:absolute;inset:0;
      background:
        radial-gradient(ellipse 500px 300px at 70% 40%, rgba(251, 146, 60, 0.07), transparent 50%),
        radial-gradient(ellipse 400px 300px at 20% 60%, rgba(167, 139, 250, 0.06), transparent 50%);
      animation:bgShift 18s ease-in-out infinite alternate;
    }}
    @keyframes bgShift {{
      0% {{ transform:translate(0,0) scale(1); opacity:0.6; }}
      100% {{ transform:translate(40px,-30px) scale(1.05); opacity:1; }}
    }}
    .shell {{
      position:relative;
      z-index:1;
      width:min(1260px,calc(100% - 32px));
      margin:0 auto;
      padding:20px 0 60px;
    }}
    /* ── Topbar ── */
    .topbar {{
      display:flex;align-items:center;justify-content:space-between;
      padding:14px 24px;
      background:var(--bg-panel);border:1px solid var(--glass-border);
      border-radius:16px;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
    }}
    .topbar-brand {{
      display:flex;align-items:center;gap:10px;
      font-weight:900;font-size:clamp(1.15rem,2vw,1.55rem);letter-spacing:-0.01em;
    }}
    .topbar-logo {{
      display:flex;align-items:center;justify-content:center;flex:0 0 auto;line-height:1;
      padding:6px 12px;border-radius:10px;
      background:linear-gradient(135deg,#cf7de8,#8b8dff);
      border:1px solid rgba(255,255,255,0.18);
      font-size:0.88rem;font-weight:900;letter-spacing:0.04em;color:#fff;
      box-shadow:0 6px 16px rgba(113,87,196,0.22);
    }}
    .topbar-title {{ color:var(--text);text-decoration:none; }}
    .topbar-accent {{
      background:var(--accent-gradient);-webkit-background-clip:text;
      -webkit-text-fill-color:transparent;background-clip:text;
    }}
    .topbar-nav {{ display:flex;gap:6px; }}
    .topbar-nav a {{
      padding:7px 16px;border-radius:10px;font-size:0.88rem;font-weight:600;
      color:var(--text-dim);text-decoration:none;transition:all 0.25s ease;
    }}
    .topbar-nav a:hover {{ color:var(--text);background:rgba(255,255,255,0.06); }}
    .topbar-nav a.active {{ color:#fff;background:rgba(167,139,250,0.18); }}
    /* ── Hero ── */
    .hero {{ margin-top:14px;display:grid;grid-template-columns:1fr;gap:16px; }}
    .glass-panel {{
      background:var(--bg-panel);border:1px solid var(--glass-border);
      border-radius:18px;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
      position:relative;overflow:hidden;
    }}
    .glass-panel::before {{
      content:"";
      position:absolute;inset:0;
      background:linear-gradient(170deg, rgba(255,255,255,0.04) 0%, transparent 40%);
      pointer-events:none;
    }}
    .hero-main {{ padding:30px 28px; }}
    .hero-eyebrow {{
      display:flex;align-items:center;gap:8px;font-size:0.78rem;
      color:var(--text-dim);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;
    }}
    .dot {{ width:7px;height:7px;border-radius:50%;background:#34d399;animation:pulse 2s infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.4}} }}
    .hero-heading {{ font-size:clamp(1.5rem,3vw,2rem);font-weight:900;letter-spacing:-0.02em;line-height:1.3;margin:0; }}
    .gradient-text {{ background:var(--accent-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text; }}
    .hero-desc {{ color:var(--text-dim);font-size:0.92rem;line-height:1.7;margin-top:12px; }}
    .hero-stats {{ display:flex;gap:28px;margin-top:22px; }}
    .stat-item {{ display:flex;flex-direction:column;gap:2px; }}
    .stat-value {{
      font-size:1.35rem;font-weight:900;letter-spacing:-0.01em;
      background:var(--accent-gradient);-webkit-background-clip:text;
      -webkit-text-fill-color:transparent;background-clip:text;
    }}
    .stat-item:nth-child(2) .stat-value {{
      background:linear-gradient(135deg,#34d399,#2dd4bf);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
    }}
    .stat-item:nth-child(3) .stat-value {{
      background:linear-gradient(135deg,#fbbf24,#fb7185);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
    }}
    .stat-label {{ font-size:0.72rem;color:var(--text-dim);letter-spacing:0.03em; }}
    /* ── NEW picks ── */
    .pickup-panel {{ margin-top:14px;padding:18px 16px; }}
    .side-header {{ display:flex;align-items:center;gap:10px;margin-bottom:16px; }}
    .side-header-icon {{
      width:28px;height:28px;border-radius:8px;
      background:linear-gradient(135deg,#fbbf24,#f97316);
      display:flex;align-items:center;justify-content:center;font-size:0.85rem;
    }}
    .side-title {{ font-size:1.05rem;font-weight:800;margin:0; }}
    .new-list {{ margin:0; }}
    .pickup-thumb-card {{
      display:block;position:relative;border:1px solid var(--glass-border);border-radius:12px;overflow:hidden;
      background:rgba(255,255,255,.03);text-decoration:none;color:var(--text);
    }}
    .pickup-thumb-card img {{ width:100%;aspect-ratio:16/9;object-fit:cover;display:block; }}
    .pickup-thumb-rank {{
      position:absolute;top:8px;left:8px;z-index:2;width:28px;height:28px;border-radius:8px;
      background:rgba(10,15,30,.82);color:#fff;display:flex;align-items:center;justify-content:center;font-size:.76rem;font-weight:800;
    }}
    .pickup-thumb-new {{ position:absolute;top:8px;right:8px;z-index:2; }}
    .pickup-thumb-title {{
      margin:0;padding:8px 10px 9px;font-size:.82rem;line-height:1.35;color:var(--text);
      display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
      background:rgba(11,16,26,.66);
    }}
    .new-badge {{
      padding:4px 12px;border-radius:999px;
      background:linear-gradient(135deg,#f472b6,#a78bfa);
      font-size:0.74rem;font-weight:800;color:#fff;white-space:nowrap;
    }}
    .new-text {{ font-size:0.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
    /* ── Content area ── */
    .content {{ padding:22px 24px;margin-top:16px; }}
    .content-head {{ display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px; }}
    .content-title {{ font-size:1.35rem;font-weight:800;margin:0;display:flex;align-items:center;gap:10px; }}
    .section-icon {{ font-style:normal; }}
    .filter-row {{ display:flex;align-items:center;gap:10px; }}
    .filter-divider {{ width:1px;height:22px;background:var(--glass-border); }}
    .type-tabs,.period-tabs {{
      display:flex;gap:2px;background:rgba(255,255,255,0.04);
      border-radius:12px;padding:3px;border:1px solid var(--glass-border);
    }}
    .type-tab,.period-tab {{
      border:none;background:transparent;color:var(--text-dim);
      border-radius:10px;padding:8px 18px;font-size:0.88rem;font-weight:700;
      cursor:pointer;transition:all 0.25s ease;font-family:inherit;
    }}
    .type-tab:hover,.period-tab:hover {{ color:var(--text);background:rgba(255,255,255,0.06); }}
    .type-tab.active {{ background:rgba(167,139,250,0.2);color:#fff;box-shadow:0 0 12px rgba(167,139,250,0.15); }}
    .period-tab.active {{ background:rgba(167,139,250,0.2);color:#fff;box-shadow:0 0 12px rgba(167,139,250,0.15); }}
    /* ── Ranking panel display ── */
    .ranking-panel {{ display:none; }}
    .ranking-panel.active {{ display:block; }}
    .period-panel {{ display:none; }}
    .period-panel.active {{ display:block; }}
    .group-panel {{ display:none; }}
    .group-panel.active {{ display:block; }}
    .content-panel {{ display:none; }}
    .content-panel.active {{ display:block; }}
    .content-tabs {{
      display:flex;gap:2px;background:rgba(255,255,255,0.04);
      border-radius:12px;padding:3px;border:1px solid var(--glass-border);
      width:max-content;max-width:100%;margin-bottom:12px;
    }}
    .tab-button {{
      border:none;background:transparent;color:var(--text-dim);
      border-radius:10px;padding:8px 18px;font-size:0.88rem;font-weight:700;
      cursor:pointer;transition:all 0.25s ease;font-family:inherit;
    }}
    .tab-button:hover {{ color:var(--text);background:rgba(255,255,255,0.06); }}
    .tab-button.active {{ background:rgba(167,139,250,0.2);color:#fff;box-shadow:0 0 12px rgba(167,139,250,0.15); }}
    .ranking-list {{ margin-top:8px; }}
    .lane-block {{
      margin-top:14px;padding:12px;border-radius:14px;
      border:1px solid var(--glass-border);background:rgba(255,255,255,0.02);
    }}
    .lane-block h3 {{
      margin:0 0 10px;font-size:0.95rem;font-weight:800;letter-spacing:0.01em;color:var(--text);
    }}
    .provisional-lane {{
      border-color:rgba(251,191,36,0.24);
      background:linear-gradient(180deg,rgba(251,191,36,0.08),rgba(255,255,255,0.02));
    }}
    /* ── Cards grid ── */
    .cards {{ display:grid;grid-template-columns:repeat(3,1fr);gap:14px; }}
    .cards.new-list {{ grid-template-columns:repeat(4,minmax(0,1fr));gap:10px; }}
    .card {{
      border:1px solid var(--glass-border);border-radius:18px;overflow:hidden;
      background:rgba(255,255,255,0.03);transition:transform 0.2s,box-shadow 0.2s;
    }}
    .card:hover {{ transform:translateY(-3px);box-shadow:0 14px 34px rgba(0,0,0,0.3); }}
    .card-focus {{ box-shadow:0 0 0 2px rgba(99,208,255,0.85), 0 14px 34px rgba(0,0,0,0.35); }}
    .card-rank-1 {{ border-color:var(--rank-gold);box-shadow:0 0 20px rgba(255,215,0,0.15); }}
    .card-rank-2 {{ border-color:var(--rank-silver);box-shadow:0 0 18px rgba(192,192,192,0.12); }}
    .card-rank-3 {{ border-color:var(--rank-bronze);box-shadow:0 0 18px rgba(205,127,50,0.12); }}
    .thumb {{
      position:relative;display:block;aspect-ratio:16/9;
      background:#1a1e30;border:0;padding:0;width:100%;
    }}
    .thumb img {{ width:100%;height:100%;object-fit:cover;display:block; }}
    .rank-badge {{
      position:absolute;top:10px;left:10px;
      width:32px;height:32px;border-radius:10px;
      background:rgba(10,15,30,0.82);color:#fff;
      display:flex;align-items:center;justify-content:center;
      font-size:0.85rem;font-weight:800;
    }}
    .rank-1 {{ background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#1a0a00; }}
    .rank-2 {{ background:linear-gradient(135deg,#94a3b8,#cbd5e1);color:#1a1a2e; }}
    .rank-3 {{ background:linear-gradient(135deg,#cd7f32,#b8860b);color:#1a0a00; }}
    .thumb-new-badge {{
      position:absolute;top:10px;right:10px;z-index:2;
    }}
    .duration-badge {{
      position:absolute;right:10px;bottom:10px;z-index:2;
      padding:3px 8px;border-radius:999px;
      background:rgba(10,15,30,0.82);color:#fff;
      font-size:0.74rem;font-weight:800;line-height:1;
    }}
    .card-meta {{ padding:15px 17px; }}
    .card-title {{
      display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;
      overflow:hidden;font-size:1.01rem;font-weight:700;line-height:1.5;
      min-height:2.6em;margin-bottom:10px;color:var(--text);text-decoration:none;
    }}
    .card-info {{ display:flex;align-items:center;gap:8px;font-size:0.87rem;color:var(--text-dim); }}
    .card-info-top {{ margin-bottom:6px;justify-content:space-between; }}
    .card-info-bottom {{ justify-content:space-between;align-items:flex-end;margin-bottom:8px;font-size:0.84rem; }}
    .card-date {{ color:var(--text-dim);white-space:nowrap; }}
    .card-actions {{
      display:flex;gap:8px;margin-top:10px;padding-top:10px;
      border-top:1px solid rgba(100,160,240,0.16);
    }}
    .card-action-link {{
      display:inline-flex;align-items:center;justify-content:center;gap:4px;
      font-size:0.72rem;font-weight:600;color:var(--text-dim);
      background:rgba(255,255,255,0.04);border:1px solid var(--glass-border);
      border-radius:6px;padding:5px 10px;text-decoration:none;cursor:pointer;
      transition:all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    .card-action-link:hover {{
      background:rgba(99,208,255,0.14);color:#dff4ff;border-color:rgba(138,215,255,0.55);
    }}
    .card-action-icon {{ width:14px;height:14px;display:inline-block;vertical-align:middle; }}
    .card-detail-link {{ margin-left:auto; }}
    .channel-link {{ display:inline-flex;align-items:center;gap:6px;text-decoration:none;color:var(--text-dim);min-width:0;flex:1; }}
    .channel-icon {{
      width:20px;height:20px;border-radius:50%;object-fit:cover;flex:0 0 20px;
      border:1px solid var(--glass-border);background:rgba(255,255,255,0.08);
    }}
    .channel-icon-fallback {{
      width:20px;height:20px;border-radius:50%;border:1px solid var(--glass-border);
      color:var(--text-dim);font-size:0.6rem;align-items:center;justify-content:center;flex:0 0 20px;
    }}
    .channel-name {{ overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;font-size:0.9rem; }}
    .channel-avatar {{ width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#f472b6,#a78bfa);flex:0 0 18px; }}
    .card-views {{
      white-space:nowrap;font-weight:700;font-size:0.9rem;
      display:flex;align-items:center;gap:4px;color:#82f2c2;
    }}
    .card-views .view-growth {{
      color:#82f2c2;
    }}
    .card-metrics-stack {{
      display:inline-flex;flex-direction:column;align-items:flex-start;gap:2px;
    }}
    .card-likes {{
      white-space:nowrap;font-weight:700;font-size:0.88rem;
      display:inline-flex;align-items:center;gap:4px;color:#ffb4cf;
    }}
    .card-likes .like-icon {{ font-style:normal;line-height:1; }}
    .arrow {{ font-style:normal;color:#34d399; }}
    .pill {{ border:1px solid var(--glass-border);border-radius:999px;padding:2px 8px;white-space:nowrap;font-size:0.72rem;color:var(--text-dim);margin-left:auto;flex:0 0 auto; }}
    .empty {{ padding:20px;border:1px dashed var(--glass-border);color:var(--text-dim);background:rgba(255,255,255,0.03); }}
    /* ── Pagination tabs ── */
    .page-tabs {{ display:none;gap:4px;flex-wrap:wrap;margin-top:12px;margin-bottom:10px; }}
    .page-tabs.bottom {{ margin-top:16px;margin-bottom:0;justify-content:center; }}
    .page-tab {{
      border:1px solid rgba(100,160,240,0.12);background:rgba(255,255,255,0.03);
      color:var(--text-dim);border-radius:8px;padding:6px 14px;font-size:0.82rem;
      font-weight:700;cursor:pointer;transition:all 0.25s ease;font-family:inherit;
    }}
    .page-tab:hover {{ color:var(--text);background:rgba(255,255,255,0.06); }}
    .page-tab.active {{ background:rgba(167,139,250,0.15);border-color:rgba(167,139,250,0.3);color:#fff; }}
    /* ── Back to top ── */
    .back-to-top {{ text-align:center;margin-top:28px; }}
    .back-to-top a {{
      display:inline-flex;align-items:center;gap:6px;
      padding:12px 28px;border-radius:14px;
      border:1px solid var(--glass-border);background:var(--bg-panel);
      color:var(--text-dim);text-decoration:none;font-size:0.88rem;font-weight:600;
      transition:all 0.25s ease;backdrop-filter:blur(12px);
    }}
    .back-to-top a:hover {{ color:var(--text);background:rgba(255,255,255,0.08);border-color:rgba(167,139,250,0.3); }}
    .arrow-up {{ font-style:normal;transform:translateY(-1px); }}
    /* ── Footer ── */
    .footer {{
      text-align:center;color:var(--text-dim);font-size:0.78rem;
      letter-spacing:0.02em;margin-top:18px;opacity:0.6;padding-bottom:24px;
      display:flex;flex-direction:column;gap:6px;align-items:center;
    }}
    .footer a {{ color:var(--text-dim);text-decoration:none;transition:color 0.2s; }}
    .footer a:hover {{ color:var(--text); }}
    .footer-links {{ display:flex;gap:16px; }}
    /* ── Animations ── */
    @keyframes fadeUp {{ from{{opacity:0;transform:translateY(16px)}} to{{opacity:1;transform:translateY(0)}} }}
    .animate-in {{ animation:fadeUp 0.6s ease forwards;opacity:0; }}
    .delay-1 {{ animation-delay:0.1s; }}
    .delay-2 {{ animation-delay:0.2s; }}
    .delay-3 {{ animation-delay:0.3s; }}
    /* ── Admin ── */
    .admin-board {{ border:1px solid var(--glass-border);background:linear-gradient(180deg,#121a25,#0f161f);box-shadow:0 24px 60px rgba(0,0,0,0.35);margin-top:12px;padding:14px;border-radius:16px; }}
    .admin-board-head h2 {{ font-size:1rem;margin:0;color:#e7f2ff; }}
    .admin-board-head p {{ color:#a9bed6;margin-top:4px;font-size:0.82rem; }}
    .admin-metric-grid {{ margin-top:10px;display:grid;gap:8px;grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .admin-metric-card {{ border:1px solid var(--glass-border);background:linear-gradient(180deg,#172232,#121a25);padding:9px 10px;min-height:62px;display:grid;align-content:center;gap:4px;border-radius:10px; }}
    .admin-metric-card span {{ color:#afc3db;font-size:0.75rem; }}
    .admin-metric-card strong {{ font-size:1rem;font-weight:800;color:#eaf3ff; }}
    .admin-quota {{ display:inline-flex;align-items:center;gap:10px;font-size:0.88rem;color:#b8cade; }}
    .admin-pill {{ border-radius:999px;padding:4px 10px;font-weight:700;color:#081017; }}
    .admin-pill.ok {{ background:#5ee0b0; }}
    .admin-pill.warn {{ background:#f4b942; }}
    .admin-pill.danger {{ background:#ff7c7c; }}
    .admin-pill.muted {{ background:#9fb2c1; }}
    .admin-share-row {{ margin-top:10px;display:flex;flex-wrap:wrap;gap:8px; }}
    .admin-share-btn {{
      border:1px solid rgba(138,215,255,0.35);background:rgba(99,208,255,0.12);color:#dff4ff;
      border-radius:10px;padding:7px 12px;font-size:0.82rem;font-weight:700;cursor:pointer;font-family:inherit;
      transition:all 0.2s ease;
    }}
    .admin-share-btn:hover {{ background:rgba(99,208,255,0.2);border-color:rgba(138,215,255,0.55); }}
    .admin-target-toggle {{
      margin-top:8px;
      display:flex;
      gap:8px;
      flex-wrap:wrap;
    }}
    .admin-target-btn {{
      border:1px solid rgba(138,215,255,0.35);
      background:rgba(99,208,255,0.06);
      color:#dff4ff;
      border-radius:9px;
      padding:6px 10px;
      font-size:0.78rem;
      font-weight:700;
      cursor:pointer;
      font-family:inherit;
    }}
    .admin-target-btn.active {{
      background:rgba(99,208,255,0.22);
      border-color:rgba(138,215,255,0.65);
    }}
    .admin-trending-picker {{
      margin-top:10px;
      display:grid;
      gap:10px;
      grid-template-columns:repeat(2,minmax(0,1fr));
    }}
    .admin-picker-card {{
      border:1px solid var(--glass-border);
      background:linear-gradient(180deg,#172232,#121a25);
      border-radius:12px;
      padding:10px;
      display:grid;
      gap:8px;
    }}
    .admin-picker-head {{
      font-size:0.8rem;
      color:var(--text-dim);
      font-weight:700;
      letter-spacing:0.02em;
    }}
    .admin-picker-select {{
      width:100%;
      border:1px solid rgba(138,215,255,0.35);
      background:#0f1a24;
      color:#e8edf4;
      border-radius:10px;
      padding:7px 9px;
      font-family:inherit;
      font-size:0.82rem;
    }}
    .admin-picker-preview {{
      white-space:pre-wrap;
      margin:0;
      border:1px solid rgba(138,215,255,0.18);
      border-radius:10px;
      background:rgba(7,12,18,0.6);
      color:#dbe8f4;
      padding:8px 9px;
      font-size:0.76rem;
      line-height:1.5;
      min-height:84px;
    }}
    .admin-picker-actions {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
    }}
    .admin-picker-btn {{
      border:1px solid rgba(138,215,255,0.35);
      background:rgba(99,208,255,0.1);
      color:#dff4ff;
      border-radius:9px;
      padding:6px 10px;
      font-size:0.78rem;
      font-weight:700;
      cursor:pointer;
      font-family:inherit;
      transition:all 0.2s ease;
    }}
    .admin-picker-btn:hover {{ background:rgba(99,208,255,0.2);border-color:rgba(138,215,255,0.55); }}
    .admin-picker-empty {{
      color:var(--text-dim);
      font-size:0.76rem;
      border:1px dashed rgba(138,215,255,0.2);
      border-radius:9px;
      padding:8px 9px;
      margin:0;
    }}
    /* ── Responsive ── */
    @media (max-width:1024px) {{
      .hero {{ grid-template-columns:1fr; }}
      .cards {{ grid-template-columns:repeat(2,1fr); }}
      .cards.new-list {{ grid-template-columns:repeat(4,minmax(0,1fr)); }}
      .admin-metric-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .admin-trending-picker {{ grid-template-columns:1fr; }}
    }}
    @media (max-width:760px) {{
      .shell {{ width:calc(100% - 20px);padding:12px 0 40px; }}
      .topbar-nav {{ display:none; }}
      .topbar {{ padding:10px 14px;border-radius:12px; }}
      .topbar-brand {{ gap:8px;font-size:0.9rem; }}
      .topbar-title {{ font-size:0.94rem;font-weight:900;line-height:1.2; }}
      .topbar-logo {{
        padding:4px 9px;border-radius:8px;font-size:0.72rem;font-weight:800;letter-spacing:0.03em;
        background:linear-gradient(135deg,#c37dde,#8a8ef8);
        border-color:rgba(255,255,255,0.14);box-shadow:none;
      }}
      .hero {{ margin-top:12px;gap:12px; }}
      .glass-panel {{ border-radius:14px; }}
      .hero-main {{ padding:22px 16px; }}
      .hero-eyebrow {{ font-size:0.7rem;margin-bottom:10px; }}
      .hero-heading {{ font-size:1.35rem;line-height:1.35; }}
      .hero-desc {{ font-size:0.85rem;margin-top:10px; }}
      .hero-stats {{ flex-wrap:wrap;gap:16px;margin-top:16px; }}
      .stat-value {{ font-size:1.15rem; }}
      .stat-label {{ font-size:0.68rem; }}
      .pickup-panel {{ margin-top:10px;padding:14px 12px; }}
      .side-header {{ margin-bottom:12px; }}
      .side-header-icon {{ width:24px;height:24px;font-size:0.75rem; }}
      .side-title {{ font-size:0.95rem; }}
      .cards.new-list {{ gap:8px;grid-template-columns:1fr; }}
      .new-badge {{ padding:3px 9px;font-size:0.68rem; }}
      .content {{ padding:16px 14px; }}
      .content-head {{ flex-direction:column;align-items:flex-start;gap:12px;margin-bottom:16px; }}
      .content-title {{ font-size:1.2rem;gap:8px; }}
      .filter-row {{ width:100%;gap:8px; }}
      .filter-divider {{ display:none; }}
      .type-tabs,.period-tabs {{ border-radius:10px; }}
      .type-tab {{ padding:7px 16px;font-size:0.82rem; }}
      .period-tab {{ padding:7px 14px;font-size:0.82rem; }}
      .content-tabs {{ margin-bottom:10px;padding:2px;border-radius:10px; }}
      .tab-button {{ padding:7px 14px;font-size:0.82rem;border-radius:9px; }}
      .lane-block {{ margin-top:10px;padding:10px;border-radius:12px; }}
      .lane-block h3 {{ margin-bottom:8px;font-size:0.86rem; }}
      .page-tabs {{ display:flex;gap:3px;margin-bottom:12px; }}
      .page-tab {{ padding:5px 10px;font-size:0.75rem;border-radius:6px; }}
      .cards {{ grid-template-columns:1fr;gap:12px; }}
      .card {{ border-radius:14px; }}
      .card-meta {{ padding:12px 14px; }}
      .card-title {{ font-size:0.94rem;margin-bottom:8px; }}
      .card-info {{ font-size:0.8rem; }}
      .rank-badge {{ width:28px;height:28px;font-size:0.78rem;border-radius:8px;top:8px;left:8px; }}
      .back-to-top {{ margin-top:20px; }}
      .back-to-top a {{ padding:10px 24px;font-size:0.82rem;border-radius:10px; }}
      .footer {{ margin-top:20px;font-size:0.72rem;padding-bottom:16px; }}
      .footer-links {{ gap:12px;font-size:0.72rem; }}
      .admin-metric-grid {{ grid-template-columns:1fr; }}
    }}
    @media (max-width:400px) {{
      .topbar-brand {{ align-items:center; }}
      .topbar-title {{ display:inline-block;white-space:nowrap; }}
      .topbar-title {{ font-size:0.84rem;font-weight:900;line-height:1.2; }}
      .hero-heading {{ font-size:1.15rem; }}
      .hero-stats {{ gap:12px; }}
      .stat-value {{ font-size:1rem; }}
      .filter-row {{ flex-direction:column;align-items:flex-start; }}
    }}

    /* ==== v2 visual override (new_design_proposal_v2_sample aligned) ==== */
    :root {{
      --v2-bg: #ffffff;
      --v2-header: rgba(11, 17, 28, 0.92);
      --v2-border: #d3dde8;
      --v2-border-soft: #e3ebf5;
      --v2-text: #111827;
      --v2-sub: #374151;
      --v2-muted: #4b5563;
      --v2-accent-a: #63d0ff;
      --v2-accent-b: #60a5fa;
      --v2-surface: #ffffff;
      --v2-panel: #ffffff;
      --v2-shadow-sm: 0 8px 18px rgba(15, 23, 42, 0.10);
      --v2-shadow-md: 0 14px 30px rgba(15, 23, 42, 0.16);
    }}
    body {{
      color: var(--v2-text);
      background:
        radial-gradient(900px 420px at 10% -10%, rgba(99, 208, 255, 0.08), transparent 60%),
        radial-gradient(760px 380px at 92% -12%, rgba(96, 165, 250, 0.06), transparent 58%),
        var(--v2-bg);
    }}
    .bg-canvas {{ display: none; }}
    .shell {{
      width: 100%;
      max-width: none;
      margin: 0;
      padding: 0 24px 40px;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 100;
      height: 60px;
      margin: 0 -24px;
      padding: 0 24px;
      border-radius: 0;
      border-top: 2px solid rgba(99, 208, 255, 0.34);
      border-bottom: 1px solid rgba(100, 160, 240, 0.18);
      border-left: 0;
      border-right: 0;
      background: var(--v2-header);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      box-shadow: 0 8px 20px rgba(0,0,0,0.25);
    }}
    .topbar-brand {{ font-size: 1.05rem; gap: 12px; }}
    .topbar-logo {{
      width: 44px;
      height: 30px;
      padding: 0;
      border-radius: 8px;
      font-size: 0.72rem;
      background: linear-gradient(135deg, #63d0ff, #a78bfa);
      box-shadow: none;
      border: 0;
    }}
    .topbar-title {{
      color: #e8edf4;
      font-size: 1.05rem;
      font-weight: 800;
    }}
    .topbar-accent {{
      background: linear-gradient(135deg,#63d0ff,#a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .hero {{ margin-top: 16px; gap: 14px; }}
    .glass-panel {{
      border-radius: 12px;
      border: 1px solid var(--v2-border);
      background: var(--v2-panel);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
      box-shadow: var(--v2-shadow-sm);
    }}
    .glass-panel::before {{ display: none; }}
    .hero-main {{
      background: linear-gradient(135deg, rgba(11, 17, 28, 0.97), rgba(9, 18, 46, 0.95));
      border: 1px solid rgba(99, 208, 255, 0.24);
      border-radius: 16px;
      color: #e0edff;
      box-shadow: 0 14px 30px rgba(0, 0, 0, 0.28);
    }}
    .hero-eyebrow,
    .hero-desc,
    .stat-label {{ color: rgba(208, 226, 255, 0.86); }}
    .hero-heading {{ color: #e0edff; font-weight: 600; line-height: 1.25; }}
    .gradient-text {{
      background: linear-gradient(135deg, #7dd3fc, #60a5fa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .stat-value {{
      background: linear-gradient(135deg, #63d0ff, #a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .stat-item:nth-child(2) .stat-value {{
      background: linear-gradient(135deg,#34d399,#2dd4bf);
      -webkit-background-clip:text;
      -webkit-text-fill-color:transparent;
      background-clip:text;
    }}
    .stat-item:nth-child(3) .stat-value {{
      background: linear-gradient(135deg,#fbbf24,#f59e0b);
      -webkit-background-clip:text;
      -webkit-text-fill-color:transparent;
      background-clip:text;
    }}
    .pickup-panel,
    .content {{
      background: #ffffff;
      border: 1px solid var(--v2-border);
      box-shadow: var(--v2-shadow-sm);
    }}
    .content-title,
    .side-title {{ color: #0f294f; }}
    .side-header-icon {{
      background: #dff4ff;
      color: #1f3b6f;
      border: 1px solid #b8c8de;
    }}
    .type-tabs,
    .period-tabs,
    .content-tabs {{
      background: #eef3f8;
      border: 1px solid #c8d2dd;
      border-radius: 10px;
      padding: 3px;
    }}
    .type-tab,.period-tab,.tab-button {{
      color: #1f3b6f;
      border-radius: 8px;
      font-size: 0.9rem;
    }}
    .type-tab:hover,.period-tab:hover,.tab-button:hover {{
      color: #1f3b6f;
      background: #e5edf6;
    }}
    .type-tab.active,.period-tab.active,.tab-button.active {{
      color: #fff;
      background: linear-gradient(135deg, #63d0ff, #60a5fa);
      box-shadow: 0 2px 8px rgba(96, 165, 250, 0.30);
    }}
    .filter-divider {{ background: var(--v2-border); }}
    .lane-block {{
      background: transparent;
      border-color: transparent;
      padding: 0;
      margin-top: 10px;
    }}
    .lane-block h3 {{
      color: #475569;
      font-size: 0.86rem;
      margin: 0 0 8px;
    }}
    .cards.new-list {{
      grid-template-columns: repeat(4,minmax(0,1fr));
      gap: 12px;
    }}
    .pickup-card {{
      display: block;
      text-decoration: none;
      color: inherit;
      background: #ffffff;
      border: 1px solid var(--v2-border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: var(--v2-shadow-sm);
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .pickup-card:hover,
    .pickup-card:focus-visible {{
      transform: translateY(-2px);
      box-shadow: var(--v2-shadow-md);
    }}
    .pickup-card .pickup-thumb {{
      position: relative;
      aspect-ratio: 16 / 9;
      background: #1a1e30;
      overflow: hidden;
    }}
    .pickup-card img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .pickup-card .new-badge {{
      position: absolute;
      top: 8px;
      right: 8px;
      z-index: 2;
      font-size: 0.72rem;
      font-weight: 800;
      color: #fff;
      background: #f472b6;
      padding: 4px 10px;
      border-radius: 7px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .pickup-card .pickup-body {{
      padding: 10px 12px;
    }}
    .pickup-card .pickup-title {{
      margin: 0;
      font-size: 0.9rem;
      font-weight: 600;
      line-height: 1.4;
      color: var(--v2-text);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .pickup-card .pickup-info {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
    }}
    .pickup-card .pickup-channel {{
      display: flex;
      align-items: center;
      gap: 5px;
      min-width: 0;
      flex: 1;
    }}
    .pickup-card .pickup-channel .ch-icon {{
      width: 16px;
      height: 16px;
      border-radius: 50%;
      object-fit: cover;
      flex-shrink: 0;
      border: 1px solid #c8d2dd;
    }}
    .pickup-card .pickup-channel .channel-icon-fallback,
    .pickup-card .pickup-channel .channel-avatar {{
      width: 16px;
      height: 16px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #dbe7f3;
      color: #1f2937;
      font-size: 0.62rem;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .pickup-card .pickup-channel .ch-name {{
      font-size: 0.74rem;
      color: var(--v2-sub);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .pickup-card .group-tag {{
      flex-shrink: 0;
      font-size: 0.68rem;
      font-weight: 600;
      color: #1f2937;
      background: #dbe7f3;
      padding: 2px 8px;
      border-radius: 999px;
    }}
    .card {{
      background: var(--v2-surface);
      border: 1px solid var(--v2-border);
      border-top: 2px solid rgba(99, 208, 255, 0.32);
      border-radius: 12px;
      box-shadow: var(--v2-shadow-sm);
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: var(--v2-shadow-md);
    }}
    .card-rank-1, .card-rank-2, .card-rank-3 {{
      border-color: var(--v2-border);
      box-shadow: var(--v2-shadow-sm);
    }}
    .card-meta {{ padding: 12px 14px; }}
    .card-title {{
      color: var(--v2-text);
      font-size: 1rem;
      font-weight: 600;
      line-height: 1.45;
      margin-bottom: 8px;
      min-height: calc(1.45em * 2);
    }}
    .card-info {{ color: var(--v2-sub); }}
    .card-date {{ color: var(--v2-muted); }}
    .card-info-top,
    .card-info-bottom {{ color: var(--v2-sub); }}
    .channel-link {{
      color: var(--v2-sub);
      text-decoration: none;
    }}
    .channel-name {{
      color: var(--v2-sub);
      font-size: 0.84rem;
      font-weight: 500;
    }}
    .pill {{
      color: #1f2937;
      background: #dbe7f3;
      border: 0;
      font-size: 0.68rem;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 999px;
    }}
    .card-metrics-stack {{ gap: 4px; }}
    .card-views {{
      color: #10b981;
      font-size: 0.92rem;
      font-weight: 700;
      line-height: 1.15;
    }}
    .card-views .view-growth {{ color: #10b981; }}
    .arrow {{ color: #10b981; }}
    .card-likes {{
      color: #db2777;
      font-size: 0.92rem;
      font-weight: 700;
      line-height: 1.15;
    }}
    .card-date {{
      color: var(--v2-muted);
      font-size: 0.9rem;
      line-height: 1.15;
    }}
    .card-actions {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid rgba(100,160,240,0.12);
    }}
    .card-action-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--v2-sub);
      background: #e5eaf0;
      border: 1px solid #c8d2dd;
      border-radius: 6px;
      padding: 7px 10px;
      white-space: nowrap;
      text-decoration: none;
    }}
    .card-action-link:hover {{
      background: #e5eaf0;
      color: var(--v2-sub);
      border-color: #c8d2dd;
    }}
    .card-detail-link {{ margin-left: 0; }}
    .card-action-icon {{ width: 14px; height: 14px; }}
    .rank-badge {{
      top: 8px;
      left: 8px;
      min-width: 34px;
      height: 34px;
      border-radius: 7px;
      font-size: 0.88rem;
      font-weight: 800;
      background: rgba(26, 32, 44, 0.82);
      color: #fff;
    }}
    .rank-1 {{
      background: linear-gradient(135deg, #f59e0b, #facc15);
      color: #3b2a00;
      border: 1px solid rgba(180, 120, 0, 0.45);
    }}
    .rank-2 {{
      background: linear-gradient(135deg, #94a3b8, #e2e8f0);
      color: #1f2937;
      border: 1px solid rgba(100, 116, 139, 0.45);
    }}
    .rank-3 {{
      background: linear-gradient(135deg, #b45309, #d6a77a);
      color: #fff8ef;
      border: 1px solid rgba(120, 63, 4, 0.45);
    }}
    .thumb-new-badge {{
      position: absolute;
      top: 8px;
      right: 8px;
      z-index: 2;
      font-size: 0.72rem;
      font-weight: 800;
      color: #fff;
      background: #f472b6;
      padding: 4px 10px;
      border-radius: 7px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .page-tab {{
      min-width: 36px;
      height: 36px;
      color: #1f3b6f;
      background: #f8fafc;
      border: 1px solid #b8c8de;
      border-radius: 8px;
      font-size: 0.94rem;
      font-weight: 700;
    }}
    .page-tab:hover {{
      background: #f8fafc;
      color: #1f3b6f;
      border-color: #b8c8de;
    }}
    .page-tab.active {{
      background: linear-gradient(135deg, #63d0ff, #60a5fa);
      color: #fff;
      border-color: #63b1e6;
    }}
    .back-to-top a {{
      border: 1px solid #c8d2dd;
      background: #eef3f8;
      color: #1f3b6f;
      border-radius: 999px;
      box-shadow: none;
    }}
    .back-to-top a:hover {{
      transform: none;
      box-shadow: none;
      background: #eef3f8;
      color: #1f3b6f;
    }}
    .footer {{
      margin: 0 -24px;
      margin-top: 18px;
      padding: 18px 24px 22px;
      border-top: 1px solid rgba(100, 160, 240, 0.18);
      border-top-color: rgba(100, 160, 240, 0.18);
      background: rgba(11, 17, 28, 0.92);
      color: #dbeafe;
      opacity: 1;
      border-radius: 0;
      box-shadow: none;
    }}
    .footer-links a {{ color: #cfe0ff; }}
    /* sample structure bindings */
    .header {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(11, 17, 28, 0.92);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid rgba(100, 160, 240, 0.18);
      box-shadow: 0 8px 20px rgba(0,0,0,0.25);
      border-top: 2px solid rgba(99, 208, 255, 0.34);
      width: 100%;
    }}
    .header-inner {{
      padding: 0 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 60px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      text-decoration: none;
      color: #e8edf4;
    }}
    .brand-logo {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 30px;
      border-radius: 8px;
      background: linear-gradient(135deg, #63d0ff, #a78bfa);
      color: #fff;
      font-weight: 900;
      font-size: 0.72rem;
      letter-spacing: 0.04em;
    }}
    .brand-text {{ font-weight: 800; font-size: 1.05rem; }}
    .brand-accent {{
      background: linear-gradient(135deg,#63d0ff,#a78bfa);
      -webkit-background-clip:text;
      -webkit-text-fill-color:transparent;
      background-clip:text;
    }}
    .header-meta {{ color: rgba(232,237,244,0.9); font-size: 0.82rem; }}
    .live-dot {{
      display:inline-flex;
      align-items:center;
      gap:6px;
      color:#10b981;
      font-weight:600;
      font-size:0.78rem;
    }}
    .live-dot::before {{
      content:'';
      width:7px;height:7px;border-radius:50%;background:#10b981;
      animation:pulse 2s ease-in-out infinite;
    }}
    .main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 24px 60px;
    }}
    .hero-banner {{
      background: linear-gradient(135deg, rgba(11, 17, 28, 0.97), rgba(9, 18, 46, 0.95));
      border: 1px solid rgba(99, 208, 255, 0.24);
      border-radius: 16px;
      padding: 26px 28px;
      margin-bottom: 10px;
      box-shadow: 0 14px 30px rgba(0, 0, 0, 0.28);
    }}
    .hero-title {{
      margin: 0;
      color: #e0edff;
      font-size: clamp(1.35rem, 3.1vw, 2.15rem);
      line-height: 1.25;
      font-weight: 600;
    }}
    .hero-title-accent {{
      background: linear-gradient(135deg, #7dd3fc, #60a5fa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .hero-desc {{
      margin-top: 10px;
      color: rgba(208, 226, 255, 0.88);
      font-size: 1.02rem;
      line-height: 1.75;
      max-width: 760px;
      font-weight: 500;
    }}
    .stats-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0;
      margin-bottom: 28px;
      border: 1px solid rgba(99, 208, 255, 0.22);
      border-radius: 16px;
      overflow: hidden;
      background: linear-gradient(135deg, rgba(11, 17, 28, 0.96), rgba(9, 18, 46, 0.94));
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
    }}
    .stat-card {{ padding: 18px 20px; }}
    .stat-card + .stat-card {{ border-left: 1px solid rgba(99, 208, 255, 0.20); }}
    .stat-label {{ font-size: 0.76rem; color: rgba(232, 237, 244, 0.72); margin-top: 4px; }}
    .stat-value {{
      font-size: 1.4rem;
      font-weight: 900;
      background: linear-gradient(135deg,#63d0ff,#a78bfa);
      -webkit-background-clip:text;
      -webkit-text-fill-color:transparent;
      background-clip:text;
    }}
    .section-head {{
      display:flex;
      align-items:center;
      justify-content:space-between;
      margin-bottom:6px;
      gap:16px;
      flex-wrap:wrap;
    }}
    .section-title {{
      font-size:1.15rem;
      font-weight:800;
      display:flex;
      align-items:center;
      gap:10px;
      color:#1f3344;
    }}
    .section-icon {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      width:32px;height:32px;border-radius:8px;
      background:rgba(99,208,255,0.14);
      font-size:0.9rem;
      font-style:normal;
    }}
    .pickup-section {{ margin-bottom: 12px; }}
    .pickup-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
    .ranking-section {{ margin-top: 8px; }}
    .filter-bar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    .tab-group {{
      display:inline-flex;
      gap:2px;
      background:#eef3f8;
      border:1px solid #d5dee9;
      border-radius:10px;
      padding:3px;
    }}
    .tab-btn {{
      border:0;
      border-radius:8px;
      background:transparent;
      color:#334155;
      font-size:0.9rem;
      padding:7px 16px;
      font-weight:700;
      cursor:pointer;
      font-family:inherit;
    }}
    .tab-btn.active {{
      background: linear-gradient(135deg, #63d0ff, #a78bfa);
      color: #fff;
      box-shadow: 0 2px 10px rgba(99, 208, 255, 0.24);
    }}
    .filter-sep {{ width:1px; height:24px; background: rgba(100,160,240,0.18); }}
    .cards {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
    .pagination {{
      display:flex;
      gap:10px;
      margin: 0 0 14px;
      align-items: center;
      flex-wrap: wrap;
    }}
    #page-tabs-top.pagination {{ margin: 0 0 10px; }}
    #page-tabs-bottom.pagination {{ margin: 20px 0 8px; }}
    .page-btn {{
      min-width:36px;height:36px;display:inline-flex;align-items:center;justify-content:center;
      font-size:0.94rem;font-weight:700;color:#1f3b6f;background:#f8fafc;
      border:1px solid #b8c8de;border-radius:8px;cursor:pointer;font-family:inherit;
    }}
    .page-btn:hover {{
      border-color: #b8c8de;
      color: #1f3b6f;
      background: #f8fafc;
    }}
    .page-btn.active {{
      background: linear-gradient(135deg, #63d0ff, #60a5fa);
      color: #fff;
      border-color: #63b1e6;
      box-shadow: 0 2px 8px rgba(96, 165, 250, 0.30);
    }}
    .back-top-wrap {{ text-align:center; margin:18px 0 10px; }}
    .back-top-btn {{
      display:inline-flex; align-items:center; justify-content:center;
      padding:9px 18px; border-radius:999px; border:1px solid #c8d2dd;
      background:#eef3f8; color:#1f3b6f; text-decoration:none; font-size:0.84rem; font-weight:700;
    }}
    @media (max-width: 1024px) {{
      .pickup-grid {{ grid-template-columns: repeat(2,minmax(0,1fr)); }}
      .cards {{ grid-template-columns: repeat(2,1fr); }}
    }}
    @media (max-width: 760px) {{
      .header-inner {{ padding: 0 14px; height: 52px; }}
      .header-meta {{ display:none; }}
      .main {{ padding: 16px 14px 40px; }}
      .hero-banner {{ padding: 18px 16px; }}
      .hero-title {{ font-size: clamp(1.02rem, 5.4vw, 1.24rem); line-height: 1.28; }}
      .hero-title br, .hero-desc br {{ display: none; }}
      .hero-desc {{ font-size: 0.82rem; line-height: 1.45; margin-top: 6px; }}
      .stats-strip {{ grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 14px; }}
      .stat-card {{ padding: 8px 8px; text-align: center; }}
      .stat-card + .stat-card {{ border-left:1px solid rgba(99,208,255,0.20); border-top:0; }}
      .stat-value {{ font-size: 0.98rem; line-height: 1.2; }}
      .stat-label {{ font-size: 0.62rem; line-height: 1.2; margin-top: 2px; }}
      .cards {{ grid-template-columns:1fr; }}
      .pickup-grid {{ grid-template-columns: 1fr; }}
      .ranking-section {{ margin-top: 4px; }}
      .section-head {{
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 4px;
        gap: 8px;
      }}
      .section-title {{
        font-size: 1.03rem;
        gap: 8px;
      }}
      .section-icon {{
        width: 28px;
        height: 28px;
        font-size: 0.82rem;
      }}
      .filter-bar {{ width:100%; overflow-x:hidden; }}
      #page-tabs-top.pagination {{ margin: 8px 0 10px; }}
      .footer {{ margin: 18px 0 0; }}
    }}
  </style>
</head>
<body class="{body_class}" id="top">
  <header class="header">
    <div class="header-inner">
      <a class="brand" href="/">
        <span class="brand-logo">VCLIP</span>
        <span class="brand-text">VTuber\u5207\u308a\u629c\u304d<span class="brand-accent">\u30e9\u30f3\u30ad\u30f3\u30b0</span></span>
      </a>
      <div class="header-meta"><span class="live-dot">\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0\u66f4\u65b0\u4e2d</span></div>
    </div>
  </header>

  <main class="main">
    <section class="hero-banner">
      <h1 class="hero-title">
        VTuber\u5207\u308a\u629c\u304d\u306e<br>
        <span class="hero-title-accent">\u30c8\u30ec\u30f3\u30c9\u3092\u4e00\u76ee\u3067\u30c1\u30a7\u30c3\u30af</span>
      </h1>
      <p class="hero-desc">
        Shorts\u30fb\u52d5\u753b\u306e\u518d\u751f\u6570\u5897\u52a0\u3092\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0\u3067\u96c6\u8a08\u3002<br>
        \u3044\u307e\u8a71\u984c\u306e\u5207\u308a\u629c\u304d\u3092\u30e9\u30f3\u30ad\u30f3\u30b0\u5f62\u5f0f\u3067\u304a\u5c4a\u3051\u3057\u307e\u3059\u3002
      </p>
      {admin_html}
    </section>

    <section class="pickup-section">
      <div class="section-head">
        <h2 class="section-title"><span class="section-icon">\u2728</span>\u65b0\u7740\u30d4\u30c3\u30af\u30a2\u30c3\u30d7</h2>
      </div>
      <div id="new-list" class="pickup-grid"></div>
    </section>

    <section class="stats-strip" id="hero-stats"></section>
    {admin_board_html}

    <section class="ranking-section" id="ranking-section">
      <div class="section-head">
        <h2 class="section-title"><span class="section-icon" id="ranking-icon">▶</span><span id="ranking-label">Shorts \u30e9\u30f3\u30ad\u30f3\u30b0</span></h2>
        <div class="filter-bar">
          <div class="tab-group" id="type-tabs"></div>
          <div class="filter-sep"></div>
          <div class="tab-group" id="period-tabs"></div>
        </div>
      </div>
      <div class="pagination" id="page-tabs-top"></div>
      <div id="period-root"></div>
      <div class="pagination" id="page-tabs-bottom"></div>
    </section>

    <div class="back-top-wrap">
      <a id="back-to-top" class="back-top-btn" href="#top">TOP\u306b\u623b\u308b</a>
    </div>
  </main>

  <footer class="footer">
    <div class="footer-links">
      <a href="/policy">\u30d7\u30e9\u30a4\u30d0\u30b7\u30fc\u30dd\u30ea\u30b7\u30fc</a>
      <a href="https://x.com/Vcliprank" target="_blank" rel="noopener noreferrer">\u304a\u554f\u3044\u5408\u308f\u305b</a>
    </div>
    <span>VCLIP | VTuber\u5207\u308a\u629c\u304d\u30e9\u30f3\u30ad\u30f3\u30b0 &copy; 2026</span>
  </footer>
  <script>
    const payload = {payload_json};
    const heroStats = {hero_stats_json};
    const groupLabels = {group_labels_json};
    const showAdminMeta = {show_admin_meta};
    const typeTabs = document.getElementById("type-tabs");
    const periodTabs = document.getElementById("period-tabs");
    const periodRoot = document.getElementById("period-root");
    const backToTop = document.getElementById("back-to-top");
    const rankingSection = document.getElementById("ranking-section");
    const rankingIcon = document.getElementById("ranking-icon");
    const rankingLabel = document.getElementById("ranking-label");
    const pageTabsTop = document.getElementById("page-tabs-top");
    const pageTabsBottom = document.getElementById("page-tabs-bottom");
    const adminTargetToggle = document.getElementById("admin-target-toggle");
    let adminShareTargetType = "shorts";
    let activePeriod = "{first_period}";
    let activeContentType = "shorts";
    const PAGE_SIZE_MOBILE = 20;
    const MOBILE_BREAKPOINT = 760;
    const pageState = {{}};
    let cachedNewPickPool = null;
    let newPickLayoutMode = window.innerWidth <= MOBILE_BREAKPOINT ? "mobile" : "desktop";
    const typeConfig = {{
      shorts: {{ icon: "▶", label: "Shorts \u30e9\u30f3\u30ad\u30f3\u30b0" }},
      video:  {{ icon: "▦", label: "\u52d5\u753b\u30e9\u30f3\u30ad\u30f3\u30b0" }}
    }};

    /* ── Pagination helpers ── */
    function paginationKey() {{
      return `${{activePeriod}}::${{activeContentType}}`;
    }}
    function getCurrentCards() {{
      const activePanel = periodRoot.querySelector(".period-panel.active");
      if (!activePanel) return [];
      const contentPanel = activePanel.querySelector(`.content-panel[data-content-panel="${{activeContentType}}"]`);
      if (!contentPanel) return [];
      return Array.from(contentPanel.querySelectorAll(".card"));
    }}
    function buildDesktopPageRanges(totalItems) {{
      if (totalItems <= 0) return [[0, 0]];
      if (totalItems <= 51) return [[0, totalItems]];
      return [[0, 51], [51, totalItems]];
    }}
    function buildMobilePageRanges(totalItems) {{
      const ranges = [];
      for (let start = 0; start < totalItems; start += PAGE_SIZE_MOBILE) {{
        ranges.push([start, Math.min(start + PAGE_SIZE_MOBILE, totalItems)]);
      }}
      return ranges.length ? ranges : [[0, 0]];
    }}
    function applyPagination() {{
      const cards = getCurrentCards();
      const isMobile = window.innerWidth <= MOBILE_BREAKPOINT;
      const ranges = isMobile ? buildMobilePageRanges(cards.length) : buildDesktopPageRanges(cards.length);
      const totalPages = Math.max(1, ranges.length);
      const key = paginationKey();
      let currentPage = pageState[key] || 1;
      if (currentPage > totalPages) currentPage = totalPages;
      if (currentPage < 1) currentPage = 1;
      pageState[key] = currentPage;
      const currentRange = ranges[currentPage - 1];
      const start = currentRange[0];
      const end = currentRange[1];
      cards.forEach((card, i) => {{
        card.style.display = (i >= start && i < end) ? "" : "none";
      }});
      renderPageTabs(ranges, currentPage, isMobile);
    }}
    function renderPageTabs(ranges, currentPage, isMobile) {{
      [pageTabsTop, pageTabsBottom].forEach(container => {{
        container.innerHTML = "";
        container.style.display = "flex";
        if (ranges.length <= 1) return;
        for (let p = 1; p <= ranges.length; p++) {{
          const range = ranges[p - 1];
          const btn = document.createElement("button");
          btn.className = "page-btn" + (p === currentPage ? " active" : "");
          btn.textContent = String(p);
          btn.type = "button";
          const page = p;
          btn.addEventListener("click", () => {{
            pageState[paginationKey()] = page;
            applyPagination();
            if (container === pageTabsBottom && rankingSection) {{
              rankingSection.scrollIntoView({{ behavior: "smooth", block: "start" }});
            }}
          }});
          container.appendChild(btn);
        }}
      }});
    }}
    /* ── Build hero stats from payload ── */
    function buildHeroStats() {{
      const statsEl = document.getElementById("hero-stats");
      if (!statsEl) return;
      const tracking = Number(heroStats?.tracking_videos || 0);
      const growth = Number(heroStats?.daily_growth_total || 0);
      const fresh = Number(heroStats?.new_24h || 0);
      statsEl.innerHTML = `
        <article class="stat-card"><div class="stat-value">${{tracking.toLocaleString("ja-JP")}}</div><div class="stat-label">Tracking Videos</div></article>
        <article class="stat-card"><div class="stat-value">${{growth.toLocaleString("ja-JP")}}</div><div class="stat-label">Daily View Growth</div></article>
        <article class="stat-card"><div class="stat-value">${{fresh.toLocaleString("ja-JP")}}</div><div class="stat-label">New (24h)</div></article>
      `;
    }}
    function getDailyTop3Items(contentType) {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const daily = payload.find((p) => p.table === "daily");
      if (!daily || !daily.groups || !daily.groups.all) return [];
      const tmp = document.createElement("div");
      tmp.innerHTML = daily.groups.all;
      const cards = Array.from(
        tmp.querySelectorAll(`.content-panel[data-content-panel="${{normalized}}"] .card`)
      ).slice(0, 3);
      return cards.map((card, idx) => {{
        const titleEl = card.querySelector(".card-title");
        const videoId = (card.dataset.videoId || "").trim();
        return {{
          rank: idx + 1,
          videoId,
          title: (titleEl ? titleEl.textContent : "").trim(),
        }};
      }}).filter((item) => item.title && item.videoId);
    }}
    function normalizeShareTitle(text) {{
      return (text || "").replace(/\\s+/g, " ").trim();
    }}
    function truncateShareTitle(text, maxLen) {{
      const normalized = normalizeShareTitle(text);
      if (!normalized) return "";
      if (normalized.length <= maxLen) return normalized;
      return normalized.slice(0, Math.max(1, maxLen - 1)).trimEnd() + "…";
    }}
    function openDailyTop3Share(contentType) {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = normalized === "video" ? "動画" : "Shorts";
      const top3 = getDailyTop3Items(normalized);
      if (!top3.length) {{
        window.alert(`本日${{label}}のランキングデータがありません。`);
        return;
      }}
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      const rankEmojis = ["🥇", "🥈", "🥉"];
      const maxTextLen = 240;
      let titleMaxLen = 34;
      let lines = [];
      while (titleMaxLen >= 18) {{
        lines = [
          `🔥本日(${{monthDay}})の #VTuber切り抜きランキング Top3`,
          `（${{label}}）`,
          "",
          ...top3.map((item, idx) => `${{rankEmojis[idx] || "🏅"}}${{item.rank}}位: ${{truncateShareTitle(item.title, titleMaxLen)}}`),
        ];
        if (lines.join("\\n").length <= maxTextLen) break;
        titleMaxLen -= 2;
      }}
      const params = new URLSearchParams({{
        text: lines.join("\\n"),
        url: "https://vclipranking.com/",
      }});
      const shareUrl = `https://twitter.com/intent/tweet?${{params.toString()}}`;
      window.open(shareUrl, "_blank", "noopener,noreferrer");
    }}
    function getDailyTopItems(contentType, limit = 3) {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const daily = payload.find((p) => p.table === "daily");
      if (!daily || !daily.groups || !daily.groups.all) return [];
      const tmp = document.createElement("div");
      tmp.innerHTML = daily.groups.all;
      const cards = Array.from(
        tmp.querySelectorAll(`.content-panel[data-content-panel="${{normalized}}"] .card`)
      );
      const items = cards.map((card) => {{
        const thumb = card.querySelector(".thumb");
        const titleEl = card.querySelector(".card-title");
        const hasNewBadge = !!card.querySelector(".new-badge");
        const videoId = (thumb?.dataset?.videoId || "").trim();
        const rank = Number(card.dataset.rank || 0);
        const prevRank = Number(card.dataset.prevRank || 0);
        const growthPct = Number(card.dataset.viewGrowthPct || 0);
        const viewGrowth = Number(card.dataset.viewGrowth || 0);
        const rankUp = prevRank > 0 && rank > 0 ? (prevRank - rank) : 0;
        return {{
          contentType: normalized,
          videoId,
          isNew: hasNewBadge,
          title: normalizeShareTitle(titleEl ? titleEl.textContent : ""),
          rank: Number.isFinite(rank) ? rank : 0,
          prevRank: Number.isFinite(prevRank) ? prevRank : 0,
          growthPct: Number.isFinite(growthPct) ? growthPct : 0,
          viewGrowth: Number.isFinite(viewGrowth) ? viewGrowth : 0,
          rankUp: Number.isFinite(rankUp) ? rankUp : 0,
        }};
      }}).filter((item) => item.videoId && item.isNew);
      const maxViewGrowth = Math.max(1, ...items.map((item) => Math.max(0, Number(item.viewGrowth || 0))));
      const maxRankUp = Math.max(1, ...items.map((item) => Math.max(0, Number(item.rankUp || 0))));
      items.forEach((item) => {{
        const viewNorm = Math.max(0, Number(item.viewGrowth || 0)) / maxViewGrowth;
        const rankNorm = Math.max(0, Number(item.rankUp || 0)) / maxRankUp;
        item.momentumScore = (viewNorm * 0.5) + (rankNorm * 0.5);
      }});
      items.sort((a, b) =>
        (b.momentumScore - a.momentumScore) ||
        (b.rankUp - a.rankUp) ||
        (b.viewGrowth - a.viewGrowth) ||
        (a.rank - b.rank)
      );
      return items.slice(0, Math.max(1, limit));
    }}
    function parseMetricNumber(text) {{
      const normalized = String(text || "").replace(/[^0-9-]/g, "");
      const parsed = Number(normalized);
      return Number.isFinite(parsed) ? parsed : 0;
    }}
    function parseJstDateLabelToDate(value) {{
      const raw = String(value || "").trim();
      if (!raw) return null;
      const iso = raw.replace(" ", "T") + ":00+09:00";
      const parsed = new Date(iso);
      return Number.isNaN(parsed.getTime()) ? null : parsed;
    }}
    function getDailyCardMetrics(contentType) {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const daily = payload.find((p) => p.table === "daily");
      if (!daily || !daily.groups || !daily.groups.all) return [];
      const tmp = document.createElement("div");
      tmp.innerHTML = daily.groups.all;
      const cards = Array.from(tmp.querySelectorAll(`.content-panel[data-content-panel="${{normalized}}"] .card`));
      return cards.map((card) => {{
        const thumb = card.querySelector(".thumb");
        const titleEl = card.querySelector(".card-title");
        const videoId = (thumb?.dataset?.videoId || "").trim();
        const title = normalizeShareTitle(titleEl ? titleEl.textContent : "");
        const likeGrowth = Number(card.dataset.likeGrowth || 0);
        const commentGrowth = Number(card.dataset.commentGrowth || 0);
        const publishedIso = String(card.dataset.publishedAt || "").trim();
        const publishedAt = publishedIso ? new Date(publishedIso) : null;
        const viewGrowth = Number(card.dataset.viewGrowth || 0);
        return {{
          contentType: normalized,
          videoId,
          title,
          likeGrowth: Number.isFinite(likeGrowth) ? likeGrowth : 0,
          commentGrowth: Number.isFinite(commentGrowth) ? commentGrowth : 0,
          viewGrowth: Number.isFinite(viewGrowth) ? viewGrowth : 0,
          publishedAt: publishedAt && !Number.isNaN(publishedAt.getTime()) ? publishedAt : null,
        }};
      }}).filter((item) => item.videoId);
    }}
    function buildTrendingTemplateText(item) {{
      const isVideo = item?.contentType === "video";
      const label = isVideo ? "動画" : "Shorts";
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      const detailUrl = item?.videoId ? `${{window.location.origin}}/video/${{item.videoId}}` : "詳細URL";
      const title = item ? truncateShareTitle(item.title, 60) : "動画タイトル";
      const rankText = item && item.rank > 0 ? `${{item.rank}}位` : "順位データなし";
      const growthText = item ? `+${{Number(item.viewGrowth || 0).toLocaleString("ja-JP")}}` : "+0";
      return [
        `🔥現在(${{monthDay}})、急上昇中の${{label}}です。`,
        "",
        `「${{title}}」`,
        detailUrl,
        `24h ${{rankText}} 再生増加 ${{growthText}} #VCLIP`,
      ].join("\\n");
    }}
    function targetLabelFromType(contentType) {{
      return (contentType || "").toLowerCase() === "video" ? "動画" : "Shorts";
    }}
    function buildOverallDataShareText(contentType = "shorts") {{
      const tracking = Number(heroStats?.tracking_videos || 0);
      const growth = Number(heroStats?.daily_growth_total || 0);
      const fresh = Number(heroStats?.new_24h || 0);
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      return [
        `📊VCLIP全体データ（24h ${{monthDay}}）`,
        `トラッキング動画数: ${{tracking.toLocaleString("ja-JP")}}`,
        `総再生増加: +${{growth.toLocaleString("ja-JP")}} / 新着動画: ${{fresh.toLocaleString("ja-JP")}}`,
        "#VCLIP",
      ].join("\\n");
    }}
    function buildTop3CategoryText(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = normalized === "video" ? "動画" : "Shorts";
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      const top3 = getDailyTop3Items(normalized);
      if (!top3.length) return `本日(${{monthDay}})の${{label}}TOP3データがありません。 #VCLIP`;
      const rankEmojis = ["🥇", "🥈", "🥉"];
      return [
        `🏆本日(${{monthDay}})の${{label}} TOP3`,
        "",
        ...top3.flatMap((item, idx) => [
          `${{rankEmojis[idx] || "🏅"}}${{item.rank}}位: ${{truncateShareTitle(item.title, 40)}}`,
          `${{window.location.origin}}/video/${{item.videoId}}`,
          "",
        ]),
        "#VCLIP",
      ].join("\\n");
    }}
    function buildLikesCategoryText(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      const items = getDailyCardMetrics(normalized);
      if (!items.length) return `${{label}}のいいね数データがありません。 #VCLIP`;
      items.sort((a, b) => (b.likeGrowth - a.likeGrowth) || (b.viewGrowth - a.viewGrowth));
      const best = items[0];
      const detailUrl = `${{window.location.origin}}/video/${{best.videoId}}`;
      return [
        `❤️現在(${{monthDay}})、like数が伸びている${{label}}です。`,
        "",
        `「${{truncateShareTitle(best.title, 60)}}」`,
        detailUrl,
        `24h like +${{Number(best.likeGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
      ].join("\\n");
    }}
    function buildCommentsCategoryText(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const items = getDailyCardMetrics(normalized);
      if (!items.length) return `${{label}}のコメント数データがありません。 #VCLIP`;
      items.sort((a, b) => (b.commentGrowth - a.commentGrowth) || (b.viewGrowth - a.viewGrowth));
      const best = items[0];
      const detailUrl = `${{window.location.origin}}/video/${{best.videoId}}`;
      return [
        `💬コメント数が伸びている${{label}}です。`,
        "",
        `「${{truncateShareTitle(best.title, 60)}}」`,
        detailUrl,
        `24h コメント +${{Number(best.commentGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
      ].join("\\n");
    }}
    function buildLongSellerCategoryText(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const now = new Date();
      const items = getDailyCardMetrics(normalized).filter((item) => {{
        if (!item.publishedAt) return false;
        const ageDays = (now.getTime() - item.publishedAt.getTime()) / (1000 * 60 * 60 * 24);
        return ageDays >= 7;
      }});
      if (!items.length) return `${{label}}のロングセラー候補がありません。 #VCLIP`;
      items.sort((a, b) => (b.viewGrowth - a.viewGrowth) || (b.likeGrowth - a.likeGrowth));
      const best = items[0];
      const detailUrl = `${{window.location.origin}}/video/${{best.videoId}}`;
      const ageDays = best.publishedAt ? Math.max(0, Math.floor((now.getTime() - best.publishedAt.getTime()) / (1000 * 60 * 60 * 24))) : 0;
      return [
        `🕰ロングセラー${{label}}です。`,
        `「${{truncateShareTitle(best.title, 60)}}」`,
        detailUrl,
        `公開から${{ageDays.toLocaleString("ja-JP")}}日 / 24h +${{Number(best.viewGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
      ].join("\\n");
    }}
    function ensureThreeCandidates(candidates, fallbackText) {{
      const base = Array.isArray(candidates) ? candidates.filter((c) => c && c.text) : [];
      if (!base.length) {{
        return [1, 2, 3].map((idx) => ({{
          label: `候補${{idx}}`,
          text: fallbackText || "投稿候補データがありません。 #VCLIP",
        }}));
      }}
      const out = base.slice(0, 3);
      while (out.length < 3) {{
        const src = out[out.length - 1] || out[0];
        out.push({{
          label: `候補${{out.length + 1}}`,
          text: src.text,
        }});
      }}
      return out;
    }}
    function buildOverallCategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const baseText = buildOverallDataShareText(normalized);
      const picks = getDailyTopItems(normalized, 3);
      const candidates = picks.map((item, idx) => {{
        return {{
          label: `${{idx + 1}}件目 | ${{truncateShareTitle(item.title, 28)}}`,
          text: baseText,
        }};
      }});
      return ensureThreeCandidates(candidates, baseText);
    }}
    function buildTrendingCategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const picks = getDailyTopItems(normalized, 3);
      const candidates = picks.map((item, idx) => ({{
        label: `${{idx + 1}}件目 | ${{truncateShareTitle(item.title, 28)}}`,
        text: buildTrendingTemplateText(item),
      }}));
      return ensureThreeCandidates(candidates, buildTrendingTemplateText(null));
    }}
    function buildTop3CategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      return [{{
        label: "TOP3まとめ",
        text: buildTop3CategoryText(normalized),
      }}];
    }}
    function buildLikesCategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const now = new Date();
      const monthDay = `${{now.getMonth() + 1}}/${{now.getDate()}}`;
      const items = getDailyCardMetrics(normalized);
      items.sort((a, b) => (b.likeGrowth - a.likeGrowth) || (b.viewGrowth - a.viewGrowth));
      const candidates = items.slice(0, 3).map((item, idx) => {{
        const detailUrl = `${{window.location.origin}}/video/${{item.videoId}}`;
        return {{
          label: `${{idx + 1}}件目 | ${{truncateShareTitle(item.title, 28)}}`,
          text: [
            `❤️現在(${{monthDay}})、like数が伸びている${{label}}です。`,
            "",
            `「${{truncateShareTitle(item.title, 60)}}」`,
            detailUrl,
            `24h like +${{Number(item.likeGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
          ].join("\\n"),
        }};
      }});
      return ensureThreeCandidates(candidates, buildLikesCategoryText(normalized));
    }}
    function buildCommentsCategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const items = getDailyCardMetrics(normalized);
      items.sort((a, b) => (b.commentGrowth - a.commentGrowth) || (b.viewGrowth - a.viewGrowth));
      const candidates = items.slice(0, 3).map((item, idx) => {{
        const detailUrl = `${{window.location.origin}}/video/${{item.videoId}}`;
        return {{
          label: `${{idx + 1}}件目 | ${{truncateShareTitle(item.title, 28)}}`,
          text: [
            `💬コメント数が伸びている${{label}}です。`,
            "",
            `「${{truncateShareTitle(item.title, 60)}}」`,
            detailUrl,
            `24h コメント +${{Number(item.commentGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
          ].join("\\n"),
        }};
      }});
      return ensureThreeCandidates(candidates, buildCommentsCategoryText(normalized));
    }}
    function buildLongSellerCategoryCandidates(contentType = "shorts") {{
      const normalized = (contentType || "").toLowerCase() === "video" ? "video" : "shorts";
      const label = targetLabelFromType(normalized);
      const now = new Date();
      const items = getDailyCardMetrics(normalized).filter((item) => {{
        if (!item.publishedAt) return false;
        const ageDays = (now.getTime() - item.publishedAt.getTime()) / (1000 * 60 * 60 * 24);
        return ageDays >= 7;
      }});
      items.sort((a, b) => (b.viewGrowth - a.viewGrowth) || (b.likeGrowth - a.likeGrowth));
      const candidates = items.slice(0, 3).map((item, idx) => {{
        const detailUrl = `${{window.location.origin}}/video/${{item.videoId}}`;
        const ageDays = item.publishedAt ? Math.max(0, Math.floor((now.getTime() - item.publishedAt.getTime()) / (1000 * 60 * 60 * 24))) : 0;
        return {{
          label: `${{idx + 1}}件目 | ${{truncateShareTitle(item.title, 28)}}`,
          text: [
            `🕰ロングセラー${{label}}です。`,
            `「${{truncateShareTitle(item.title, 60)}}」`,
            detailUrl,
            `公開から${{ageDays.toLocaleString("ja-JP")}}日 / 24h +${{Number(item.viewGrowth || 0).toLocaleString("ja-JP")}} #VCLIP`,
          ].join("\\n"),
        }};
      }});
      return ensureThreeCandidates(candidates, buildLongSellerCategoryText(normalized));
    }}
    function getAdminCategoryCandidates(categoryKey, contentType) {{
      if (categoryKey === "overall") return buildOverallCategoryCandidates(contentType);
      if (categoryKey === "trending") return buildTrendingCategoryCandidates(contentType);
      if (categoryKey === "top3") return buildTop3CategoryCandidates(contentType);
      if (categoryKey === "likes") return buildLikesCategoryCandidates(contentType);
      if (categoryKey === "comments") return buildCommentsCategoryCandidates(contentType);
      if (categoryKey === "longseller") return buildLongSellerCategoryCandidates(contentType);
      return ensureThreeCandidates([], "投稿候補データがありません。 #VCLIP");
    }}
    function bindAdminTargetToggle() {{
      if (!adminTargetToggle) return;
      const buttons = Array.from(adminTargetToggle.querySelectorAll(".admin-target-btn"));
      const apply = (nextType) => {{
        adminShareTargetType = nextType === "video" ? "video" : "shorts";
        buttons.forEach((btn) => {{
          btn.classList.toggle("active", btn.dataset.targetType === adminShareTargetType);
        }});
        renderAdminTrendingPicker();
      }};
      buttons.forEach((btn) => {{
        btn.addEventListener("click", () => apply(btn.dataset.targetType || "shorts"));
      }});
      apply(adminShareTargetType);
    }}
    function openShareDraft(text) {{
      const params = new URLSearchParams({{ text }});
      const shareUrl = `https://twitter.com/intent/tweet?${{params.toString()}}`;
      window.open(shareUrl, "_blank", "noopener,noreferrer");
    }}
    async function postShareViaXApi(text) {{
      const payload = {{ text: String(text || "") }};
      const headers = {{ "Content-Type": "application/json" }};
      const adminToken = new URLSearchParams(window.location.search).get("admin_token") || "";
      if (adminToken) {{
        headers["X-Admin-Token"] = adminToken;
      }}
      const response = await fetch("/api/admin/post-x", {{
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      }});
      let data = {{}};
      try {{
        data = await response.json();
      }} catch (_err) {{
        data = {{}};
      }}
      if (!response.ok || !data.ok) {{
        const err = data.error || `HTTP ${{response.status}}`;
        throw new Error(err);
      }}
      return data;
    }}
    function renderAdminTrendingPicker() {{
      const root = document.getElementById("admin-trending-picker");
      if (!root) return;
      root.innerHTML = "";
      const specs = [
        {{ key: "overall", label: "全体データ" }},
        {{ key: "trending", label: "急上昇" }},
        {{ key: "top3", label: "TOP3", singleCandidate: true }},
        {{ key: "likes", label: "いいね数" }},
        {{ key: "comments", label: "コメント数" }},
        {{ key: "longseller", label: "ロングセラー" }},
      ];
      specs.forEach((spec) => {{
        const card = document.createElement("section");
        card.className = "admin-picker-card";
        const head = document.createElement("div");
        head.className = "admin-picker-head";
        head.textContent = `${{spec.label}}（3候補）`;
        card.appendChild(head);
        const select = document.createElement("select");
        select.className = "admin-picker-select";
        const preview = document.createElement("pre");
        preview.className = "admin-picker-preview";
        const empty = document.createElement("p");
        empty.className = "admin-picker-empty";
        empty.textContent = "投稿候補データがありません。";
        const actions = document.createElement("div");
        actions.className = "admin-picker-actions";
        const copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "admin-picker-btn";
        copyBtn.textContent = "コピー";
        const openBtn = document.createElement("button");
        openBtn.type = "button";
        openBtn.className = "admin-picker-btn";
        openBtn.textContent = "Xで開く";
        const postBtn = document.createElement("button");
        postBtn.type = "button";
        postBtn.className = "admin-picker-btn";
        postBtn.textContent = "X API投稿";
        let items = [];
        function refillCandidates() {{
          items = getAdminCategoryCandidates(spec.key, adminShareTargetType);
          select.innerHTML = "";
          items.forEach((item, idx) => {{
            const option = document.createElement("option");
            option.value = String(idx);
            option.textContent = item.label || `候補${{idx + 1}}`;
            select.appendChild(option);
          }});
          const hasItems = items.length > 0;
          const showSelector = !spec.singleCandidate && hasItems;
          select.style.display = showSelector ? "" : "none";
          empty.style.display = hasItems ? "none" : "";
          copyBtn.disabled = !hasItems;
          openBtn.disabled = !hasItems;
          postBtn.disabled = !hasItems;
          if (hasItems) select.value = "0";
        }}
        function getSelectedItem() {{
          const idx = Number(select.value || 0);
          return items[Math.max(0, Math.min(items.length - 1, idx))];
        }}
        function updatePreview() {{
          const selected = getSelectedItem();
          preview.textContent = selected && selected.text ? selected.text : "投稿候補データがありません。";
        }}
        select.addEventListener("change", updatePreview);
        copyBtn.addEventListener("click", async () => {{
          try {{
            await copyTextToClipboard((getSelectedItem() || {{}}).text || "");
            window.alert("投稿文をコピーしました。");
          }} catch (error) {{
            window.alert("コピーに失敗しました。ブラウザの権限設定をご確認ください。");
          }}
        }});
        openBtn.addEventListener("click", () => {{
          const selected = getSelectedItem();
          openShareDraft(selected && selected.text ? selected.text : "");
        }});
        postBtn.addEventListener("click", async () => {{
          const selected = getSelectedItem();
          const text = selected && selected.text ? selected.text : "";
          if (!text) {{
            window.alert("投稿文が空です。");
            return;
          }}
          if (!window.confirm("この文面をX APIで投稿しますか？")) return;
          postBtn.disabled = true;
          try {{
            const result = await postShareViaXApi(text);
            const tweetId = ((result || {{}}).tweet_id || "").trim();
            if (tweetId) {{
              window.alert(`投稿しました。tweet_id: ${{tweetId}}`);
            }} else {{
              window.alert("投稿しました。");
            }}
          }} catch (error) {{
            window.alert(`X API投稿に失敗しました: ${{error && error.message ? error.message : "unknown_error"}}`);
          }} finally {{
            postBtn.disabled = false;
          }}
        }});
        refillCandidates();
        updatePreview();
        actions.appendChild(copyBtn);
        actions.appendChild(openBtn);
        actions.appendChild(postBtn);
        card.appendChild(select);
        card.appendChild(empty);
        card.appendChild(preview);
        card.appendChild(actions);
        root.appendChild(card);
      }});
    }}
    async function copyTextToClipboard(text) {{
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {{
        await navigator.clipboard.writeText(text);
        return;
      }}
      const textArea = document.createElement("textarea");
      textArea.value = text;
      textArea.setAttribute("readonly", "readonly");
      textArea.style.position = "fixed";
      textArea.style.opacity = "0";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      const succeeded = document.execCommand("copy");
      textArea.remove();
      if (!succeeded) {{
        throw new Error("copy_failed");
      }}
    }}
    function jumpToVideoCard(videoId, contentType = "shorts", period = "daily") {{
      if (!videoId) return;

      if (activePeriod !== period || activeContentType !== contentType) {{
        activePeriod = period;
        activeContentType = contentType;
        rankingIcon.textContent = typeConfig[contentType]?.icon || typeConfig.shorts.icon;
        rankingLabel.textContent = typeConfig[contentType]?.label || typeConfig.shorts.label;
        render();
      }}

      const selector = `.period-panel[data-period="${{period}}"] .content-panel[data-content-panel="${{contentType}}"] .card[data-video-id="${{videoId}}"]`;
      const target = periodRoot.querySelector(selector);
      if (!target) return;

      const cards = getCurrentCards();
      const targetIndex = cards.indexOf(target);
      if (targetIndex >= 0) {{
        const isMobile = window.innerWidth <= MOBILE_BREAKPOINT;
        const ranges = isMobile ? buildMobilePageRanges(cards.length) : buildDesktopPageRanges(cards.length);
        let page = 1;
        for (let p = 0; p < ranges.length; p++) {{
          const [start, end] = ranges[p];
          if (targetIndex >= start && targetIndex < end) {{
            page = p + 1;
            break;
          }}
        }}
        pageState[paginationKey()] = page;
        applyPagination();
      }}

      target.scrollIntoView({{ behavior: "smooth", block: "center" }});
      target.classList.add("card-focus");
      setTimeout(() => target.classList.remove("card-focus"), 1200);
    }}

    /* ── Build NEW picks from payload ── */
    function getNewPickPool() {{
      if (Array.isArray(cachedNewPickPool)) return cachedNewPickPool;
      if (!payload.length) return [];
      const daily = payload.find(p => p.table === "daily");
      if (!daily || !daily.groups || !daily.groups["all"]) return [];
      const tmpDiv = document.createElement("div");
      tmpDiv.innerHTML = daily.groups["all"];
      const allCards = Array.from(tmpDiv.querySelectorAll(".card"));
      const newCards = allCards.filter((card) => card.querySelector(".new-badge"));
      const nowMs = Date.now();
      const fallbackWindowMs = 48 * 60 * 60 * 1000;
      const fallbackCards = newCards.length ? [] : allCards
        .filter((card) => {{
          const iso = String(card.dataset.publishedAt || "").trim();
          if (!iso) return false;
          const publishedAt = new Date(iso);
          if (Number.isNaN(publishedAt.getTime())) return false;
          const diff = nowMs - publishedAt.getTime();
          return diff >= 0 && diff <= fallbackWindowMs;
        }})
        .sort((a, b) => {{
          const at = new Date(String(a.dataset.publishedAt || "")).getTime();
          const bt = new Date(String(b.dataset.publishedAt || "")).getTime();
          return bt - at;
        }});
      const cards = newCards.length ? newCards : fallbackCards;
      const badgeLabel = newCards.length ? "NEW" : "";

      const dedup = new Map();
      cards.forEach((card) => {{
        const thumbEl = card.querySelector(".thumb");
        const imgEl = thumbEl ? thumbEl.querySelector("img") : null;
        const rankEl = card.querySelector(".rank-badge");
        const titleEl = card.querySelector(".card-title");
        if (!thumbEl) return;
        const videoId = thumbEl.dataset.videoId || "";
        if (!videoId || dedup.has(videoId)) return;
        dedup.set(videoId, {{
          videoId,
          contentType: (thumbEl.dataset.contentType || "shorts").toLowerCase(),
          thumbSrc: imgEl ? (imgEl.getAttribute("src") || "") : "",
          thumbAlt: imgEl ? (imgEl.getAttribute("alt") || "") : "",
          rank: rankEl ? (rankEl.textContent || "").trim() : "",
          title: titleEl ? (titleEl.textContent || "").trim() : "",
          channelName: (card.querySelector(".channel-name")?.textContent || "").trim(),
          groupName: (card.querySelector(".pill")?.textContent || "").trim(),
          channelIcon: card.querySelector(".channel-icon")?.getAttribute("src") || "",
          pickupBadge: badgeLabel,
        }});
      }});

      const pool = Array.from(dedup.values());
      for (let i = pool.length - 1; i > 0; i--) {{
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }}
      cachedNewPickPool = pool;
      return pool;
    }}
    function truncatePickupTitle(text, maxChars = 34) {{
      const s = (text || "").trim();
      if (s.length <= maxChars) return s;
      return s.slice(0, maxChars) + "...";
    }}
    function buildNewPicks() {{
      const listEl = document.getElementById("new-list");
      if (!listEl) return;
      const maxPickCount = window.innerWidth <= MOBILE_BREAKPOINT ? 1 : 4;
      const pool = getNewPickPool();
      const picks = pool.slice(0, maxPickCount);
      if (!picks.length) {{
        listEl.innerHTML = '<div class="empty">新着動画はまだありません</div>';
        return;
      }}
      listEl.innerHTML = "";
      picks.forEach((pick) => {{
        const link = document.createElement("a");
        link.className = "pickup-card";
        link.href = "#ranking-section";
        link.dataset.videoId = pick.videoId;
        link.dataset.contentType = pick.contentType === "video" ? "video" : "shorts";
        link.dataset.period = "daily";
        const rankLabel = pick.rank || "-";
        const thumbSrc = pick.thumbSrc || "";
        const thumbAlt = pick.title || pick.thumbAlt || "";
        const thumbWrap = document.createElement("div");
        thumbWrap.className = "pickup-thumb";
        const img = document.createElement("img");
        img.src = thumbSrc;
        img.alt = thumbAlt;
        img.loading = "lazy";
        const rank = document.createElement("span");
        rank.className = "rank-badge";
        rank.textContent = rankLabel;
        const badgeText = String(pick.pickupBadge || "").trim();
        if (badgeText) {{
          const badge = document.createElement("span");
          badge.className = "new-badge";
          badge.textContent = badgeText;
          thumbWrap.appendChild(badge);
        }}
        thumbWrap.appendChild(img);
        thumbWrap.appendChild(rank);

        const body = document.createElement("div");
        body.className = "pickup-body";
        const title = document.createElement("p");
        title.className = "pickup-title";
        title.textContent = truncatePickupTitle(pick.title || pick.thumbAlt || "", 34);
        body.appendChild(title);

        link.appendChild(thumbWrap);
        link.appendChild(body);
        listEl.appendChild(link);
      }});
    }}
    const periodMap = new Map(payload.map((p) => [p.table, p]));
    const builtPeriodPanels = new Set();
    function ensureTypeTabs() {{
      if (typeTabs.dataset.ready === "1") {{
        typeTabs.querySelectorAll(".tab-btn").forEach((btn) => {{
          btn.classList.toggle("active", btn.dataset.type === activeContentType);
        }});
        return;
      }}
      typeTabs.innerHTML = "";
      ["shorts", "video"].forEach((type) => {{
        const btn = document.createElement("button");
        btn.className = "tab-btn" + (type === activeContentType ? " active" : "");
        btn.textContent = type === "shorts" ? "Shorts" : "\u52d5\u753b";
        btn.type = "button";
        btn.dataset.type = type;
        btn.addEventListener("click", () => {{
          activeContentType = type;
          render();
        }});
        typeTabs.appendChild(btn);
      }});
      typeTabs.dataset.ready = "1";
    }}
    function ensurePeriodTabs() {{
      if (periodTabs.dataset.ready === "1") {{
        periodTabs.querySelectorAll(".tab-btn").forEach((btn) => {{
          btn.classList.toggle("active", btn.dataset.period === activePeriod);
        }});
        return;
      }}
      periodTabs.innerHTML = "";
      payload.forEach((period) => {{
        const btn = document.createElement("button");
        btn.className = "tab-btn" + (period.table === activePeriod ? " active" : "");
        btn.textContent = period.label;
        btn.type = "button";
        btn.dataset.period = period.table;
        btn.addEventListener("click", () => {{
          activePeriod = period.table;
          render();
        }});
        periodTabs.appendChild(btn);
      }});
      periodTabs.dataset.ready = "1";
    }}
    function ensurePeriodPanel(periodTable) {{
      if (builtPeriodPanels.has(periodTable)) return;
      const period = periodMap.get(periodTable);
      if (!period) return;
      const panel = document.createElement("section");
      panel.className = "period-panel";
      panel.dataset.period = period.table;

      const groupsForRender = showAdminMeta ? period.available_groups : ["all"];
      const defaultGroup = groupsForRender[0];

      let groupTabsHtml = "";
      if (showAdminMeta && groupsForRender.length > 1) {{
        groupTabsHtml = '<div class="tab-group" style="margin-bottom:12px;" id="group-tabs-' + period.table + '"></div>';
      }}

      panel.innerHTML = groupTabsHtml + '<div class="group-root"></div>';
      const groupRoot = panel.querySelector(".group-root");

      groupsForRender.forEach((groupName) => {{
        const groupPanel = document.createElement("div");
        groupPanel.className = "group-panel" + (groupName === defaultGroup ? " active" : "");
        groupPanel.dataset.group = groupName;
        groupPanel.innerHTML = period.groups[groupName];
        groupRoot.appendChild(groupPanel);
      }});

      if (showAdminMeta && groupsForRender.length > 1) {{
        const gTabs = panel.querySelector("#group-tabs-" + period.table);
        if (gTabs) {{
          groupsForRender.forEach((groupName) => {{
            const gBtn = document.createElement("button");
            gBtn.className = "tab-btn" + (groupName === defaultGroup ? " active" : "");
            gBtn.textContent = groupLabels[groupName] || groupName;
            gBtn.type = "button";
            gBtn.addEventListener("click", () => {{
              gTabs.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
              groupRoot.querySelectorAll(".group-panel").forEach((p) => p.classList.remove("active"));
              gBtn.classList.add("active");
              groupRoot.querySelector(`[data-group="${{groupName}}"]`).classList.add("active");
              applyPagination();
            }});
            gTabs.appendChild(gBtn);
          }});
        }}
      }}

      periodRoot.appendChild(panel);
      builtPeriodPanels.add(periodTable);
    }}
    /* ── Main render ── */
    function render() {{
      if (!periodMap.has(activePeriod) && payload.length) {{
        activePeriod = payload[0].table;
      }}
      ensureTypeTabs();
      ensurePeriodTabs();
      ensurePeriodPanel(activePeriod);

      rankingIcon.textContent = typeConfig[activeContentType]?.icon || typeConfig.shorts.icon;
      rankingLabel.textContent = typeConfig[activeContentType]?.label || typeConfig.shorts.label;

      periodRoot.querySelectorAll(".period-panel").forEach((panel) => {{
        panel.classList.toggle("active", panel.dataset.period === activePeriod);
      }});
      periodRoot.querySelectorAll(".content-panel").forEach((panel) => {{
        panel.classList.toggle("active", panel.dataset.contentPanel === activeContentType);
      }});
      applyPagination();
    }}

    const newListRoot = document.getElementById("new-list");
    if (newListRoot) {{
      newListRoot.addEventListener("click", (event) => {{
        const trigger = event.target.closest(".pickup-card");
        if (!trigger || !trigger.dataset.videoId) return;
        event.preventDefault();
        jumpToVideoCard(
          trigger.dataset.videoId,
          trigger.dataset.contentType || "shorts",
          trigger.dataset.period || "daily",
        );
      }});
    }}
    if (backToTop) {{
      backToTop.addEventListener("click", (event) => {{
        event.preventDefault();
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }});
    }}
    window.addEventListener("resize", () => {{
      applyPagination();
      const nextMode = window.innerWidth <= MOBILE_BREAKPOINT ? "mobile" : "desktop";
      if (nextMode !== newPickLayoutMode) {{
        newPickLayoutMode = nextMode;
        buildNewPicks();
      }}
    }});

    buildHeroStats();
    buildNewPicks();
    render();
    if (showAdminMeta) {{
      bindAdminTargetToggle();
      renderAdminTrendingPicker();
    }}
  </script>
</body>
</html>
"""


def _normalize_video_id(raw: str) -> str:
    if not raw:
        return ""
    filtered = "".join(ch for ch in str(raw).strip() if ch.isalnum() or ch in {"-", "_"})
    if len(filtered) < 6:
        return ""
    return filtered


def _ranking_table_for_content(content_type: str, period_key: str = "daily") -> str:
    normalized_content = (content_type or "").strip().lower()
    normalized_period = _normalize_period_key(period_key)
    if normalized_period == "weekly":
        return "weekly_ranking_shorts" if normalized_content == "shorts" else "weekly_ranking_video"
    if normalized_period == "monthly":
        return "monthly_ranking_shorts" if normalized_content == "shorts" else "monthly_ranking_video"
    return "daily_ranking_shorts" if normalized_content == "shorts" else "daily_ranking_video"


def _ranking_history_table_for_content(content_type: str, period_key: str = "daily") -> str:
    return f"{_ranking_table_for_content(content_type, period_key)}_history"


def _to_jst_date(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).strftime("%Y-%m-%d")


def _to_jst_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).isoformat()


def _fetch_video_detail_payload(video_id: str, period_key: str = "daily") -> dict | None:
    video_rows = fetchall(
        """
        SELECT
            video_id,
            title,
            channel_id,
            channel_name,
            channel_icon_url,
            content_type,
            published_at
        FROM videos
        WHERE video_id = %s
        LIMIT 1
        """,
        (video_id,),
    )
    if not video_rows:
        return None

    video = dict(video_rows[0])
    normalized_period = _normalize_period_key(period_key)
    ranking_table = _ranking_table_for_content(video.get("content_type") or "", normalized_period)
    history_table = _ranking_history_table_for_content(video.get("content_type") or "", normalized_period)

    try:
        first_ranked_rows = fetchall(
            f"""
            SELECT calculated_at
            FROM {history_table}
            WHERE video_id = %s
            ORDER BY calculated_at ASC
            LIMIT 1
            """,
            (video_id,),
        )
        best_rank_rows = fetchall(
            f"""
            SELECT rank, calculated_at
            FROM {history_table}
            WHERE video_id = %s
            ORDER BY rank ASC, calculated_at ASC
            LIMIT 1
            """,
            (video_id,),
        )
    except Exception:
        logger.exception("Failed to read ranking history from %s, fallback to latest table", history_table)
        first_ranked_rows = fetchall(
            f"""
            SELECT calculated_at
            FROM {ranking_table}
            WHERE video_id = %s
            ORDER BY calculated_at ASC
            LIMIT 1
            """,
            (video_id,),
        )
        best_rank_rows = fetchall(
            f"""
            SELECT rank, calculated_at
            FROM {ranking_table}
            WHERE video_id = %s
            ORDER BY rank ASC, calculated_at ASC
            LIMIT 1
            """,
            (video_id,),
        )
    current_rank_table = _ranking_table_for_content(video.get("content_type") or "", normalized_period)
    current_rank_rows = fetchall(
        f"""
        SELECT rank, calculated_at
        FROM {current_rank_table}
        WHERE video_id = %s
        ORDER BY calculated_at DESC
        LIMIT 1
        """,
        (video_id,),
    )

    delta_rows = fetchall(
        """
        WITH latest AS (
            SELECT view_count, like_count, comment_count
            FROM video_stats
            WHERE video_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        ),
        old AS (
            SELECT view_count, like_count, comment_count
            FROM video_stats
            WHERE video_id = %s
              AND timestamp <= (NOW() - INTERVAL '24 hours')
            ORDER BY timestamp DESC
            LIMIT 1
        ),
        first_stat AS (
            SELECT view_count, like_count, comment_count
            FROM video_stats
            WHERE video_id = %s
            ORDER BY timestamp ASC
            LIMIT 1
        )
        SELECT
            COALESCE((SELECT view_count FROM latest), 0) AS latest_view,
            COALESCE((SELECT view_count FROM old), (SELECT view_count FROM first_stat), 0) AS base_view,
            COALESCE((SELECT like_count FROM latest), 0) AS latest_like,
            COALESCE((SELECT like_count FROM old), (SELECT like_count FROM first_stat), 0) AS base_like,
            COALESCE((SELECT comment_count FROM latest), 0) AS latest_comment,
            COALESCE((SELECT comment_count FROM old), (SELECT comment_count FROM first_stat), 0) AS base_comment
        """,
        (video_id, video_id, video_id),
    )
    latest_view = int((delta_rows[0].get("latest_view") if delta_rows else 0) or 0)
    base_view = int((delta_rows[0].get("base_view") if delta_rows else 0) or 0)
    latest_like = int((delta_rows[0].get("latest_like") if delta_rows else 0) or 0)
    base_like = int((delta_rows[0].get("base_like") if delta_rows else 0) or 0)
    latest_comment = int((delta_rows[0].get("latest_comment") if delta_rows else 0) or 0)
    base_comment = int((delta_rows[0].get("base_comment") if delta_rows else 0) or 0)
    views_delta_24h = max(0, latest_view - base_view)
    likes_delta_24h = max(0, latest_like - base_like)
    comments_delta_24h = max(0, latest_comment - base_comment)

    trend_rows = fetchall(
        """
        SELECT
            DATE_TRUNC('day', timestamp) AS day_ts,
            MAX(view_count) AS views,
            MAX(like_count) AS likes
        FROM video_stats
        WHERE video_id = %s
          AND timestamp >= (NOW() - INTERVAL '30 days')
        GROUP BY DATE_TRUNC('day', timestamp)
        ORDER BY day_ts
        """,
        (video_id,),
    )
    trend_30 = [int(row.get("views") or 0) for row in trend_rows]
    like_trend_30 = [int(row.get("likes") or 0) for row in trend_rows]
    trend_30_dates = []
    for row in trend_rows:
        day_ts = row.get("day_ts")
        if isinstance(day_ts, datetime):
            if day_ts.tzinfo is None:
                day_ts = day_ts.replace(tzinfo=timezone.utc)
            trend_30_dates.append(day_ts.astimezone(JST).strftime("%m/%d"))
        else:
            trend_30_dates.append("-")
    if not trend_30 and latest_view > 0:
        trend_30 = [latest_view]
        trend_30_dates = [datetime.now(JST).strftime("%m/%d")]
    trend_7 = trend_30[-7:] if len(trend_30) > 7 else trend_30
    like_trend_7 = like_trend_30[-7:] if len(like_trend_30) > 7 else like_trend_30
    trend_7_dates = trend_30_dates[-7:] if len(trend_30_dates) > 7 else trend_30_dates
    like_has_data = any(value > 0 for value in like_trend_30)

    try:
        related_rows = fetchall(
            f"""
            WITH related AS (
                SELECT
                    r.video_id,
                    MIN(r.rank) AS best_rank,
                    MIN(r.calculated_at) AS first_ranked_at
                FROM {history_table} r
                JOIN videos v ON v.video_id = r.video_id
                WHERE v.channel_id = %s
                  AND r.video_id <> %s
                GROUP BY r.video_id
            )
            SELECT
                rel.video_id,
                rel.best_rank,
                rel.first_ranked_at,
                v.title
            FROM related rel
            JOIN videos v ON v.video_id = rel.video_id
            WHERE rel.best_rank <= 100
            ORDER BY rel.best_rank ASC, rel.first_ranked_at DESC
            LIMIT 50
            """,
            (video.get("channel_id"), video_id),
        )
    except Exception:
        logger.exception("Failed to read related history from %s, fallback to latest table", history_table)
        related_rows = fetchall(
            f"""
            WITH related AS (
                SELECT
                    r.video_id,
                    MIN(r.rank) AS best_rank,
                    MIN(r.calculated_at) AS first_ranked_at
                FROM {ranking_table} r
                JOIN videos v ON v.video_id = r.video_id
                WHERE v.channel_id = %s
                  AND r.video_id <> %s
                GROUP BY r.video_id
            )
            SELECT
                rel.video_id,
                rel.best_rank,
                rel.first_ranked_at,
                v.title
            FROM related rel
            JOIN videos v ON v.video_id = rel.video_id
            WHERE rel.best_rank <= 100
            ORDER BY rel.best_rank ASC, rel.first_ranked_at DESC
            LIMIT 50
            """,
            (video.get("channel_id"), video_id),
        )
    if len(related_rows) > 3:
        related_rows = random.sample(related_rows, 3)

    top3_rows = fetchall(
        f"""
        WITH latest AS (
            SELECT MAX(calculated_at) AS calculated_at
            FROM {ranking_table}
        )
        SELECT
            r.rank,
            r.video_id,
            r.calculated_at,
            v.title,
            v.channel_name
        FROM {ranking_table} r
        JOIN latest l ON l.calculated_at = r.calculated_at
        JOIN videos v ON v.video_id = r.video_id
        ORDER BY r.rank ASC
        LIMIT 3
        """
    )

    first_ranked_at = first_ranked_rows[0].get("calculated_at") if first_ranked_rows else None
    best_rank = best_rank_rows[0].get("rank") if best_rank_rows else None
    best_rank_at = best_rank_rows[0].get("calculated_at") if best_rank_rows else None
    current_rank = current_rank_rows[0].get("rank") if current_rank_rows else None
    current_rank_at = current_rank_rows[0].get("calculated_at") if current_rank_rows else None

    return {
        "video_id": video_id,
        "period_key": normalized_period,
        "title": _sanitize_text(video.get("title") or ""),
        "channel_name": _sanitize_text(video.get("channel_name") or ""),
        "channel_icon_url": _sanitize_text(video.get("channel_icon_url") or ""),
        "published_at": _to_jst_date(video.get("published_at")),
        "published_at_iso": _to_jst_iso(video.get("published_at")),
        "content_type": _sanitize_text(video.get("content_type") or ""),
        "first_ranked_at": _to_jst_date(first_ranked_at) if first_ranked_at else "-",
        "best_rank": int(best_rank) if best_rank is not None else None,
        "best_rank_at": _to_jst_date(best_rank_at) if best_rank_at else "-",
        "current_rank": int(current_rank) if current_rank is not None else None,
        "current_rank_at": _to_jst_date(current_rank_at) if current_rank_at else "-",
        "views_delta_24h": int(views_delta_24h),
        "likes_delta_24h": int(likes_delta_24h),
        "comments_delta_24h": int(comments_delta_24h),
        "latest_view_count": int(latest_view),
        "latest_like_count": int(latest_like),
        "latest_comment_count": int(latest_comment),
        "trend_7": trend_7,
        "trend_7_dates": trend_7_dates,
        "trend_30": trend_30,
        "trend_30_dates": trend_30_dates,
        "like_trend_7": like_trend_7,
        "like_trend_30": like_trend_30,
        "like_has_data": like_has_data,
        "top3_cards": [
            {
                "rank": int(row.get("rank") or 0),
                "video_id": _sanitize_text(row.get("video_id") or ""),
                "title": _sanitize_text(row.get("title") or ""),
                "channel_name": _sanitize_text(row.get("channel_name") or ""),
                "calculated_at": _to_jst_date(row.get("calculated_at")),
            }
            for row in top3_rows
        ],
        "related": [
            {
                "video_id": _sanitize_text(row.get("video_id") or ""),
                "title": _sanitize_text(row.get("title") or ""),
                "best_rank": int(row.get("best_rank") or 0),
                "first_ranked_at": _to_jst_date(row.get("first_ranked_at")),
            }
            for row in related_rows
        ],
    }


def render_video_detail_page(video_id: str, base_url: str = "", period_key: str = "daily") -> tuple[int, str]:
    normalized_period = _normalize_period_key(period_key)
    payload = _fetch_video_detail_payload(video_id, period_key=normalized_period)
    if payload is None:
        escaped_id = html.escape(video_id)
        return (
            404,
            f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>動画が見つかりません | VCLIP</title>
</head>
<body style="background:#0b101a;color:#e8edf4;font-family:sans-serif;padding:24px;">
  <h1>動画が見つかりません</h1>
  <p>video_id: {escaped_id}</p>
  <p><a href="https://vclipranking.com/" style="color:#8ad7ff;">ランキングに戻る</a></p>
</body>
</html>""",
        )

    title_escaped = html.escape(payload["title"])
    channel_escaped = html.escape(payload["channel_name"])
    channel_icon_escaped = html.escape(payload.get("channel_icon_url") or "")
    published_escaped = html.escape(payload["published_at"])
    detail_title = f"{payload['title']} | VCLIP"
    normalized_base_url = _normalize_base_url(base_url)
    detail_path = f"/video/{payload['video_id']}"
    if normalized_period != "daily":
        detail_path += f"?period={normalized_period}"
    canonical_url = f"{normalized_base_url}{detail_path}" if normalized_base_url else detail_path
    thumbnail_url = _thumbnail_url(payload["video_id"])
    video_id_escaped = html.escape(payload["video_id"])
    yt_url = f"https://www.youtube.com/watch?v={video_id_escaped}"
    detail_description = (
        f"{payload['title']} の詳細ページ。初回ランクイン日・最高順位・現在順位・再生推移を確認できます。"
    )
    video_structured_data = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": payload["title"],
        "description": detail_description,
        "thumbnailUrl": [thumbnail_url],
        "uploadDate": payload.get("published_at_iso") or payload.get("published_at") or "",
        "contentUrl": yt_url,
        "embedUrl": f"https://www.youtube-nocookie.com/embed/{payload['video_id']}",
        "url": canonical_url,
        "publisher": {
            "@type": "Organization",
            "name": payload.get("channel_name") or SITE_TITLE,
        },
    }
    head_meta = (
        f'<meta name="description" content="{html.escape(detail_description, quote=True)}">\n'
        f'  <link rel="icon" type="image/x-icon" href="/assets/favicon.ico">\n'
        f'  <link rel="canonical" href="{html.escape(canonical_url, quote=True)}">\n'
        f'  <meta property="og:type" content="website">\n'
        f'  <meta property="og:site_name" content="{html.escape(SITE_TITLE, quote=True)}">\n'
        f'  <meta property="og:title" content="{html.escape(detail_title, quote=True)}">\n'
        f'  <meta property="og:description" content="{html.escape(detail_description, quote=True)}">\n'
        f'  <meta property="og:url" content="{html.escape(canonical_url, quote=True)}">\n'
        f'  <meta property="og:image" content="{html.escape(thumbnail_url, quote=True)}">\n'
        f'  <meta property="og:image:alt" content="{html.escape(payload["title"], quote=True)}">\n'
        f'  <meta name="twitter:card" content="summary_large_image">\n'
        f'  <meta name="twitter:title" content="{html.escape(detail_title, quote=True)}">\n'
        f'  <meta name="twitter:description" content="{html.escape(detail_description, quote=True)}">\n'
        f'  <meta name="twitter:image" content="{html.escape(thumbnail_url, quote=True)}">\n'
        f'  <script type="application/ld+json">{json.dumps(video_structured_data, ensure_ascii=False)}</script>'
    )

    best_rank_label = _rank_label_for_detail(payload.get("best_rank"))
    current_rank_label = _rank_label_for_detail(payload.get("current_rank"))
    best_rank_at_label = payload.get("best_rank_at") if best_rank_label != "Not ranked" else "-"
    current_rank_at_label = payload.get("current_rank_at") if current_rank_label != "Not ranked" else "-"
    latest_view_count = int(payload.get("latest_view_count") or 0)
    latest_like_count = int(payload.get("latest_like_count") or 0)
    like_rate_label = "-" if latest_view_count <= 0 else f"{(latest_like_count / latest_view_count) * 100:.2f}%"
    period_category_label = {
        "daily": "24時間",
        "weekly": "7日間",
        "monthly": "30日間",
    }.get(normalized_period, "24時間")
    if isinstance(payload.get("current_rank"), int):
        rank_chip_value = str(int(payload["current_rank"]))
    else:
        rank_chip_value = "-"
    rank_chip_class = (
        "gold" if rank_chip_value == "1"
        else ("silver" if rank_chip_value == "2" else ("bronze" if rank_chip_value == "3" else ""))
    )
    content_type = (payload.get("content_type") or "").strip().lower()
    top3_heading = "本日のShortsランキング TOP3" if content_type == "shorts" else "本日の動画ランキング TOP3"
    detail_query_suffix = "" if normalized_period == "daily" else f"?period={normalized_period}"

    top3_html_parts: list[str] = []
    for item in payload.get("top3_cards", []):
        rank_value = int(item.get("rank") or 0)
        rank_label = "1" if rank_value == 1 else ("2" if rank_value == 2 else ("3" if rank_value == 3 else str(max(0, rank_value))))
        rank_class = "gold" if rank_value == 1 else ("silver" if rank_value == 2 else ("bronze" if rank_value == 3 else ""))
        top3_id = html.escape(item.get("video_id") or "")
        top3_title = html.escape(item.get("title") or "")
        top3_html_parts.append(
            f"""
            <a class="hero-top3-card" href="/video/{top3_id}{detail_query_suffix}">
              <div class="hero-top3-thumb-wrap">
                <img class="hero-top3-thumb" src="{_thumbnail_url(top3_id)}" alt="{top3_title}" loading="lazy">
                <span class="hero-top3-rank {rank_class}">{rank_label}</span>
              </div>
              <div class="hero-top3-body">
                <p class="hero-top3-title">{top3_title}</p>
              </div>
            </a>
            """
        )
    top3_html = "".join(top3_html_parts) or '<p class="empty-note">本日のランキングデータがまだありません。</p>'

    related_html_parts: list[str] = []
    for item in payload["related"]:
        rid = html.escape(item["video_id"])
        rtitle = html.escape(item["title"])
        rbest = _rank_label_for_detail(int(item.get("best_rank") or 0))
        rfirst = html.escape(item.get("first_ranked_at") or "-")
        related_html_parts.append(
            f"""
            <a class="related-item" href="/video/{rid}{detail_query_suffix}">
              <img src="{_thumbnail_url(rid)}" alt="{rtitle}" loading="lazy">
              <div class="related-body">
                <div class="related-meta"><span>Best: {rbest}</span><span>{rfirst}</span></div>
                <div class="related-title">{rtitle}</div>
              </div>
            </a>
            """
        )
    related_html = "".join(related_html_parts) or '<p class="empty-note">該当する過去ランクイン動画はありません。</p>'

    payload_json = json.dumps(
        {
            "trend_7": payload["trend_7"],
            "trend_7_dates": payload["trend_7_dates"],
            "trend_30": payload["trend_30"],
            "trend_30_dates": payload["trend_30_dates"],
            "like_trend_7": payload["like_trend_7"],
            "like_trend_30": payload["like_trend_30"],
            "like_has_data": payload["like_has_data"],
        },
        ensure_ascii=False,
    )
    detail_share_text = f"{payload['title']}\n{canonical_url}\n#VCLIP"
    detail_share_url = "https://twitter.com/intent/tweet?text=" + quote(detail_share_text, safe="")
    player_aspect_class = "landscape" if content_type == "video" else "portrait"
    player_open_label = "動画プレーヤーを開く" if content_type == "video" else "Shortsプレーヤーを開く"
    thumbnail_escaped = html.escape(thumbnail_url, quote=True)

    body = f"""
<!doctype html>
<html lang="ja" id="top">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(detail_title)}</title>
  {head_meta}
  <style>
    :root {{
      --bg: #ffffff;
      --header-bg: rgba(11, 17, 28, 0.92);
      --header-border: rgba(99, 177, 230, 0.22);
      --text-main: #111827;
      --text-sub: #475569;
      --text-soft: #64748b;
      --panel: #ffffff;
      --panel-border: #d3dde8;
      --accent-a: #63d0ff;
      --accent-b: #60a5fa;
      --gradient-start: #63d0ff;
      --gradient-end: #a78bfa;
      --accent-c: #1f3b6f;
      --ok: #10b981;
      --growth-green: #10b981;
      --like: #db2777;
      --new: #f472b6;
      --border: rgba(100, 160, 240, 0.18);
      --text-primary: #e8edf4;
      --text-secondary: rgba(232, 237, 244, 0.90);
      --shadow-sm: 0 8px 18px rgba(15, 23, 42, 0.10);
      --shadow-md: 0 14px 30px rgba(15, 23, 42, 0.16);
      --radius-md: 12px;
      --radius-lg: 16px;
      --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans JP Local","Hiragino Kaku Gothic ProN",sans-serif;
      color: var(--text-main);
      background:
        radial-gradient(900px 420px at 10% -10%, rgba(99, 208, 255, 0.08), transparent 60%),
        radial-gradient(760px 380px at 92% -12%, rgba(96, 165, 250, 0.06), transparent 58%),
        var(--bg);
      line-height: 1.6;
    }}
    body.player-open {{ overflow:hidden; }}
    .header {{
      position: sticky; top: 0; z-index: 100;
      background: rgba(11, 17, 28, 0.92);
      backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      border-top: 2px solid rgba(99, 208, 255, 0.34);
      box-shadow: 0 8px 20px rgba(0,0,0,0.25);
    }}
    .header-inner {{
      height: 60px; padding: 0 24px; display:flex; align-items:center; justify-content:space-between;
    }}
    .brand {{ display:flex; align-items:center; gap:12px; text-decoration:none; color:var(--text-primary); }}
    .brand-logo {{
      display:inline-flex; align-items:center; justify-content:center; width:44px; height:30px;
      border-radius:8px; background:linear-gradient(135deg,var(--gradient-start),var(--gradient-end));
      color:#fff; font-weight:900; font-size:.72rem; letter-spacing:.04em;
    }}
    .brand-text {{ font-weight:800; font-size:1.05rem; }}
    .brand-accent {{
      background: linear-gradient(135deg,var(--gradient-start),var(--gradient-end));
      -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
    }}
    .header-meta {{ color: var(--text-secondary); font-size: .82rem; }}
    .live-dot {{ display:inline-flex; align-items:center; gap:6px; color:var(--growth-green); font-weight:600; }}
    .live-dot::before {{
      content:''; width:7px; height:7px; border-radius:50%; background:var(--growth-green); animation:pulse-dot 2s ease-in-out infinite;
    }}
    @keyframes pulse-dot {{ 0%,100%{{opacity:1;transform:scale(1);}} 50%{{opacity:.5;transform:scale(.8);}} }}
    .main {{ max-width:1180px; margin:0 auto; padding:22px 24px 48px; display:grid; gap:16px; }}
    .hero-note {{ margin:0; text-align:center; color:#1f3b6f; font-size:.9rem; font-weight:600; }}
    .panel {{ background:var(--panel); border:1px solid var(--panel-border); border-radius:var(--radius-md); box-shadow:var(--shadow-sm); padding:14px; }}
    .top3-section {{ padding:12px; }}
    .top3-head {{ margin:0 0 10px; display:flex; align-items:center; gap:8px; font-size:1.05rem; font-weight:800; color:#0f294f; }}
    .top3-icon {{ width:24px; height:24px; border-radius:7px; display:inline-flex; align-items:center; justify-content:center; background:#dff4ff; font-size:.82rem; }}
    .hero-top3 {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
    .hero-top3-card {{ display:grid; grid-template-columns:108px 1fr; gap:9px; border:1px solid var(--panel-border); background:#fff; border-radius:10px; box-shadow:var(--shadow-sm); overflow:hidden; text-decoration:none; color:inherit; transition:transform var(--transition), box-shadow var(--transition); }}
    .hero-top3-card:hover {{ transform:translateY(-2px); box-shadow:var(--shadow-md); }}
    .hero-top3-thumb-wrap {{ position:relative; width:108px; }}
    .hero-top3-thumb {{ width:100%; height:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
    .hero-top3-body {{ padding:8px 10px 8px 0; min-width:0; display:flex; align-items:center; }}
    .hero-top3-rank {{ position:absolute; top:8px; left:8px; z-index:2; min-width:24px; height:24px; border-radius:6px; display:inline-flex; align-items:center; justify-content:center; font-size:.75rem; font-weight:800; box-shadow:0 2px 8px rgba(15,23,42,.24); }}
    .hero-top3-rank.gold {{ background:linear-gradient(135deg,#f59e0b,#facc15); color:#3b2a00; }}
    .hero-top3-rank.silver {{ background:linear-gradient(135deg,#94a3b8,#e2e8f0); color:#1f2937; }}
    .hero-top3-rank.bronze {{ background:linear-gradient(135deg,#b45309,#d6a77a); color:#fff8ef; }}
    .hero-top3-title {{ margin:0; font-size:.88rem; line-height:1.4; color:#0f172a; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
    .content {{ display:grid; grid-template-columns:minmax(0,2fr) minmax(300px,1fr); gap:14px; align-items:start; }}
    .video-title {{ margin:0; font-size:clamp(1.05rem,2vw,1.3rem); line-height:1.45; font-weight:600; }}
    .video-meta {{ margin-top:6px; color:var(--text-sub); font-size:.86rem; display:flex; gap:10px; flex-wrap:wrap; }}
    .player-launch {{
      margin-top:10px; border:1px solid var(--panel-border); border-radius:12px; overflow:hidden; background:#000;
      width:100%; padding:0; cursor:pointer; position:relative; display:block;
      aspect-ratio:16/9;
    }}
    .player-launch.portrait {{ aspect-ratio:9/16; max-width:220px; margin-left:auto; margin-right:auto; }}
    .player-launch img {{ width:100%; height:100%; object-fit:cover; display:block; opacity:.9; transition:opacity .2s ease; }}
    .player-launch:hover img {{ opacity:1; }}
    .player-launch::after {{
      content:'▶ 再生';
      position:absolute; left:50%; top:50%; transform:translate(-50%, -50%);
      padding:10px 18px; border-radius:999px; border:1px solid rgba(255,255,255,.35);
      background:rgba(2,6,23,.72); color:#f8fafc; font-size:.88rem; font-weight:700;
      backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
    }}
    .player-modal {{
      position:fixed; inset:0; z-index:1200; display:none; align-items:center; justify-content:center;
      background:rgba(2,6,23,.82); padding:14px;
    }}
    .player-modal.open {{ display:flex; }}
    .player-sheet {{
      width:min(96vw,1020px); background:#020617; border:1px solid rgba(148,163,184,.45);
      border-radius:14px; overflow:hidden; box-shadow:0 22px 58px rgba(2,6,23,.55);
      position:relative;
    }}
    .player-sheet.portrait {{
      width:min(94vw,420px);
      max-height:calc(100dvh - 20px);
      display:grid;
      grid-template-rows:auto 1fr;
    }}
    .player-head {{
      height:44px; padding:6px 8px; display:flex; align-items:center; justify-content:flex-end;
      background:rgba(15,23,42,.9); border-bottom:1px solid rgba(148,163,184,.35);
      position:sticky; top:0; z-index:2;
    }}
    .player-close {{
      width:36px; height:36px; border-radius:999px; border:1px solid rgba(148,163,184,.55);
      background:rgba(15,23,42,.75); color:#e2e8f0; cursor:pointer; font-size:1.08rem; line-height:1;
      display:inline-flex; align-items:center; justify-content:center;
    }}
    .player-frame {{ width:100%; aspect-ratio:16/9; background:#000; }}
    .player-frame.portrait {{ aspect-ratio:auto; height:min(72dvh, 640px); }}
    .player-frame iframe {{ width:100%; height:100%; border:0; display:block; }}
    .action-row {{ margin-top:10px; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
    .action-btn {{ display:inline-flex; align-items:center; justify-content:center; gap:6px; border-radius:8px; border:1px solid #c8d2dd; background:#e5eaf0; color:#334155; text-decoration:none; font-size:.82rem; font-weight:700; padding:8px 10px; }}
    .action-btn .btn-icon {{ width:14px; height:14px; display:inline-flex; align-items:center; justify-content:center; flex:0 0 14px; }}
    .action-btn .btn-icon svg {{ width:14px; height:14px; display:block; }}
    .action-btn .btn-icon.xmark {{ font-size:13px; font-weight:800; line-height:1; }}
    .info-list {{ display:grid; gap:8px; }}
    .info-row {{ display:flex; justify-content:space-between; gap:8px; font-size:.84rem; color:var(--text-sub); }}
    .info-row strong {{ color:#0f172a; font-weight:700; }}
    .info-row .growth {{ color:var(--ok); font-size:.95rem; font-weight:800; }}
    .info-row .like {{ color:var(--like); font-size:.95rem; font-weight:800; }}
    .section-head {{ margin:0 0 8px; display:flex; justify-content:space-between; align-items:center; gap:8px; color:#1f3344; font-size:1rem; font-weight:800; }}
    .tabs {{ display:inline-flex; border:1px solid var(--panel-border); border-radius:10px; overflow:hidden; }}
    .tab {{ border:0; background:transparent; color:#64748b; padding:6px 10px; font-size:.76rem; font-weight:700; cursor:pointer; }}
    .tab.active {{ color:#fff; background:linear-gradient(135deg, rgba(99,208,255,.72), rgba(96,165,250,.82)); }}
    .tab-groups {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
    .chart-box {{ border:1px solid var(--panel-border); border-radius:10px; background:#f8fbff; padding:8px; }}
    .legend {{ margin-top:6px; color:var(--text-soft); font-size:.78rem; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }}
    .side-stack {{ display:grid; gap:14px; }}
    .rank-chip {{ min-width:34px; height:34px; border-radius:7px; display:inline-flex; align-items:center; justify-content:center; font-size:.9rem; font-weight:800; color:#fff; background:rgba(26,32,44,.82); }}
    .rank-chip.gold {{ background:linear-gradient(135deg,#f59e0b,#facc15); color:#3b2a00; border:1px solid rgba(180,120,0,.45); }}
    .rank-chip.silver {{ background:linear-gradient(135deg,#94a3b8,#e2e8f0); color:#1f2937; border:1px solid rgba(148,163,184,.5); }}
    .rank-chip.bronze {{ background:linear-gradient(135deg,#b45309,#d6a77a); color:#fff8ef; border:1px solid rgba(180,83,9,.45); }}
    .related-list {{ display:grid; gap:10px; }}
    .related-item {{ display:grid; grid-template-columns:108px 1fr; gap:9px; text-decoration:none; color:inherit; background:#fff; border:1px solid var(--panel-border); border-radius:10px; overflow:hidden; box-shadow:var(--shadow-sm); transition:transform var(--transition), box-shadow var(--transition); }}
    .related-item:hover {{ transform:translateY(-2px); box-shadow:var(--shadow-md); }}
    .related-item img {{ width:100%; height:100%; object-fit:cover; display:block; aspect-ratio:16/9; }}
    .related-body {{ padding:8px 10px 8px 0; min-width:0; }}
    .related-meta {{ margin-top:5px; font-size:.76rem; color:var(--text-soft); display:flex; gap:8px; flex-wrap:wrap; }}
    .related-title {{ margin:0; font-size:.88rem; line-height:1.4; color:#0f172a; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
    .empty-note {{ margin:0; color:var(--text-soft); font-size:.84rem; }}
    .back-top-wrap {{ text-align:center; margin-top:2px; }}
    .back-top-btn {{ display:inline-flex; align-items:center; justify-content:center; padding:9px 18px; border-radius:999px; border:1px solid #c8d2dd; background:#eef3f8; color:var(--accent-c); text-decoration:none; font-size:.84rem; font-weight:700; }}
    .footer {{ width:100%; margin:0; padding:18px 24px 22px; text-align:center; font-size:.76rem; color:#dbeafe; border-top:1px solid var(--header-border); background:var(--header-bg); }}
    .footer-links {{ display:flex; justify-content:center; gap:14px; margin-bottom:8px; font-size:.82rem; }}
    .footer-links a {{ color:#cfe0ff; text-decoration:none; }}
    @media (max-width:980px) {{ .content {{ grid-template-columns:1fr; }} }}
    @media (max-width:760px) {{
      .header-inner {{ padding:0 14px; height:54px; }} .header-meta {{ display:none; }}
      .main {{ padding:16px 14px 34px; }}
      .hero-top3 {{ grid-template-columns:1fr; }}
      .hero-top3-card {{ grid-template-columns:96px 1fr; }}
      .hero-top3-thumb-wrap {{ width:96px; }}
      .action-row {{ grid-template-columns:1fr; }}
      .related-item {{ grid-template-columns:96px 1fr; }}
      .player-modal {{
        align-items:stretch;
        padding:0;
      }}
      .player-sheet {{
        width:100vw;
        height:100dvh;
        max-height:100dvh;
        border-radius:0;
        border:0;
        box-shadow:none;
      }}
      .player-sheet.portrait {{ width:100vw; }}
      .player-head {{
        height:56px;
        padding:max(8px, env(safe-area-inset-top)) 10px 8px;
      }}
      .player-close {{
        width:46px;
        height:46px;
        font-size:1.28rem;
        border-width:2px;
      }}
      .player-frame {{ height:calc(100dvh - 56px - env(safe-area-inset-top)); }}
      .player-frame.portrait {{ height:calc(100dvh - 56px - env(safe-area-inset-top)); }}
    }}
  </style>
</head>
<body>
  <header class="header">
    <div class="header-inner">
      <a class="brand" href="/">
        <span class="brand-logo">VCLIP</span>
        <span class="brand-text">VTuber切り抜き<span class="brand-accent">ランキング</span></span>
      </a>
      <div class="header-meta"><span class="live-dot">リアルタイム更新中</span></div>
    </div>
  </header>
  <main class="main">
    <p class="hero-note">詳細ページは現在、試験運用中です。</p>
    <section class="panel top3-section">
      <h2 class="top3-head"><span class="top3-icon">✦</span>{html.escape(top3_heading)}</h2>
      <div class="hero-top3">{top3_html}</div>
    </section>
    <section class="content">
      <div class="left-stack" style="display:grid; gap:14px;">
        <article class="panel">
          <h1 class="video-title">{title_escaped}</h1>
          <div class="video-meta">
            <span>チャンネル: {channel_escaped}</span>
            <span>公開日: {published_escaped}</span>
            <span>video_id: {video_id_escaped}</span>
          </div>
          <button class="player-launch {player_aspect_class}" id="detail-player-launch" type="button" aria-label="{player_open_label}">
            <img src="{thumbnail_escaped}" alt="{title_escaped}" loading="eager" fetchpriority="high">
          </button>
          <div class="action-row">
            <a class="action-btn" href="{yt_url}" target="_blank" rel="noopener noreferrer"><span class="btn-icon"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="2" y="5" width="20" height="14" rx="4" fill="#ff0033"></rect><polygon points="10,9 16,12 10,15" fill="#ffffff"></polygon></svg></span>YouTube</a>
            <a class="action-btn" href="{detail_share_url}" target="_blank" rel="noopener noreferrer"><span class="btn-icon xmark">𝕏</span>シェア</a>
          </div>
        </article>
        <article class="panel">
          <div class="section-head">
            <span>再生・like推移（軽量）</span>
            <div class="tab-groups">
              <div class="tabs">
                <button class="tab active" data-metric="views">再生</button>
                <button class="tab" data-metric="likes">like</button>
              </div>
              <div class="tabs">
                <button class="tab active" data-range="7">7日</button>
                <button class="tab" data-range="30">30日</button>
              </div>
            </div>
          </div>
          <div class="chart-box">
            <svg viewBox="0 0 900 220" width="100%" height="220">
              <defs><linearGradient id="lineGrad" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#63d0ff" /><stop offset="100%" stop-color="#60a5fa" /></linearGradient></defs>
              <g stroke="rgba(100,116,139,0.26)" stroke-width="1">
                <line x1="88" y1="30" x2="88" y2="190" /><line x1="88" y1="190" x2="860" y2="190" /><line x1="88" y1="150" x2="860" y2="150" />
                <line x1="88" y1="110" x2="860" y2="110" /><line x1="88" y1="70" x2="860" y2="70" />
              </g>
              <polyline id="line" fill="none" stroke="url(#lineGrad)" stroke-width="4" points="88,170 860,60" />
              <g id="y-axis-labels"></g>
              <g id="x-axis-labels"></g>
              <g id="point-labels"></g>
              <g id="dots"></g>
            </svg>
          </div>
          <div class="legend"><span id="legendL"></span><span id="legendR"></span></div>
        </article>
      </div>
      <aside class="side-stack">
        <article class="panel">
          <div class="section-head"><span>現在のランキング情報</span></div>
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <span class="rank-chip {rank_chip_class}">{html.escape(rank_chip_value)}</span>
            <div style="min-width:0;">
              <div style="font-size:.86rem; color:var(--text-soft);">カテゴリ</div>
              <div style="font-size:.94rem; font-weight:700; color:#0f172a;">{html.escape(('Shorts' if content_type == 'shorts' else '動画') + ' / ' + period_category_label)}</div>
            </div>
          </div>
          <div class="info-list">
            <div class="info-row"><span>Current Rank</span><strong>{html.escape(current_rank_label)}</strong></div>
            <div class="info-row"><span>Best Rank</span><strong>{html.escape(best_rank_label)}</strong></div>
            <div class="info-row"><span>24h Views</span><strong class="growth">▶ +{payload["views_delta_24h"]:,}</strong></div>
            <div class="info-row"><span>Total Views</span><strong>{latest_view_count:,}</strong></div>
            <div class="info-row"><span>24h Likes</span><strong class="like">❤ +{payload["likes_delta_24h"]:,}</strong></div>
            <div class="info-row"><span>Total Likes</span><strong>{latest_like_count:,}</strong></div>
            <div class="info-row"><span>24h Comments</span><strong>💬 +{payload["comments_delta_24h"]:,}</strong></div>
            <div class="info-row"><span>Total Comments</span><strong>{payload["latest_comment_count"]:,}</strong></div>
            <div class="info-row"><span>初回ランクイン</span><strong>{html.escape(payload["first_ranked_at"])}</strong></div>
            <div class="info-row"><span>最高順位日時</span><strong>{html.escape(best_rank_at_label or "-")}</strong></div>
            <div class="info-row"><span>更新日時</span><strong>{html.escape(current_rank_at_label or "-")}</strong></div>
          </div>
        </article>
        <article class="panel">
          <div class="section-head"><span>同チャンネル関連動画</span></div>
          <div class="related-list">{related_html}</div>
        </article>
      </aside>
    </section>
    <div class="back-top-wrap"><a class="back-top-btn" href="https://vclipranking.com/">TOPに戻る</a></div>
  </main>
  <footer class="footer">
    <div class="footer-links">
      <a href="/policy">プライバシーポリシー</a>
      <a href="https://x.com/Vcliprank" target="_blank" rel="noopener noreferrer">お問い合わせ</a>
    </div>
    <span>VCLIP | VTuber切り抜きランキング &copy; 2026</span>
  </footer>
  <div id="detail-player-modal" class="player-modal" aria-hidden="true">
    <div class="player-sheet {player_aspect_class}" role="dialog" aria-modal="true" aria-label="動画プレーヤー">
      <div class="player-head">
        <button id="detail-player-close" class="player-close" type="button" aria-label="閉じる">×</button>
      </div>
      <div class="player-frame {player_aspect_class}">
        <iframe
          id="detail-player-iframe"
          src=""
          title="YouTube player"
          loading="lazy"
          allow="autoplay; encrypted-media; picture-in-picture"
          allowfullscreen
        ></iframe>
      </div>
    </div>
  </div>
  <script>
    const trendPayload = {payload_json};
    const detailPlayerLaunch = document.getElementById("detail-player-launch");
    const detailPlayerModal = document.getElementById("detail-player-modal");
    const detailPlayerClose = document.getElementById("detail-player-close");
    const detailPlayerIframe = document.getElementById("detail-player-iframe");
    function openDetailPlayer() {{
      detailPlayerIframe.src = "https://www.youtube-nocookie.com/embed/{video_id_escaped}?rel=0&autoplay=1&playsinline=1";
      detailPlayerModal.classList.add("open");
      detailPlayerModal.setAttribute("aria-hidden", "false");
      document.body.classList.add("player-open");
    }}
    function closeDetailPlayer() {{
      detailPlayerModal.classList.remove("open");
      detailPlayerModal.setAttribute("aria-hidden", "true");
      detailPlayerIframe.src = "";
      document.body.classList.remove("player-open");
    }}
    if (detailPlayerLaunch) {{
      detailPlayerLaunch.addEventListener("click", openDetailPlayer);
    }}
    if (detailPlayerClose) {{
      detailPlayerClose.addEventListener("click", closeDetailPlayer);
    }}
    if (detailPlayerModal) {{
      detailPlayerModal.addEventListener("click", (event) => {{
        if (event.target === detailPlayerModal) closeDetailPlayer();
      }});
    }}
    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && detailPlayerModal?.classList.contains("open")) closeDetailPlayer();
    }});
    const tabs = Array.from(document.querySelectorAll(".tab"));
    let activeRange = "7";
    let activeMetric = "views";
    function mapPoints(values) {{
      if (!values.length) return [];
      const min = Math.min(...values), max = Math.max(...values), r = Math.max(1, max - min);
      const left = 88, right = 860, top = 40, bottom = 190;
      return values.map((v, i) => [left + i * (right - left) / Math.max(1, values.length - 1), bottom - ((v - min) / r) * (bottom - top)]);
    }}
    function renderTrend() {{
      const metricKey = activeMetric === "likes" ? "likes" : "views";
      const values = metricKey === "likes"
        ? (activeRange === "30" ? (trendPayload.like_trend_30 || []) : (trendPayload.like_trend_7 || []))
        : (activeRange === "30" ? (trendPayload.trend_30 || []) : (trendPayload.trend_7 || []));
      const dates = activeRange === "30" ? (trendPayload.trend_30_dates || []) : (trendPayload.trend_7_dates || []);
      const points = mapPoints(values);
      const line = document.getElementById("line");
      const dots = document.getElementById("dots");
      const pointLabels = document.getElementById("point-labels");
      const yLabels = document.getElementById("y-axis-labels");
      const xLabels = document.getElementById("x-axis-labels");
      const legendL = document.getElementById("legendL");
      const legendR = document.getElementById("legendR");
      if (metricKey === "likes" && !trendPayload.like_has_data) {{
        line.setAttribute("points", "");
        line.setAttribute("stroke", "#f472b6");
        dots.innerHTML = "";
        pointLabels.innerHTML = "";
        yLabels.innerHTML = "";
        xLabels.innerHTML = "";
        legendL.textContent = "No data";
        legendR.textContent = "";
        return;
      }}
      if (!points.length) {{
        line.setAttribute("points", "");
        dots.innerHTML = "";
        pointLabels.innerHTML = "";
        yLabels.innerHTML = "";
        xLabels.innerHTML = "";
        legendL.textContent = "No data";
        legendR.textContent = "";
        return;
      }}
      line.setAttribute("stroke", metricKey === "likes" ? "#f472b6" : "url(#lineGrad)");
      line.setAttribute("points", points.map(([x,y]) => `${{x.toFixed(1)}},${{y.toFixed(1)}}`).join(" "));
      dots.innerHTML = "";
      pointLabels.innerHTML = "";
      points.forEach(([x,y]) => {{
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        c.setAttribute("cx", x.toFixed(1)); c.setAttribute("cy", y.toFixed(1)); c.setAttribute("r", "3.5");
        c.setAttribute("fill", metricKey === "likes" ? "#f472b6" : "#63d0ff");
        dots.appendChild(c);
      }});
      const labelIndexes = [];
      if (values.length <= 10) {{
        for (let i = 0; i < values.length; i++) labelIndexes.push(i);
      }} else {{
        const step = Math.max(1, Math.floor(values.length / 6));
        for (let i = 0; i < values.length; i += step) labelIndexes.push(i);
        if (!labelIndexes.includes(values.length - 1)) labelIndexes.push(values.length - 1);
      }}
      labelIndexes.forEach((idx) => {{
        const p = points[idx];
        if (!p) return;
        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("x", p[0].toFixed(1));
        t.setAttribute("y", Math.max(14, p[1] - 8).toFixed(1));
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("fill", metricKey === "likes" ? "#be185d" : "#0369a1");
        t.setAttribute("font-size", "10");
        t.setAttribute("font-weight", "700");
        t.textContent = Number(values[idx] || 0).toLocaleString("ja-JP");
        pointLabels.appendChild(t);
      }});

      const min = Math.min(...values);
      const max = Math.max(...values);
      const mid = Math.round((min + max) / 2);
      const yTicks = [
        {{ y: 190, v: min }},
        {{ y: 110, v: mid }},
        {{ y: 40, v: max }},
      ];
      yLabels.innerHTML = "";
        yTicks.forEach((tick) => {{
          const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
          t.setAttribute("x", "78");
          t.setAttribute("y", String(tick.y));
          t.setAttribute("text-anchor", "end");
          t.setAttribute("dominant-baseline", "middle");
          t.setAttribute("fill", "rgba(71,85,105,0.92)");
          t.setAttribute("font-size", "10");
          t.textContent = Number(tick.v || 0).toLocaleString("ja-JP");
          yLabels.appendChild(t);
        }});

      xLabels.innerHTML = "";
      const xTickIndexes = [];
      if (values.length <= 7) {{
        for (let i = 0; i < values.length; i++) xTickIndexes.push(i);
      }} else {{
        [0, 4, 9, 14, 19, 24, values.length - 1].forEach((idx) => {{
          if (idx >= 0 && idx < values.length && !xTickIndexes.includes(idx)) xTickIndexes.push(idx);
        }});
      }}
      xTickIndexes.forEach((idx) => {{
        const point = points[idx];
        if (!point) return;
        const d = dates[idx] || "";
        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("x", point[0].toFixed(1));
        t.setAttribute("y", "206");
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("fill", "rgba(71,85,105,0.92)");
        t.setAttribute("font-size", "10");
        t.textContent = d;
        xLabels.appendChild(t);
      }});

      legendL.textContent = `期間: 直近${{activeRange}}日 / 点数: ${{values.length}}`;
      const unit = metricKey === "likes" ? "likes" : "views";
      legendR.textContent = `最終値: ${{(values[values.length - 1] || 0).toLocaleString("ja-JP")}} ${{unit}}`;
    }}
    tabs.forEach((btn) => btn.addEventListener("click", () => {{
      if (btn.dataset.range) {{
        activeRange = btn.dataset.range || "7";
        tabs.filter((t) => t.dataset.range).forEach((t) => t.classList.remove("active"));
        btn.classList.add("active");
      }}
      if (btn.dataset.metric) {{
        activeMetric = btn.dataset.metric === "likes" ? "likes" : "views";
        tabs.filter((t) => t.dataset.metric).forEach((t) => t.classList.remove("active"));
        btn.classList.add("active");
      }}
      renderTrend();
    }}));
    renderTrend();
  </script>
</body>
</html>
"""
    return 200, body


class TestSiteHandler(BaseHTTPRequestHandler):
    def _request_base_url(self) -> str:
        configured = _normalize_base_url(SITE_BASE_URL)
        if configured:
            return configured

        forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",", 1)[0].strip()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else "http"
        host = (self.headers.get("Host") or f"{HOST}:{PORT}").strip()
        return _normalize_base_url(f"{scheme}://{host}")

    def _resolve_admin_token(self, query: dict[str, list[str]], body_token: str = "") -> str:
        header_token = (self.headers.get("X-Admin-Token") or "").strip()
        if header_token:
            return header_token
        query_token = (query.get("admin_token") or [""])[0].strip()
        if query_token:
            return query_token
        return (body_token or "").strip()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path_only = parsed.path
        query = parse_qs(parsed.query)

        if path_only != "/api/admin/post-x":
            self.send_error(404, "Not found")
            return

        content_length_raw = self.headers.get("Content-Length") or "0"
        try:
            content_length = max(0, int(content_length_raw))
        except ValueError:
            content_length = 0
        body_raw = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            body_data = json.loads(body_raw.decode("utf-8")) if body_raw else {}
        except ValueError:
            _json_response(self, 400, {"ok": False, "error": "invalid_json"})
            return

        if ADMIN_TOKEN:
            token = self._resolve_admin_token(query, str(body_data.get("admin_token") or ""))
            if token != ADMIN_TOKEN:
                _json_response(self, 403, {"ok": False, "error": "admin_token_required"})
                return

        text = str(body_data.get("text") or "")
        ok, status, result = _post_text_to_x_api(text)
        if not ok:
            _json_response(self, status, {"ok": False, **result})
            return
        _json_response(self, 200, {"ok": True, **result})

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path_only = parsed.path
        query = parse_qs(parsed.query)

        if path_only == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", "/assets/favicon.ico")
            self.end_headers()
            return
        if path_only == "/assets/noto-sans-jp.ttf":
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return

        if path_only in {"/assets/favicon.ico", "/assets/ueno-icon.jpg"}:
            if not FAVICON_FILE:
                self.send_error(404, "Favicon not configured")
                return
            try:
                with open(FAVICON_FILE, "rb") as icon_file:
                    body = icon_file.read()
            except OSError:
                self.send_error(404, "Favicon not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", _favicon_content_type())
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only == "/assets/site-logo.png":
            if not LOGO_FILE or not Path(LOGO_FILE).exists():
                self.send_error(404, "Logo not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return
        if path_only == "/assets/site-logo.jpg":
            if not Path(DEFAULT_OG_IMAGE_FILE).exists():
                self.send_error(404, "Logo not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return

        if path_only == "/robots.txt":
            body = _build_robots_txt(self._request_base_url()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return

        if path_only == "/sitemap.xml":
            body = _build_sitemap_xml(self._request_base_url()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        if path_only == "/policy":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return

        if path_only.startswith("/video/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return

        if path_only in {"/", "/index.html"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return

        if path_only == "/admin":
            if ADMIN_TOKEN:
                token = (query.get("admin_token") or [""])[0]
                if token != ADMIN_TOKEN:
                    self.send_error(403, "Admin token required")
                    return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return

        self.send_error(404, "Not found")
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path_only = parsed.path
        query = parse_qs(parsed.query)

        if path_only == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", "/assets/favicon.ico")
            self.end_headers()
            return
        if path_only == "/assets/noto-sans-jp.ttf":
            try:
                with open(FONT_FILE, "rb") as font_file:
                    body = font_file.read()
            except OSError:
                self.send_error(404, "Font not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only in {"/assets/favicon.ico", "/assets/ueno-icon.jpg"}:
            if not FAVICON_FILE:
                self.send_error(404, "Favicon not configured")
                return
            try:
                with open(FAVICON_FILE, "rb") as icon_file:
                    body = icon_file.read()
            except OSError:
                self.send_error(404, "Favicon not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", _favicon_content_type())
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only == "/assets/site-logo.png":
            if not LOGO_FILE:
                self.send_error(404, "Logo not configured")
                return
            try:
                with open(LOGO_FILE, "rb") as logo_file:
                    body = logo_file.read()
            except OSError:
                self.send_error(404, "Logo not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        if path_only == "/assets/site-logo.jpg":
            try:
                with open(DEFAULT_OG_IMAGE_FILE, "rb") as logo_file:
                    body = logo_file.read()
            except OSError:
                self.send_error(404, "Logo not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only == "/robots.txt":
            body = _build_robots_txt(self._request_base_url()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only == "/sitemap.xml":
            body = _build_sitemap_xml(self._request_base_url()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only == "/policy":
            body = render_policy_page(base_url=self._request_base_url()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only.startswith("/video/"):
            video_id = _normalize_video_id(path_only.rsplit("/", 1)[-1])
            if not video_id:
                self.send_error(404, "Not found")
                return
            period_key = (query.get("period") or ["daily"])[0]
            status, html_body = render_video_detail_page(
                video_id,
                base_url=self._request_base_url(),
                period_key=period_key,
            )
            body = html_body.encode("utf-8", errors="replace")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only not in {"/", "/index.html", "/admin"}:
            self.send_error(404, "Not found")
            return

        requested_admin = path_only == "/admin"
        token = (query.get("admin_token") or [""])[0]

        is_admin = False
        if requested_admin:
            if ADMIN_TOKEN and token != ADMIN_TOKEN:
                self.send_error(403, "Admin token required")
                return
            is_admin = True
        elif ADMIN_TOKEN:
            is_admin = token == ADMIN_TOKEN

        base_url = self._request_base_url()
        try:
            body = render_homepage(is_admin=is_admin, base_url=base_url).encode("utf-8", errors="replace")
        except Exception as exc:
            logger.exception("Failed to render test site")
            body = render_error_page(exc, base_url=base_url).encode("utf-8", errors="replace")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        logger.info("%s - %s", self.client_address[0], format % args)

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = ThreadingHTTPServer((HOST, PORT), TestSiteHandler)
    logger.info("Test site running at http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Test site stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()



