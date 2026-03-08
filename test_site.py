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

from config import GROUP_KEYWORDS
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

SITE_TITLE = "ぶいくりっぷ VTuber切り抜きランキング"
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

def _fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    jst = timezone(timedelta(hours=9))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(jst).strftime("%Y-%m-%d %H:%M")


def _infer_group(row: dict) -> str:
    current = (row.get("group_name") or "").strip()
    if current:
        for group_name in GROUP_ORDER:
            if group_name != "all" and current.lower() == group_name.lower():
                return group_name
        return current

    haystack = " ".join(
        [
            row.get("title", ""),
            row.get("tags_text", ""),
            row.get("channel_name", ""),
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


def _share_prefix_for_period(period_key: str, month_day: str, rank: int, content_label: str) -> str:
    if period_key == "daily":
        return f"本日({month_day})の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    if period_key == "weekly":
        return f"直近7日間の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    if period_key == "monthly":
        return f"直近30日間の #VTuber切り抜きランキング {rank}位の{content_label}です！"
    return f"#VTuber切り抜きランキング {rank}位の{content_label}です！"


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
        title = html.escape(row["title"])
        channel_id = html.escape(row["channel_id"])
        channel_name = html.escape(row["channel_name"])
        channel_icon_url = html.escape(row.get("channel_icon_url") or "")
        group_name = html.escape(_infer_group(row))
        published_at = _fmt_datetime(row["published_at"])
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        title_plain = " ".join(str(row.get("title") or "").split())
        share_title = _truncate_text(title_plain, 56)
        share_prefix = _share_prefix_for_period(period_key, month_day, row["rank"], content_label)
        share_text = f"{share_prefix}  {share_title}"
        share_url = (
            "https://twitter.com/intent/tweet?text="
            f"{quote(share_text, safe='')}&url={quote(video_url, safe='')}"
        )
        class_name = "video-card" if not card_class else f"video-card {card_class}"
        content_type = html.escape((row.get("content_type") or "").lower())
        new_badge_html = '<span class="new-badge">NEW</span>' if row.get("is_new") else ""
        group_pill_html = f'<span class="pill">{group_name}</span>' if show_group else ""
        cards.append(
            f"""
            <article class="{class_name}">
              <button class="thumb thumb-play" type="button" data-video-id="{video_id}" data-video-title="{title}" data-content-type="{content_type}" aria-label="{title} を再生">
                <img src="{_thumbnail_url(video_id)}" alt="{title}" loading="lazy">
                <span class="rank-badge">#{row['rank']}</span>
                {new_badge_html}
              </button>
              <div class="video-body">
                <button class="video-title video-play" type="button" data-video-id="{video_id}" data-video-title="{title}" data-content-type="{content_type}">{title}</button>
                <div class="meta-row">
                  <a class="channel-link" href="{channel_url}" target="_blank" rel="noreferrer">
                    <img class="channel-icon" src="{channel_icon_url}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.nextElementSibling.style.display='inline-flex';">
                    <span class="channel-icon-fallback" style="display:none;">ch</span>
                    <span class="channel-name">{channel_name}</span>
                  </a>
                  {group_pill_html}
                </div>
                <div class="meta-row compact stats-row">
                  <span>再生数 +{row['view_growth']:,}</span>
                  <span>{published_at}</span>
                </div>
                <div class="meta-row compact action-row">
                  <a class="watch-link" href="{video_url}" target="_blank" rel="noreferrer">YouTubeで開く</a>
                  <a class="share-link" href="{share_url}" target="_blank" rel="noreferrer">SNSでシェア</a>
                </div>
              </div>
            </article>
            """
        )
    return "".join(cards)
def _render_rank_sections(rows: list[dict], show_group: bool = True, period_key: str = "daily", content_label: str = "shorts") -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    feature_rows = rows[:3]
    rest_rows = rows[3:]

    feature_html = _render_cards(feature_rows, "feature-card", show_group=show_group, period_key=period_key, content_label=content_label)
    rest_html = _render_cards(rest_rows, show_group=show_group, period_key=period_key, content_label=content_label)
    return f"""
    <section class="ranking-list">
      <h3>上位3件</h3>
      <div class="feature-grid">{feature_html}</div>
    </section>
    <section class="ranking-list">
      <h3>4位以下</h3>
      <div class="rest-grid">{rest_html}</div>
    </section>
    """


def _render_group_content(
    shorts_rows: list[dict],
    video_rows: list[dict],
    show_group: bool = True,
    period_key: str = "daily",
    content_label: str = "shorts",
) -> str:
    if not shorts_rows and not video_rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    default_tab = "shorts" if shorts_rows else "video"
    shorts_html = (
        _render_rank_sections(shorts_rows, show_group=show_group, period_key=period_key, content_label="shorts")
        if shorts_rows
        else '<div class="empty">Shortsに該当する動画はありません。</div>'
    )
    video_html = (
        _render_rank_sections(video_rows, show_group=show_group, period_key=period_key, content_label="動画")
        if video_rows
        else '<div class="empty">動画に該当する動画はありません。</div>'
    )

    shorts_active = " active" if default_tab == "shorts" else ""
    video_active = " active" if default_tab == "video" else ""

    return f"""
    <div class="content-tabs">
      <button class="tab-button content-tab-button{shorts_active}" type="button" data-content-target="shorts">Shorts</button>
      <button class="tab-button content-tab-button{video_active}" type="button" data-content-target="video">動画</button>
    </div>
    <div class="content-panel{shorts_active}" data-content-panel="shorts">{shorts_html}</div>
    <div class="content-panel{video_active}" data-content-panel="video">{video_html}</div>
    """


def _build_period_payload(is_admin: bool = False) -> list[dict]:
    payload = []
    for period_key, label, shorts_table, video_table in PERIODS:
        shorts_calculated_at, shorts_rows = _fetch_latest_rankings(shorts_table)
        video_calculated_at, video_rows = _fetch_latest_rankings(video_table)

        grouped_shorts: dict[str, list[dict]] = defaultdict(list)
        grouped_video: dict[str, list[dict]] = defaultdict(list)
        grouped_shorts["all"] = shorts_rows
        grouped_video["all"] = video_rows

        for row in shorts_rows:
            grouped_shorts[_infer_group(row)].append(row)
        for row in video_rows:
            grouped_video[_infer_group(row)].append(row)

        available_groups = [
            group_name
            for group_name in GROUP_ORDER
            if grouped_shorts.get(group_name) or grouped_video.get(group_name)
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
    normalized_base_url = _normalize_base_url(base_url)
    head_meta = _build_head_meta(normalized_base_url, is_admin=is_admin)
    show_admin_meta = "true" if is_admin else "false"
    admin_html = ""
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
        admin_html = f"""
          <div class="admin-quota">
            <span class="admin-pill {status_class}">API状態: {status_label}</span>
            <span class="admin-metrics">search.list {used:,} / {limit:,}</span>
          </div>
        """

    return f"""<!doctype html>
<html lang="ja">
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
      --bg: #0c1217;
      --panel: rgba(12, 18, 23, 0.84);
      --panel-strong: rgba(16, 24, 31, 0.96);
      --ink: #eff6fb;
      --accent: #f4b942;
      --accent-strong: #ff8a3d;
      --accent-cool: #63d0ff;
      --line: rgba(239, 246, 251, 0.12);
      --muted: #9fb2c1;
      --shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans JP Local", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(99, 208, 255, 0.16), transparent 24%),
        radial-gradient(circle at top right, rgba(244, 185, 66, 0.16), transparent 22%),
        linear-gradient(160deg, #081017 0%, #121b24 52%, #0b1218 100%);
    }}
    .shell {{
      width: min(1320px, calc(100% - 24px));
      margin: 24px auto 48px;
    }}
    .back-top-wrap {{
      text-align: center;
      margin-top: 16px;
    }}
    .back-top-link {{
      color: var(--accent-cool);
      text-decoration: underline;
      text-underline-offset: 2px;
      font-size: 0.9rem;
    }}

    .site-footer {{
      text-align: center;
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.02em;
      margin-top: 18px;
      opacity: 0.9;
    }}
    .footer-policy-link {{
      color: var(--accent-cool);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .hero, .panel {{
      border: 1px solid var(--line);
      background: #101821;
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 28px;
    }}
    .hero-logo-wrap {{
      display: flex;
      justify-content: center;
      width: 100%;
      margin: 2px 0 10px;
      padding: 0;
      pointer-events: none;
    }}
    .hero-logo {{
      width: min(100%, 640px);
      max-height: 150px;
      object-fit: contain;
      opacity: 0.92;
      filter: none;
    }}
    h1, h2, h3, p {{
      margin: 0;
    }}
    .hero-copy {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .hero-copy > div {{
      flex: 1 1 640px;
      min-width: 0;
    }}
    .hero-copy p {{
      color: var(--muted);
      margin-top: 10px;
      max-width: 760px;
    }}
    .admin-quota {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-size: 0.88rem;
      color: var(--muted);
    }}
    .admin-pill {{
      border-radius: 999px;
      padding: 4px 10px;
      font-weight: 700;
      color: #081017;
    }}
    .admin-pill.ok {{
      background: #5ee0b0;
    }}
    .admin-pill.warn {{
      background: #f4b942;
    }}
    .admin-pill.danger {{
      background: #ff7c7c;
    }}
    .admin-pill.muted {{
      background: #9fb2c1;
    }}
    .period-tabs, .group-tabs {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .period-tabs {{
      margin-top: 20px;
    }}
    .tab-button {{
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      padding: 10px 15px;
      border-radius: 999px;
      cursor: pointer;
      font: inherit;
    }}
    .tab-button.active {{
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      border-color: transparent;
      color: #081017;
      font-weight: 700;
    }}
    .content-tabs {{
      display: flex;
      gap: 8px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }}
    .content-tab-button {{
      padding: 7px 12px;
      font-size: 0.86rem;
    }}
    .content-panel {{
      display: none;
    }}
    .content-panel.active {{
      display: block;
    }}
    .period-panel {{
      display: none;
      margin-top: 18px;
      padding: 22px;
      background: var(--panel-strong);
    }}
    .period-panel.active {{
      display: block;
    }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }}
    .panel-head p {{
      color: var(--muted);
      margin-top: 6px;
    }}
    .group-panel {{
      display: none;
      margin-top: 18px;
    }}
    .group-panel.active {{
      display: block;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
      gap: 16px;
    }}
    .feature-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 22px;
    }}
    .rest-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
    }}
    .video-card {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
      overflow: hidden;
      position: relative;
    }}
    .feature-card {{
      border-color: rgba(244, 185, 66, 0.5);
      box-shadow: 0 18px 40px rgba(244, 185, 66, 0.14);
    }}
    .feature-card .thumb {{
      aspect-ratio: 16 / 9;
      background: #0a0f13;
    }}
    .feature-card .thumb img {{
      object-fit: cover;
      background: #0a0f13;
    }}
    .feature-card .video-body {{
      padding: 15px 18px 17px;
    }}
    .feature-card .video-title {{
      font-size: 1.08rem;
      min-height: 4.2em;
      line-height: 1.38;
      -webkit-line-clamp: 3;
    }}
    .feature-card .meta-row.compact {{
      font-size: 0.84rem;
    }}
    .feature-card .rank-badge {{
      top: 12px;
      left: 12px;
      padding: 8px 11px;
      font-size: 0.9rem;
      background: rgba(8, 16, 23, 0.88);
      color: var(--accent);
      font-weight: 800;
    }}
    .video-card .thumb {{
      aspect-ratio: 16 / 9;
    }}
    .rest-grid .video-card .video-body {{
      padding: 12px;
    }}
    .rest-grid .video-card .video-title {{
      min-height: 3.8em;
      font-size: 0.96rem;
    }}
    .rest-grid .video-card .meta-row {{
      font-size: 0.84rem;
    }}
    .ranking-list h3 {{
      margin-bottom: 14px;
      color: var(--accent-cool);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.82rem;
    }}
    .thumb {{
      position: relative;
      display: block;
      aspect-ratio: 16 / 9;
      background: #ded4c3;
      border: 0;
      padding: 0;
      width: 100%;
      cursor: pointer;
    }}
    .thumb img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .rank-badge {{
      position: absolute;
      top: 10px;
      left: 10px;
      padding: 7px 11px;
      border-radius: 999px;
      background: rgba(29, 42, 51, 0.8);
      color: #fff;
      font-size: 0.95rem;
    }}
    .new-badge {{
      position: absolute;
      top: 10px;
      right: 10px;
      padding: 6px 9px;
      border-radius: 999px;
      background: linear-gradient(135deg, #ff7a18, #ff3d54);
      color: #fff;
      font-size: 0.75rem;
      letter-spacing: 0.04em;
      font-weight: 800;
      box-shadow: 0 8px 18px rgba(255, 61, 84, 0.35);
    }}
    .video-body {{
      padding: 14px;
      display: flex;
      flex-direction: column;
    }}
    .video-title {{
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
      overflow: hidden;
      color: var(--ink);
      text-decoration: none;
      line-height: 1.45;
      min-height: 4.2em;
      font-weight: 700;
    }}
    .video-play {{
      background: transparent;
      border: 0;
      padding: 0;
      width: 100%;
      text-align: left;
      cursor: pointer;
      font: inherit;
    }}
    .watch-link {{
      color: var(--muted);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .share-link {{
      color: var(--accent-cool);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .action-row {{
      justify-content: flex-start;
      gap: 14px;
    }}
    .player-modal {{
      position: fixed;
      inset: 0;
      background: rgba(2, 6, 10, 0.84);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 14px;
    }}
    .player-modal.open {{
      display: flex;
    }}
    .player-sheet {{
      width: min(94vw, 460px);
      background: #0a131a;
      border: 1px solid var(--line);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.55);
    }}
    .player-sheet.landscape {{
      width: min(96vw, 980px);
    }}
    .player-topbar {{
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 6px 8px;
      border-bottom: 1px solid rgba(231, 242, 251, 0.12);
      background: rgba(10, 19, 26, 0.92);
    }}
    .player-controls {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .player-toggle {{
      min-width: 38px;
      height: 30px;
      border-radius: 999px;
      border: 1px solid rgba(231, 242, 251, 0.24);
      background: rgba(255, 255, 255, 0.04);
      color: #c9d8e5;
      font-size: 0.78rem;
      cursor: pointer;
      font: inherit;
      padding: 0 10px;
    }}
    .player-toggle.active {{
      background: rgba(99, 208, 255, 0.2);
      border-color: rgba(99, 208, 255, 0.72);
      color: #e9f7ff;
      font-weight: 700;
    }}
    .player-close {{
      position: static;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: rgba(10, 19, 26, 0.82);
      color: #e7f2fb;
      border: 1px solid rgba(231, 242, 251, 0.28);
      font-size: 1.15rem;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font: inherit;
    }}
    .player-frame {{
      position: relative;
      width: 100%;
      aspect-ratio: 9 / 16;
      background: #000;
    }}
    .player-frame.landscape {{
      aspect-ratio: 16 / 9;
    }}
    .player-frame iframe {{
      width: 100%;
      height: 100%;
      border: 0;
    }}
    .channel-link {{
      color: var(--accent-cool);
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex: 1;
      max-width: calc(100% - 84px);
    }}
    .channel-name {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .channel-icon {{
      width: 22px;
      height: 22px;
      border-radius: 50%;
      object-fit: cover;
      flex: 0 0 22px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.08);
    }}
    .channel-icon-fallback {{
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.64rem;
      line-height: 1;
      align-items: center;
      justify-content: center;
      flex: 0 0 22px;
      text-transform: uppercase;
    }}
    .meta-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .meta-row.compact {{
      font-size: 0.86rem;
    }}
    .stats-row {{
      margin-top: auto;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      white-space: nowrap;
      color: var(--ink);
    }}
    .empty {{
      padding: 20px;
      border: 1px dashed var(--line);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.03);
    }}
    .mobile-pager {{
      display: none;
      align-items: center;
      gap: 8px;
      margin: 10px 0 12px;
      overflow-x: auto;
      white-space: nowrap;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: thin;
    }}
    .mobile-pager button {{
      flex: 0 0 auto;
      min-width: 90px;
      padding: 6px 10px;
      font-size: 0.78rem;
    }}
    @media (max-width: 1024px) {{
      .feature-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .rest-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 640px) {{
      .shell {{
        width: calc(100% - 12px);
        margin: 8px auto 22px;
      }}
      .hero, .period-panel {{
        padding: 12px;
      }}
      .hero-copy {{
        align-items: flex-start;
        gap: 10px;
      }}
      .hero-logo-wrap {{
        margin: 2px 0 8px;
        padding: 0;
      }}
      .hero-logo {{
        width: min(100%, 500px);
        max-height: 112px;
      }}
      .hero-copy h1 {{
        font-size: 1.16rem;
        line-height: 1.3;
      }}
      .hero-copy p {{
        margin-top: 6px;
        font-size: 0.86rem;
      }}
      .period-tabs,
      .group-tabs,
      .content-tabs {{
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: thin;
        -webkit-overflow-scrolling: touch;
        padding-bottom: 4px;
      }}
      .tab-button {{
        flex: 0 0 auto;
        padding: 8px 12px;
        font-size: 0.84rem;
      }}
      .content-tab-button {{
        font-size: 0.8rem;
        padding: 6px 10px;
      }}
      .panel-head {{
        margin-bottom: 12px;
      }}
      .panel-head h2 {{
        font-size: 1.04rem;
      }}
      .panel-head p {{
        margin-top: 4px;
        font-size: 0.82rem;
      }}
      .feature-grid,
      .rest-grid {{
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .feature-card .video-body,
      .rest-grid .video-card .video-body {{
        padding: 10px 11px 12px;
      }}
      .feature-card .video-title,
      .rest-grid .video-card .video-title,
      .video-title {{
        min-height: auto;
        font-size: 0.95rem;
        line-height: 1.36;
      }}
      .meta-row {{
        font-size: 0.83rem;
        gap: 6px;
      }}
      .stats-row {{
        margin-top: 8px;
      }}
      .channel-link {{
        max-width: calc(100% - 70px);
      }}
      .rank-badge {{
        font-size: 0.9rem;
        padding: 6px 10px;
      }}
      .new-badge {{
        font-size: 0.7rem;
        padding: 5px 8px;
      }}
      .player-modal {{
        align-items: flex-end;
        padding: 4px;
      }}
      .player-sheet {{
        width: 100%;
        max-height: calc(100dvh - 8px);
      }}
      .player-sheet.landscape {{
        width: 100%;
      }}
      .player-topbar {{
        height: 40px;
        padding: 4px 6px;
      }}
      .player-toggle {{
        min-width: 34px;
        height: 28px;
        font-size: 0.75rem;
        padding: 0 8px;
      }}
      .player-close {{
        width: 30px;
        height: 30px;
      }}
      .player-frame {{
        max-height: calc(100dvh - 130px);
      }}
      .mobile-pager {{
        display: flex;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-copy">
        <div>
          {logo_html}
          <h1>ぶいくりっぷ Vtuber切り抜きランキング</h1>
          <p>テスト運用中です。。。</p>
        </div>
        {admin_html}
      </div>
      <div class="period-tabs" id="period-tabs"></div>
    </section>
    <div id="period-root"></div>
    <div class="back-top-wrap"><a id="back-to-top" class="back-top-link" href="#">TOPへ</a></div>
    <footer class="site-footer">Copyright (C) 2026- 3vskhv0 All Rights Reserved. <span>|</span> <a class="footer-policy-link" href="/policy">プライバシーポリシー</a></footer>
  </main>
  <div id="player-modal" class="player-modal" aria-hidden="true">
    <div class="player-sheet" role="dialog" aria-modal="true" aria-label="動画プレイヤー">
      <div class="player-topbar">
        <div class="player-controls" role="group" aria-label="プレーヤー表示切替">
          <button id="player-mode-portrait" class="player-toggle active" type="button" aria-label="縦表示">縦</button>
          <button id="player-mode-landscape" class="player-toggle" type="button" aria-label="横表示">横</button>
        </div>
        <button id="player-close" class="player-close" type="button" aria-label="閉じる">×</button>
      </div>
      <div class="player-frame">
        <iframe id="player-iframe" src="" title="YouTube player" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
      </div>
    </div>
  </div>
  <script>
    const payload = {payload_json};
    const groupLabels = {group_labels_json};
    const showAdminMeta = {show_admin_meta};
    const periodTabs = document.getElementById("period-tabs");
    const periodRoot = document.getElementById("period-root");
    const backToTop = document.getElementById("back-to-top");
    const playerModal = document.getElementById("player-modal");
    const playerSheet = playerModal.querySelector(".player-sheet");
    const playerFrame = playerModal.querySelector(".player-frame");
    const playerIframe = document.getElementById("player-iframe");
    const playerClose = document.getElementById("player-close");
    const playerModePortrait = document.getElementById("player-mode-portrait");
    const playerModeLandscape = document.getElementById("player-mode-landscape");
    let activePeriod = "{first_period}";
    const MOBILE_PAGE_SIZE = 20;
    const pageState = {{}};

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

    function isMobileViewport() {{
      return window.matchMedia("(max-width: 640px)").matches;
    }}

    function contentPanelKey(contentPanel) {{
      const periodPanel = contentPanel.closest(".period-panel");
      const groupPanel = contentPanel.closest(".group-panel");
      const periodKey = periodPanel ? (periodPanel.dataset.period || "") : "";
      const groupKey = groupPanel ? (groupPanel.dataset.group || "") : "";
      const contentKey = contentPanel.dataset.contentPanel || "";
      return `${{periodKey}}::${{groupKey}}::${{contentKey}}`;
    }}

    function makePager(totalPages, currentPage, pageSize, totalItems, onPageChange) {{
      const pager = document.createElement("div");
      pager.className = "mobile-pager";

      for (let page = 1; page <= totalPages; page += 1) {{
        const startRank = (page - 1) * pageSize + 1;
        const endRank = Math.min(page * pageSize, totalItems);

        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = "tab-button" + (page === currentPage ? " active" : "");
        tab.textContent = `${{startRank}}-${{endRank}}位`;
        tab.addEventListener("click", () => onPageChange(page));
        pager.appendChild(tab);
      }}

      return pager;
    }}
    function applyMobilePagination(scope) {{
      const root = scope || periodRoot;
      const panels = root.querySelectorAll(".content-panel");

      for (const contentPanel of panels) {{
        for (const existingPager of contentPanel.querySelectorAll(".mobile-pager")) {{
          existingPager.remove();
        }}

        const cards = Array.from(contentPanel.querySelectorAll(".video-card"));
        if (!cards.length) {{
          continue;
        }}

        if (!isMobileViewport()) {{
          cards.forEach((card) => {{
            card.style.display = "";
          }});
          continue;
        }}

        const totalPages = Math.ceil(cards.length / MOBILE_PAGE_SIZE);
        if (totalPages <= 1) {{
          cards.forEach((card) => {{
            card.style.display = "";
          }});
          continue;
        }}

        const key = contentPanelKey(contentPanel);
        let currentPage = pageState[key] || 1;
        if (currentPage < 1) {{
          currentPage = 1;
        }}
        if (currentPage > totalPages) {{
          currentPage = totalPages;
        }}
        pageState[key] = currentPage;

        const start = (currentPage - 1) * MOBILE_PAGE_SIZE;
        const end = start + MOBILE_PAGE_SIZE;

        cards.forEach((card, index) => {{
          card.style.display = index >= start && index < end ? "" : "none";
        }});

        const onPageChange = (nextPage) => {{
          pageState[key] = nextPage;
          applyMobilePagination(contentPanel.closest(".group-panel") || contentPanel);
          contentPanel.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }};

        const topPager = makePager(totalPages, currentPage, MOBILE_PAGE_SIZE, cards.length, onPageChange);
        const bottomPager = makePager(totalPages, currentPage, MOBILE_PAGE_SIZE, cards.length, onPageChange);
        contentPanel.prepend(topPager);
        contentPanel.appendChild(bottomPager);
      }}
    }}

    function render() {{
      periodTabs.innerHTML = "";
      periodRoot.innerHTML = "";

      payload.forEach((period) => {{
        const periodButton = document.createElement("button");
        periodButton.className = "tab-button" + (period.table === activePeriod ? " active" : "");
        periodButton.textContent = period.label;
        periodButton.type = "button";
        periodButton.onclick = () => {{
          activePeriod = period.table;
          render();
        }};
        periodTabs.appendChild(periodButton);

        const panel = document.createElement("section");
        panel.className = "panel period-panel" + (period.table === activePeriod ? " active" : "");
        panel.dataset.period = period.table;

        const groupsForRender = showAdminMeta ? period.available_groups : ["all"];
        const defaultGroup = groupsForRender[0];
        panel.innerHTML = `
          <div class="panel-head">
            <div>
              <h2>${{period.label}}ランキング</h2>
              ${{showAdminMeta ? `<p>集計時刻: ${{period.calculated_at}}</p>` : ""}}
            </div>
            ${{showAdminMeta ? '<div class="group-tabs"></div>' : ""}}
          </div>
          <div class="group-root"></div>
        `;

        const groupTabs = panel.querySelector(".group-tabs");
        const groupRoot = panel.querySelector(".group-root");

        groupsForRender.forEach((groupName) => {{
          if (groupTabs) {{
            const groupButton = document.createElement("button");
            groupButton.className = "tab-button" + (groupName === defaultGroup ? " active" : "");
            groupButton.textContent = groupLabels[groupName] || groupName;
            groupButton.type = "button";
            groupButton.onclick = () => {{
              for (const btn of groupTabs.querySelectorAll(".tab-button")) {{
                btn.classList.remove("active");
              }}
              for (const panelEl of groupRoot.querySelectorAll(".group-panel")) {{
                panelEl.classList.remove("active");
              }}
              groupButton.classList.add("active");
              groupRoot.querySelector(`[data-group="${{groupName}}"]`).classList.add("active");
            }};
            groupTabs.appendChild(groupButton);
          }}

          const groupPanel = document.createElement("div");
          groupPanel.className = "group-panel" + (groupName === defaultGroup ? " active" : "");
          groupPanel.dataset.group = groupName;
          groupPanel.innerHTML = period.groups[groupName];
          groupRoot.appendChild(groupPanel);
          applyMobilePagination(groupPanel);
        }});

        periodRoot.appendChild(panel);
      }});
      applyMobilePagination(periodRoot);
    }}

    function resolvePlayerLayout(trigger) {{
      const contentType = (trigger.dataset.contentType || "").toLowerCase();
      if (contentType !== "video") {{
        return "portrait";
      }}

      const card = trigger.closest(".video-card");
      const thumbImg = card ? card.querySelector(".thumb img") : null;
      if (!thumbImg) {{
        return "portrait";
      }}

      const width = thumbImg.naturalWidth || thumbImg.clientWidth || 0;
      const height = thumbImg.naturalHeight || thumbImg.clientHeight || 0;
      if (height <= 0) {{
        return "portrait";
      }}

      return width / height >= 1.2 ? "landscape" : "portrait";
    }}

    periodRoot.addEventListener("click", (event) => {{
      const contentTabButton = event.target.closest(".content-tab-button");
      if (contentTabButton) {{
        const contentTabs = contentTabButton.closest(".content-tabs");
        const groupPanel = contentTabButton.closest(".group-panel");
        if (!contentTabs || !groupPanel) {{
          return;
        }}
        const target = contentTabButton.dataset.contentTarget;
        for (const btn of contentTabs.querySelectorAll(".content-tab-button")) {{
          btn.classList.remove("active");
        }}
        for (const panelEl of groupPanel.querySelectorAll(".content-panel")) {{
          panelEl.classList.remove("active");
        }}
        contentTabButton.classList.add("active");
        const targetPanel = groupPanel.querySelector(`.content-panel[data-content-panel="${{target}}"]`);
        if (targetPanel) {{
          targetPanel.classList.add("active");
        }}
        applyMobilePagination(groupPanel);
        return;
      }}

      const trigger = event.target.closest(".thumb-play, .video-play");
      if (!trigger) {{
        return;
      }}
      event.preventDefault();
      openPlayer(trigger.dataset.videoId, resolvePlayerLayout(trigger));
    }});
    playerModePortrait.addEventListener("click", () => setPlayerLayout("portrait"));
    playerModeLandscape.addEventListener("click", () => setPlayerLayout("landscape"));
    playerClose.addEventListener("click", closePlayer);
    playerModal.addEventListener("click", (event) => {{
      if (event.target === playerModal) {{
        closePlayer();
      }}
    }});
    let resizeTimer = null;
    window.addEventListener("resize", () => {{
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => applyMobilePagination(periodRoot), 120);
    }});
    if (backToTop) {{
      backToTop.addEventListener("click", (event) => {{
        event.preventDefault();
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }});
    }}
    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && playerModal.classList.contains("open")) {{
        closePlayer();
      }}
    }});

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

        if path_only not in {"/", "/index.html"}:
            self.send_error(404, "Not found")
            return

        is_admin = False
        if ADMIN_TOKEN:
            token = (query.get("admin_token") or [""])[0]
            is_admin = token == ADMIN_TOKEN

        base_url = self._request_base_url()
        try:
            body = render_homepage(is_admin=is_admin, base_url=base_url).encode("utf-8")
        except Exception as exc:
            logger.exception("Failed to render test site")
            body = render_error_page(exc, base_url=base_url).encode("utf-8")
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






















