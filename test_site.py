"""
test_site.py -- Local ranking viewer with period and group tabs.
"""

from __future__ import annotations

import html
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from config import EXCLUDED_CHANNELS_FILE, GROUP_KEYWORDS
from db import fetchall

logger = logging.getLogger(__name__)

HOST = os.getenv("TEST_SITE_HOST", "127.0.0.1")
PORT = int(os.getenv("TEST_SITE_PORT", "8000"))
FONT_FILE = r"C:\Users\11bs0\OneDrive\デスクトップ\NotoSansJP-VariableFont_wght.ttf"
LOGO_FILE = os.getenv("TEST_SITE_LOGO_FILE", "").strip()
FAVICON_FILE = os.getenv(
    "TEST_SITE_FAVICON_FILE",
    str(Path(__file__).resolve().parent / "assets" / "ueno-icon.jpg"),
).strip()
ADMIN_TOKEN = os.getenv("TEST_SITE_ADMIN_TOKEN", "")
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "").strip()
SITE_BASE_URL = os.getenv("TEST_SITE_BASE_URL", "").strip()
YOUTUBE_DAILY_SEARCH_UNIT_LIMIT = int(os.getenv("YOUTUBE_DAILY_SEARCH_UNIT_LIMIT", "8000"))
YOUTUBE_QUOTA_STATE_FILE = os.getenv("YOUTUBE_QUOTA_STATE_FILE", ".youtube_quota_state.json")
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
    og_image_url = f"{base_url}/assets/site-logo.png" if base_url and LOGO_FILE else ""

    tags = [
        f'<meta name="description" content="{html.escape(SITE_DESCRIPTION, quote=True)}">',
        f'<meta name="robots" content="{robots}">',
        '<link rel="icon" type="image/jpeg" href="/assets/ueno-icon.jpg">',
        '<link rel="shortcut icon" href="/assets/ueno-icon.jpg">',
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
            f'<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:title" content="{html.escape(SITE_TITLE, quote=True)}">',
            f'<meta name="twitter:description" content="{html.escape(SITE_DESCRIPTION, quote=True)}">',
        ]
    )
    if canonical_url:
        escaped_canonical = html.escape(canonical_url, quote=True)
        tags.append(f'<meta property="og:url" content="{escaped_canonical}">')
        tags.append(f'<meta name="twitter:url" content="{escaped_canonical}">')

    if og_image_url:
        escaped_og_image = html.escape(og_image_url, quote=True)
        tags.append(f'<meta property="og:image" content="{escaped_og_image}">')
        tags.append(f'<meta name="twitter:image" content="{escaped_og_image}">')

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
    urls = [
        f"{normalized}/",
        f"{normalized}/index.html",
    ]
    items = "".join(
        f"<url><loc>{html.escape(url)}</loc><lastmod>{now_iso}</lastmod></url>" for url in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{items}"
        "</urlset>"
    )


def _build_robots_txt(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    lines = ["User-agent: *", "Allow: /"]
    if normalized:
        lines.append(f"Sitemap: {normalized}/sitemap.xml")
    return "\n".join(lines) + "\n"
def _fetch_latest_rankings(table: str) -> tuple[datetime | None, list[dict]]:
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
            v.published_at
        FROM {table} r
        JOIN videos v ON v.video_id = r.video_id
        WHERE r.calculated_at = %s
        ORDER BY r.rank
        LIMIT 100
        """,
        (calculated_at,),
    )

    now_utc = datetime.now(timezone.utc)
    for row in rows:
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
        channel_ids.append(value)
    return list(dict.fromkeys(channel_ids))


def _fetch_daily_provisional_rows(content_type: str) -> list[dict]:
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

    rows = fetchall(
        f"""
        WITH latest_stats AS (
            SELECT DISTINCT ON (video_id)
                video_id,
                view_count,
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
                (l.view_count - f.view_count) AS view_growth
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
                published_at
            FROM provisional
            WHERE view_growth > 0
        )
        SELECT *
        FROM ranked
        ORDER BY rank
        LIMIT 100
        """,
        tuple(params),
    )

    now_utc = datetime.now(timezone.utc)
    for row in rows:
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
    if period_key == "daily":
        return f"本日({month_day})の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    if period_key == "weekly":
        return f"直近7日間の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    if period_key == "monthly":
        return f"直近30日間の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    return f"#VTuber切り抜きランキング {rank}位の{content_label}です！"


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


def _merge_daily_rows(strict_rows: list[dict], provisional_rows: list[dict]) -> list[dict]:
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

    sorted_rows = sorted(
        merged.values(),
        key=lambda r: (_row_growth(r), r.get("video_id") or ""),
        reverse=True,
    )[:100]
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
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        title_plain = " ".join(title_raw.split())
        share_title = _truncate_text(title_plain, 56)
        share_prefix = _share_prefix_for_period(period_key, month_day, row["rank"], content_label)
        share_text = f"{share_prefix}  {share_title}"
        share_url = (
            "https://twitter.com/intent/tweet?text="
            f"{quote(share_text, safe='')}&url={quote(video_url, safe='')}"
        )
        content_type = html.escape((row.get("content_type") or "").lower())
        published_label = ""
        published_at = row.get("published_at")
        if isinstance(published_at, datetime):
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            published_label = published_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")

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
            <article class="card video-card{rank_class}">
              <a class="thumb" href="{video_url}" target="_blank" rel="noreferrer"
                 data-video-id="{video_id}" data-video-title="{title}" data-content-type="{content_type}">
                <img src="{_thumbnail_url(video_id)}" alt="{title}" loading="lazy">
                <div class="{rank_badge_class}">{rank}</div>
                {new_badge_html}
                {duration_html}
              </a>
              <div class="card-meta">
                <a class="card-title" href="{video_url}" target="_blank" rel="noreferrer">{title}</a>
                <div class="card-info card-info-top">
                  <a class="card-channel channel-link" href="{channel_url}" target="_blank" rel="noreferrer">
                    {icon_html}
                    <span class="channel-name">{channel_name}</span>
                  </a>
                  {group_pill_html}
                </div>
                <div class="card-info card-info-bottom">
                  <span class="card-views"><em class="arrow">↑</em><span class="view-growth">+{row['view_growth']:,}</span></span>
                  <span class="card-date">{html.escape(published_label)}</span>
                </div>
                <div class="card-actions">
                  <a class="card-action-link" href="{video_url}" target="_blank" rel="noreferrer">YouTubeで開く</a>
                  <a class="card-action-link card-share-link" href="{share_url}" target="_blank" rel="noreferrer">SNSでシェア</a>
                </div>
              </div>
            </article>
            """
        )
    return "".join(cards)
def _render_rank_sections(rows: list[dict], show_group: bool = True, period_key: str = "daily", content_label: str = "shorts") -> str:
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
) -> str:
    provisional_shorts_rows = provisional_shorts_rows or []
    provisional_video_rows = provisional_video_rows or []

    if period_key == "daily":
        display_shorts_rows = _merge_daily_rows(shorts_rows, provisional_shorts_rows)
        display_video_rows = _merge_daily_rows(video_rows, provisional_video_rows)
    else:
        display_shorts_rows = shorts_rows
        display_video_rows = video_rows

    if not display_shorts_rows and not display_video_rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    shorts_html = (
        _render_rank_sections(display_shorts_rows, show_group=show_group, period_key=period_key, content_label="shorts")
        if display_shorts_rows
        else '<div class="empty">Shortsに該当する動画はありません。</div>'
    )
    video_html = (
        _render_rank_sections(display_video_rows, show_group=show_group, period_key=period_key, content_label="動画")
        if display_video_rows
        else '<div class="empty">動画に該当する動画はありません。</div>'
    )

    return f"""
    <div class="content-panel" data-content-panel="shorts">{shorts_html}</div>
    <div class="content-panel" data-content-panel="video">{video_html}</div>
    """
def _build_period_payload(is_admin: bool = False) -> list[dict]:
    payload = []
    for period_key, label, shorts_table, video_table in PERIODS:
        shorts_calculated_at, shorts_rows = _fetch_latest_rankings(shorts_table)
        video_calculated_at, video_rows = _fetch_latest_rankings(video_table)

        provisional_shorts_rows: list[dict] = []
        provisional_video_rows: list[dict] = []
        if period_key == "daily":
            provisional_shorts_rows = _fetch_daily_provisional_rows("shorts")
            provisional_video_rows = _fetch_daily_provisional_rows("video")

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
            WHERE (published_at + interval '9 hours')::date = CURRENT_DATE
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
            WHERE published_at >= (NOW() AT TIME ZONE 'UTC') - interval '24 hours'
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
    if LOGO_FILE and Path(LOGO_FILE).exists():
        logo_html = """
          <div class="hero-logo-wrap">
            <img class="hero-logo" src="/assets/site-logo.png" alt="ぶいくりっぷ ロゴ" loading="eager">
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
      padding:6px 14px;border-radius:10px;background:var(--accent-gradient);
      display:flex;align-items:center;justify-content:center;
      font-size:0.95rem;font-weight:900;color:#fff;letter-spacing:0.06em;
      box-shadow:0 0 18px rgba(167,139,250,0.3);
    }}
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
    .hero {{ margin-top:18px;display:grid;grid-template-columns:1.5fr 0.9fr;gap:16px; }}
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
    /* ── Sidebar NEW picks ── */
    .hero-side {{ padding:22px 18px; }}
    .side-header {{ display:flex;align-items:center;gap:10px;margin-bottom:16px; }}
    .side-header-icon {{
      width:28px;height:28px;border-radius:8px;
      background:linear-gradient(135deg,#fbbf24,#f97316);
      display:flex;align-items:center;justify-content:center;font-size:0.85rem;
    }}
    .side-title {{ font-size:1.05rem;font-weight:800;margin:0; }}
    .new-list {{ display:flex;flex-direction:column;gap:10px; }}
    .new-item {{
      padding:12px 14px;border-radius:12px;border:1px solid var(--glass-border);
      background:rgba(255,255,255,0.03);display:flex;align-items:center;gap:10px;
      transition:background 0.2s;cursor:pointer;text-decoration:none;color:var(--text);
    }}
    .new-item:hover {{ background:rgba(255,255,255,0.07); }}
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
    .card {{
      border:1px solid var(--glass-border);border-radius:18px;overflow:hidden;
      background:rgba(255,255,255,0.03);transition:transform 0.2s,box-shadow 0.2s;
    }}
    .card:hover {{ transform:translateY(-3px);box-shadow:0 14px 34px rgba(0,0,0,0.3); }}
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
    .card-info-top {{ margin-bottom:6px; }}
    .card-info-bottom {{ justify-content:space-between;margin-bottom:8px;font-size:0.84rem; }}
    .card-date {{ color:var(--text-dim);white-space:nowrap; }}
    .card-actions {{ display:flex;justify-content:space-between;align-items:center;gap:12px;font-size:0.82rem; }}
    .card-action-link {{ color:#8ad7ff;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px; }}
    .card-action-link:hover {{ color:#b8e9ff; }}
    .card-share-link {{ margin-left:auto; }}
    .channel-link {{ display:inline-flex;align-items:center;gap:6px;text-decoration:none;color:var(--text-dim);min-width:0;flex:1;max-width:calc(100% - 84px); }}
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
      display:flex;align-items:center;gap:4px;
    }}
    .card-views .view-growth {{
      color:var(--accent-purple);
    }}
    .arrow {{ font-style:normal;color:#34d399; }}
    .pill {{ border:1px solid var(--glass-border);border-radius:999px;padding:2px 8px;white-space:nowrap;font-size:0.72rem;color:var(--text-dim); }}
    .empty {{ padding:20px;border:1px dashed var(--glass-border);color:var(--text-dim);background:rgba(255,255,255,0.03); }}
    /* ── Pagination tabs ── */
    .page-tabs {{ display:none;gap:4px;flex-wrap:wrap;margin-top:12px; }}
    .page-tabs.bottom {{ margin-top:16px;justify-content:center; }}
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
    /* ── Player modal ── */
    .player-modal {{
      position:fixed;inset:0;background:rgba(2,6,10,0.84);
      display:none;align-items:center;justify-content:center;z-index:1000;padding:14px;
    }}
    .player-modal.open {{ display:flex; }}
    .player-sheet {{ width:min(94vw,460px);background:#0a131a;border:1px solid var(--glass-border);box-shadow:0 20px 60px rgba(0,0,0,0.55);border-radius:16px;overflow:hidden; }}
    .player-sheet.landscape {{ width:min(96vw,980px); }}
    .player-topbar {{
      height:44px;display:flex;align-items:center;justify-content:space-between;
      gap:10px;padding:6px 8px;border-bottom:1px solid rgba(231,242,251,0.12);background:rgba(10,19,26,0.92);
    }}
    .player-controls {{ display:inline-flex;align-items:center;gap:6px; }}
    .player-toggle {{
      min-width:38px;height:30px;border-radius:999px;
      border:1px solid rgba(231,242,251,0.24);background:rgba(255,255,255,0.04);
      color:#c9d8e5;font-size:0.78rem;cursor:pointer;font:inherit;padding:0 10px;
    }}
    .player-toggle.active {{ background:rgba(99,208,255,0.2);border-color:rgba(99,208,255,0.72);color:#e9f7ff;font-weight:700; }}
    .player-close {{
      width:34px;height:34px;border-radius:999px;
      background:rgba(10,19,26,0.82);color:#e7f2fb;border:1px solid rgba(231,242,251,0.28);
      font-size:1.15rem;line-height:1;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;font:inherit;
    }}
    .player-frame {{ position:relative;width:100%;aspect-ratio:9/16;background:#000; }}
    .player-frame.landscape {{ aspect-ratio:16/9; }}
    .player-frame iframe {{ width:100%;height:100%;border:0; }}
    /* ── Admin ── */
    .admin-board {{ border:1px solid var(--glass-border);background:linear-gradient(180deg,#121a25,#0f161f);box-shadow:0 24px 60px rgba(0,0,0,0.35);margin-top:12px;padding:14px;border-radius:16px; }}
    .admin-board-head h2 {{ font-size:1rem;margin:0; }}
    .admin-board-head p {{ color:var(--text-dim);margin-top:4px;font-size:0.82rem; }}
    .admin-metric-grid {{ margin-top:10px;display:grid;gap:8px;grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .admin-metric-card {{ border:1px solid var(--glass-border);background:linear-gradient(180deg,#172232,#121a25);padding:9px 10px;min-height:62px;display:grid;align-content:center;gap:4px;border-radius:10px; }}
    .admin-metric-card span {{ color:var(--text-dim);font-size:0.75rem; }}
    .admin-metric-card strong {{ font-size:1rem;font-weight:800; }}
    .admin-quota {{ display:inline-flex;align-items:center;gap:10px;font-size:0.88rem;color:var(--text-dim); }}
    .admin-pill {{ border-radius:999px;padding:4px 10px;font-weight:700;color:#081017; }}
    .admin-pill.ok {{ background:#5ee0b0; }}
    .admin-pill.warn {{ background:#f4b942; }}
    .admin-pill.danger {{ background:#ff7c7c; }}
    .admin-pill.muted {{ background:#9fb2c1; }}
    /* ── Responsive ── */
    @media (max-width:1024px) {{
      .hero {{ grid-template-columns:1fr; }}
      .cards {{ grid-template-columns:repeat(2,1fr); }}
      .admin-metric-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    }}
    @media (max-width:760px) {{
      .shell {{ width:calc(100% - 20px);padding:12px 0 40px; }}
      .topbar-nav {{ display:none; }}
      .topbar {{ padding:10px 14px;border-radius:12px; }}
      .topbar-brand {{ gap:8px;font-size:0.9rem; }}
      .topbar-logo {{ padding:5px 10px;font-size:0.8rem;border-radius:8px; }}
      .hero {{ margin-top:12px;gap:12px; }}
      .glass-panel {{ border-radius:14px; }}
      .hero-main {{ padding:22px 16px; }}
      .hero-eyebrow {{ font-size:0.7rem;margin-bottom:10px; }}
      .hero-heading {{ font-size:1.35rem;line-height:1.35; }}
      .hero-desc {{ font-size:0.85rem;margin-top:10px; }}
      .hero-stats {{ flex-wrap:wrap;gap:16px;margin-top:16px; }}
      .stat-value {{ font-size:1.15rem; }}
      .stat-label {{ font-size:0.68rem; }}
      .hero-side {{ padding:18px 14px; }}
      .side-header {{ margin-bottom:12px; }}
      .side-header-icon {{ width:24px;height:24px;font-size:0.75rem; }}
      .side-title {{ font-size:0.95rem; }}
      .new-list {{ gap:8px; }}
      .new-item {{ padding:10px 12px;border-radius:10px;gap:8px; }}
      .new-badge {{ padding:3px 9px;font-size:0.68rem; }}
      .new-text {{ font-size:0.82rem; }}
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
      .page-tabs {{ display:flex;gap:3px; }}
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
      .player-modal {{ align-items:flex-end;padding:4px; }}
      .player-sheet {{ width:100%;max-height:calc(100dvh - 8px); }}
      .player-frame {{ max-height:calc(100dvh - 130px); }}
    }}
    @media (max-width:400px) {{
      .topbar-brand {{ align-items:flex-start; }}
      .topbar-title {{ display:inline-block;font-size:0.76rem;line-height:1.2;white-space:normal; }}
      .topbar-logo {{ padding:4px 8px;font-size:0.72rem; }}
      .hero-heading {{ font-size:1.15rem; }}
      .hero-stats {{ gap:12px; }}
      .stat-value {{ font-size:1rem; }}
      .filter-row {{ flex-direction:column;align-items:flex-start; }}
    }}
  </style>
</head>
<body class="{body_class}">
  <div class="bg-canvas"></div>
  <main class="shell">
    <!-- ── Topbar ── -->
    <nav class="topbar animate-in">
      <div class="topbar-brand">
        <div class="topbar-logo">VCLIP</div>
        <span class="topbar-title">VTuber\u5207\u308a\u629c\u304d<span class="topbar-accent">\u30e9\u30f3\u30ad\u30f3\u30b0</span></span>
      </div>

    </nav>

    <!-- ── Hero + Sidebar ── -->
    <section class="hero">
      <section class="glass-panel hero-main animate-in delay-1">
        <div class="hero-eyebrow">
          <span class="dot"></span>
          <span>LIVE \u30fb \u30ea\u30a2\u30eb\u30bf\u30a4\u30e0\u66f4\u65b0\u4e2d</span>
        </div>
        <h1 class="hero-heading">
          VTuber\u5207\u308a\u629c\u304d\u306e<br>
          <span class="gradient-text">\u30c8\u30ec\u30f3\u30c9\u3092\u4e00\u76ee\u3067\u30c1\u30a7\u30c3\u30af</span>
        </h1>
        <p class="hero-desc">
          Shorts\u30fb\u52d5\u753b\u306e\u518d\u751f\u6570\u5897\u52a0\u3092\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0\u3067\u96c6\u8a08\u3002<br>
          \u3044\u307e\u8a71\u984c\u306e\u5207\u308a\u629c\u304d\u3092\u30e9\u30f3\u30ad\u30f3\u30b0\u5f62\u5f0f\u3067\u304a\u5c4a\u3051\u3057\u307e\u3059\u3002
        </p>
        <div class="hero-stats" id="hero-stats"></div>
        {admin_html}
      </section>

      <aside class="glass-panel hero-side animate-in delay-2">
        <div class="side-header">
          <div class="side-header-icon">\u2728</div>
          <h2 class="side-title">\u65b0\u7740\u30d4\u30c3\u30af\u30a2\u30c3\u30d7</h2>
        </div>
        <div id="new-list" class="new-list"></div>
      </aside>
    </section>

    {admin_board_html}

    <!-- ── Ranking ── -->
    <section class="glass-panel content animate-in delay-3" id="ranking-section">
      <div class="content-head">
        <h2 class="content-title">
          <em class="section-icon" id="ranking-icon">▶</em><span id="ranking-label">Shorts \u30e9\u30f3\u30ad\u30f3\u30b0</span>
        </h2>
        <div class="filter-row">
          <div class="type-tabs" id="type-tabs"></div>
          <div class="filter-divider"></div>
          <div class="period-tabs" id="period-tabs"></div>
        </div>
      </div>
      <div class="page-tabs" id="page-tabs-top"></div>
      <div id="period-root"></div>
      <div class="page-tabs bottom" id="page-tabs-bottom"></div>
    </section>

    <!-- ── Back to top ── -->
    <div class="back-to-top animate-in">
      <a id="back-to-top" href="#top"><em class="arrow-up">\u2191</em>TOP\u3078</a>
    </div>

    <footer class="footer animate-in">
      <div class="footer-links">
        <a href="/policy">\u30d7\u30e9\u30a4\u30d0\u30b7\u30fc\u30dd\u30ea\u30b7\u30fc</a>
        <a href="#">\u5229\u7528\u898f\u7d04</a>
        <a href="#">\u304a\u554f\u3044\u5408\u308f\u305b</a>
      </div>
      <span>VCLIP | VTuber\u5207\u308a\u629c\u304d\u30e9\u30f3\u30ad\u30f3\u30b0 &copy; 2026</span>
    </footer>
  </main>
  <div id="player-modal" class="player-modal" aria-hidden="true">
    <div class="player-sheet" role="dialog" aria-modal="true" aria-label="\u52d5\u753b\u30d7\u30ec\u30a4\u30e4\u30fc">
      <div class="player-topbar">
        <div class="player-controls" role="group" aria-label="\u30d7\u30ec\u30fc\u30e4\u30fc\u8868\u793a\u5207\u66ff">
          <button id="player-mode-portrait" class="player-toggle active" type="button" aria-label="\u7e26\u8868\u793a">\u7e26</button>
          <button id="player-mode-landscape" class="player-toggle" type="button" aria-label="\u6a2a\u8868\u793a">\u6a2a</button>
        </div>
        <button id="player-close" class="player-close" type="button" aria-label="\u9589\u3058\u308b">\u00d7</button>
      </div>
      <div class="player-frame">
        <iframe id="player-iframe" src="" title="YouTube player" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
      </div>
    </div>
  </div>
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
    const playerModal = document.getElementById("player-modal");
    const playerSheet = playerModal.querySelector(".player-sheet");
    const playerFrame = playerModal.querySelector(".player-frame");
    const playerIframe = document.getElementById("player-iframe");
    const playerClose = document.getElementById("player-close");
    const playerModePortrait = document.getElementById("player-mode-portrait");
    const playerModeLandscape = document.getElementById("player-mode-landscape");
    let activePeriod = "{first_period}";
    let activeContentType = "shorts";
    const PAGE_SIZE = 20;
    const MOBILE_BREAKPOINT = 760;
    const pageState = {{}};
    const typeConfig = {{
      shorts: {{ icon: "▶", label: "Shorts \u30e9\u30f3\u30ad\u30f3\u30b0" }},
      video:  {{ icon: "▦", label: "\u52d5\u753b\u30e9\u30f3\u30ad\u30f3\u30b0" }}
    }};

    function setPlayerLayout(layout) {{
      const normalized = layout === "landscape" ? "landscape" : "portrait";
      const isLandscape = normalized === "landscape";
      playerSheet.classList.toggle("landscape", isLandscape);
      playerFrame.classList.toggle("landscape", isLandscape);
      playerModePortrait.classList.toggle("active", !isLandscape);
      playerModeLandscape.classList.toggle("active", isLandscape);
    }}
    function openPlayer(videoId, layout) {{
      setPlayerLayout(layout);
      playerIframe.src = `https://www.youtube.com/embed/${{videoId}}?autoplay=1&playsinline=1`;
      playerModal.classList.add("open");
      playerModal.setAttribute("aria-hidden", "false");
    }}
    function closePlayer() {{
      playerModal.classList.remove("open");
      playerModal.setAttribute("aria-hidden", "true");
      setPlayerLayout("portrait");
      playerIframe.src = "";
    }}

    function resolvePlayerLayout(trigger) {{
      const contentType = (trigger.dataset.contentType || "").toLowerCase();
      if (contentType !== "video") return "portrait";
      const card = trigger.closest(".card");
      const thumbImg = card ? card.querySelector(".thumb img") : null;
      if (!thumbImg) return "portrait";
      const w = thumbImg.naturalWidth || thumbImg.clientWidth || 0;
      const h = thumbImg.naturalHeight || thumbImg.clientHeight || 0;
      if (h <= 0) return "portrait";
      return w / h >= 1.2 ? "landscape" : "portrait";
    }}

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
    function applyPagination() {{
      const cards = getCurrentCards();
      const isMobile = window.innerWidth <= MOBILE_BREAKPOINT;
      if (!isMobile) {{
        cards.forEach(card => {{ card.style.display = ""; }});
        [pageTabsTop, pageTabsBottom].forEach(container => {{
          container.innerHTML = "";
          container.style.display = "none";
        }});
        return;
      }}

      const totalPages = Math.max(1, Math.ceil(cards.length / PAGE_SIZE));
      const key = paginationKey();
      let currentPage = pageState[key] || 1;
      if (currentPage > totalPages) currentPage = totalPages;
      if (currentPage < 1) currentPage = 1;
      pageState[key] = currentPage;
      const start = (currentPage - 1) * PAGE_SIZE;
      const end = start + PAGE_SIZE;
      cards.forEach((card, i) => {{
        card.style.display = (i >= start && i < end) ? "" : "none";
      }});
      renderPageTabs(totalPages, currentPage, cards.length);
    }}
    function renderPageTabs(totalPages, currentPage, totalItems) {{
      [pageTabsTop, pageTabsBottom].forEach(container => {{
        container.innerHTML = "";
        container.style.display = "flex";
        if (totalPages <= 1) return;
        for (let p = 1; p <= totalPages; p++) {{
          const s = (p - 1) * PAGE_SIZE + 1;
          const e = Math.min(p * PAGE_SIZE, totalItems);
          const btn = document.createElement("button");
          btn.className = "page-tab" + (p === currentPage ? " active" : "");
          btn.textContent = `${{s}}\u4f4d-${{e}}\u4f4d`;
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
        <div class="stat-item"><span class="stat-value">${{tracking.toLocaleString("ja-JP")}}</span><span class="stat-label">トラッキング動画数</span></div>
        <div class="stat-item"><span class="stat-value">${{growth.toLocaleString("ja-JP")}}</span><span class="stat-label">本日の総再生増加</span></div>
        <div class="stat-item"><span class="stat-value">${{fresh.toLocaleString("ja-JP")}}</span><span class="stat-label">新着（24h）</span></div>
      `;
    }}
    /* ── Build NEW picks from payload ── */
    function buildNewPicks() {{
      const listEl = document.getElementById("new-list");
      if (!listEl || !payload.length) return;
      const daily = payload.find(p => p.table === "daily");
      if (!daily || !daily.groups || !daily.groups["all"]) return;

      const tmpDiv = document.createElement("div");
      tmpDiv.innerHTML = daily.groups["all"];
      const cards = Array.from(tmpDiv.querySelectorAll(".card")).filter((card) => card.querySelector(".new-badge"));

      const dedup = new Map();
      cards.forEach((card) => {{
        const titleEl = card.querySelector(".card-title");
        const rankEl = card.querySelector(".rank-badge");
        if (!titleEl) return;
        const href = titleEl.href || "#";
        if (dedup.has(href)) return;
        dedup.set(href, {{
          rank: rankEl ? "#" + rankEl.textContent.trim() : "",
          text: titleEl.textContent.trim(),
          href,
        }});
      }});

      const pool = Array.from(dedup.values());
      for (let i = pool.length - 1; i > 0; i--) {{
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }}
      const picks = pool.slice(0, 4);
      if (!picks.length) {{
        listEl.innerHTML = '<div style="color:var(--text-dim);font-size:0.85rem;">新着動画はまだありません</div>';
        return;
      }}
      listEl.innerHTML = "";
      picks.forEach((pick, i) => {{
        const item = document.createElement("a");
        item.className = "new-item animate-in";
        item.style.animationDelay = `${{0.3 + i * 0.1}}s`;
        item.href = pick.href;
        item.target = "_blank";
        item.rel = "noreferrer";
        item.innerHTML = `<span class="new-badge">NEW ${{pick.rank}}</span><p class="new-text"></p>`;
        item.querySelector(".new-text").textContent = pick.text;
        listEl.appendChild(item);
      }});
    }}
    /* ── Main render ── */
    function render() {{
      // Type tabs (Shorts / 動画)
      typeTabs.innerHTML = "";
      ["shorts", "video"].forEach(type => {{
        const btn = document.createElement("button");
        btn.className = "type-tab" + (type === activeContentType ? " active" : "");
        btn.textContent = type === "shorts" ? "Shorts" : "\u52d5\u753b";
        btn.type = "button";
        btn.addEventListener("click", () => {{
          activeContentType = type;
          rankingIcon.textContent = typeConfig[type].icon;
          rankingLabel.textContent = typeConfig[type].label;
          render();
        }});
        typeTabs.appendChild(btn);
      }});

      // Period tabs (24h / 7d / 30d)
      periodTabs.innerHTML = "";
      periodRoot.innerHTML = "";
      payload.forEach(period => {{
        const btn = document.createElement("button");
        btn.className = "period-tab" + (period.table === activePeriod ? " active" : "");
        btn.textContent = period.label;
        btn.type = "button";
        btn.addEventListener("click", () => {{
          activePeriod = period.table;
          render();
        }});
        periodTabs.appendChild(btn);

        const panel = document.createElement("section");
        panel.className = "period-panel" + (period.table === activePeriod ? " active" : "");
        panel.dataset.period = period.table;

        const groupsForRender = showAdminMeta ? period.available_groups : ["all"];
        const defaultGroup = groupsForRender[0];

        let groupTabsHtml = "";
        if (showAdminMeta && groupsForRender.length > 1) {{
          groupTabsHtml = '<div class="type-tabs" style="margin-bottom:12px;" id="group-tabs-' + period.table + '"></div>';
        }}

        panel.innerHTML = groupTabsHtml + '<div class="group-root"></div>';
        const groupRoot = panel.querySelector(".group-root");

        groupsForRender.forEach(groupName => {{
          const groupPanel = document.createElement("div");
          groupPanel.className = "group-panel" + (groupName === defaultGroup ? " active" : "");
          groupPanel.dataset.group = groupName;
          groupPanel.innerHTML = period.groups[groupName];
          groupRoot.appendChild(groupPanel);
        }});

        // Group tabs event
        if (showAdminMeta && groupsForRender.length > 1) {{
          const gTabs = panel.querySelector("#group-tabs-" + period.table);
          if (gTabs) {{
            groupsForRender.forEach(groupName => {{
              const gBtn = document.createElement("button");
              gBtn.className = "type-tab" + (groupName === defaultGroup ? " active" : "");
              gBtn.textContent = groupLabels[groupName] || groupName;
              gBtn.type = "button";
              gBtn.addEventListener("click", () => {{
                gTabs.querySelectorAll(".type-tab").forEach(b => b.classList.remove("active"));
                groupRoot.querySelectorAll(".group-panel").forEach(p => p.classList.remove("active"));
                gBtn.classList.add("active");
                groupRoot.querySelector(`[data-group="${{groupName}}"]`).classList.add("active");
                applyPagination();
              }});
              gTabs.appendChild(gBtn);
            }});
          }}
        }}

        periodRoot.appendChild(panel);
      }});

      // Show correct content type panels
      periodRoot.querySelectorAll(".content-panel").forEach(panel => {{
        panel.classList.toggle("active", panel.dataset.contentPanel === activeContentType);
      }});


      applyPagination();
    }}

    // Player modal event delegation
    periodRoot.addEventListener("click", (event) => {{
      const trigger = event.target.closest(".thumb, .card-title");
      if (!trigger || !trigger.dataset.videoId) return;
      event.preventDefault();
      openPlayer(trigger.dataset.videoId, resolvePlayerLayout(trigger));
    }});
    playerModePortrait.addEventListener("click", () => setPlayerLayout("portrait"));
    playerModeLandscape.addEventListener("click", () => setPlayerLayout("landscape"));
    playerClose.addEventListener("click", closePlayer);
    playerModal.addEventListener("click", (event) => {{
      if (event.target === playerModal) closePlayer();
    }});
    if (backToTop) {{
      backToTop.addEventListener("click", (event) => {{
        event.preventDefault();
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }});
    }}
    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && playerModal.classList.contains("open")) closePlayer();
    }});
    window.addEventListener("resize", () => {{ applyPagination(); }});

    buildHeroStats();
    buildNewPicks();
    render();
  </script>
</body>
</html>
"""


class TestSiteHandler(BaseHTTPRequestHandler):
    def _request_base_url(self) -> str:
        configured = _normalize_base_url(SITE_BASE_URL)
        if configured:
            return configured

        forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",", 1)[0].strip()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else "http"
        host = (self.headers.get("Host") or f"{HOST}:{PORT}").strip()
        return _normalize_base_url(f"{scheme}://{host}")

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path_only = parsed.path
        query = parse_qs(parsed.query)

        if path_only == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", "/assets/ueno-icon.jpg")
            self.end_headers()
            return
        if path_only == "/assets/noto-sans-jp.ttf":
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return

        if path_only == "/assets/ueno-icon.jpg":
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
            self.send_header("Content-Type", "image/jpeg")
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
            self.send_header("Location", "/assets/ueno-icon.jpg")
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

        if path_only == "/assets/ueno-icon.jpg":
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
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
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

