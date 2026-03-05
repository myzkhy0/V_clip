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

from config import GROUP_KEYWORDS, MIN_DURATION_SECONDS, SHORTS_MAX_SECONDS, SHORTS_TAG_KEYWORD
from db import fetchall

logger = logging.getLogger(__name__)

HOST = os.getenv("TEST_SITE_HOST", "127.0.0.1")
PORT = int(os.getenv("TEST_SITE_PORT", "8000"))
FONT_FILE = r"C:\Users\11bs0\OneDrive\デスクトップ\NotoSansJP-VariableFont_wght.ttf"
LOGO_FILE = os.getenv("TEST_SITE_LOGO_FILE", "").strip()
ADMIN_TOKEN = os.getenv("TEST_SITE_ADMIN_TOKEN", "")
YOUTUBE_DAILY_SEARCH_UNIT_LIMIT = int(os.getenv("YOUTUBE_DAILY_SEARCH_UNIT_LIMIT", "8000"))
YOUTUBE_QUOTA_STATE_FILE = os.getenv("YOUTUBE_QUOTA_STATE_FILE", ".youtube_quota_state.json")

PERIODS: list[tuple[str, str]] = [
    ("daily_ranking", "24時間"),
    ("weekly_ranking", "7日"),
    ("monthly_ranking", "30日"),
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
    "other": "その他",
}


def _fetch_latest_rankings(table: str) -> tuple[datetime | None, list[dict]]:
    latest_row = fetchall(
        f"""
        SELECT calculated_at
        FROM {table}
        ORDER BY calculated_at DESC
        LIMIT 1
        """
    )
    if not latest_row:
        return None, []

    calculated_at = latest_row[0]["calculated_at"]
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



def _infer_content_type(row: dict) -> str:
    """Infer shorts/video; fallback for old rows with default content_type."""
    stored = (row.get("content_type") or "").strip().lower()
    duration = int(row.get("duration_seconds") or 0)
    text = " ".join([row.get("title", ""), row.get("tags_text", "")]).lower()
    has_shorts_keyword = SHORTS_TAG_KEYWORD in text or " shorts" in text

    if duration >= MIN_DURATION_SECONDS and (
        duration <= SHORTS_MAX_SECONDS or has_shorts_keyword
    ):
        return "shorts"

    if stored == "shorts":
        return "shorts"
    return "video"

def _thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _truncate_text(value: str, max_len: int = 42) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _render_cards(rows: list[dict], card_class: str = "") -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    cards = []
    today = datetime.now()
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
        share_title = _truncate_text(title_plain, 36)
        share_text = (
            f"本日（{month_day}）のVTuber切り抜きランキング{row['rank']}位です！#ぶいくりっぷ"
            f"  {share_title}"
        )
        share_url = (
            "https://twitter.com/intent/tweet?text="
            f"{quote(share_text, safe='')}&url={quote(video_url, safe='')}"
        )
        class_name = "video-card" if not card_class else f"video-card {card_class}"
        cards.append(
            f"""
            <article class="{class_name}">
              <button class="thumb thumb-play" type="button" data-video-id="{video_id}" data-video-title="{title}" aria-label="{title} を再生">
                <img src="{_thumbnail_url(video_id)}" alt="{title}" loading="lazy">
                <span class="rank-badge">#{row['rank']}</span>
              </button>
              <div class="video-body">
                <button class="video-title video-play" type="button" data-video-id="{video_id}" data-video-title="{title}">{title}</button>
                <div class="meta-row">
                  <a class="channel-link" href="{channel_url}" target="_blank" rel="noreferrer">
                    <img class="channel-icon" src="{channel_icon_url}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.nextElementSibling.style.display='inline-flex';">
                    <span class="channel-icon-fallback" style="display:none;">ch</span>
                    <span class="channel-name">{channel_name}</span>
                  </a>
                  <span class="pill">{group_name}</span>
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
def _render_rank_sections(rows: list[dict]) -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    feature_rows = rows[:3]
    rest_rows = rows[3:]

    feature_html = _render_cards(feature_rows, "feature-card")
    rest_html = _render_cards(rest_rows)
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


def _render_group_content(rows: list[dict]) -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    shorts_rows = [r for r in rows if _infer_content_type(r) == "shorts"]
    video_rows = [r for r in rows if _infer_content_type(r) != "shorts"]

    default_tab = "shorts" if shorts_rows else "video"
    shorts_html = (
        _render_rank_sections(shorts_rows)
        if shorts_rows
        else '<div class="empty">Shortsに該当する動画はありません。</div>'
    )
    video_html = (
        _render_rank_sections(video_rows)
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

def _build_period_payload() -> list[dict]:
    payload = []
    for table, label in PERIODS:
        calculated_at, rows = _fetch_latest_rankings(table)
        grouped: dict[str, list[dict]] = defaultdict(list)
        grouped["all"] = rows
        for row in rows:
            grouped[_infer_group(row)].append(row)

        available_groups = [
            group_name
            for group_name in GROUP_ORDER
            if grouped.get(group_name)
        ]
        if not available_groups:
            available_groups = ["all"]

        payload.append(
            {
                "table": table,
                "label": label,
                "calculated_at": _fmt_datetime(calculated_at),
                "groups": {
                    group_name: _render_group_content(grouped.get(group_name, []))
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
def render_error_page(error: Exception) -> str:
    message = html.escape(str(error) or error.__class__.__name__)
    database_url = os.getenv("DATABASE_URL", "(not set)")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ぶいくりっぷ Vtuber切り抜きランキング</title>
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


def render_homepage(is_admin: bool = False) -> str:
    payload = _build_period_payload()
    first_period = payload[0]["table"] if payload else ""
    group_labels_json = json.dumps(GROUP_LABELS, ensure_ascii=False).replace("</", "<\\/")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    admin_html = ""
    logo_html = ""
    if LOGO_FILE and Path(LOGO_FILE).exists():
        logo_html = """
          <div class="hero-logo-wrap">
            <img class="hero-logo" src="/assets/site-logo.png" alt="ぶいくりっぷ ロゴ" loading="eager">
          </div>
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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ぶいくりっぷ Vtuber切り抜きランキング</title>
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
    .site-footer {{
      text-align: center;
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.02em;
      margin-top: 18px;
      opacity: 0.9;
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
      margin-bottom: 10px;
      pointer-events: none;
    }}
    .hero-logo {{
      width: min(100%, 420px);
      max-height: 150px;
      object-fit: contain;
      opacity: 0.68;
      filter: drop-shadow(0 4px 16px rgba(90, 145, 255, 0.28));
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
      font-weight: 600;
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
    .player-topbar {{
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding: 6px 8px;
      border-bottom: 1px solid rgba(231, 242, 251, 0.12);
      background: rgba(10, 19, 26, 0.92);
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
    @media (max-width: 640px) {{
      .shell {{
        width: calc(100% - 16px);
        margin-top: 8px;
      }}
      .hero, .period-panel {{
        padding: 14px;
      }}
      .video-title {{
        min-height: auto;
      }}
      .meta-row {{
        flex-direction: column;
      }}
      .feature-grid,
      .rest-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      {logo_html}
      <div class="hero-copy">
        <div>
          <h1>ぶいくりっぷ Vtuber切り抜きランキング</h1>
          <p>テスト運用中です。。。</p>
        </div>
        {admin_html}
      </div>
      <div class="period-tabs" id="period-tabs"></div>
    </section>
    <div id="period-root"></div>
    <footer class="site-footer">Copyright (C) 2026- 3vskhv0 All Rights Reserved.</footer>
  </main>
  <div id="player-modal" class="player-modal" aria-hidden="true">
    <div class="player-sheet" role="dialog" aria-modal="true" aria-label="動画プレイヤー">
      <div class="player-topbar">
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
    const periodTabs = document.getElementById("period-tabs");
    const periodRoot = document.getElementById("period-root");
    const playerModal = document.getElementById("player-modal");
    const playerIframe = document.getElementById("player-iframe");
    const playerClose = document.getElementById("player-close");
    let activePeriod = "{first_period}";

    function openPlayer(videoId) {{
      playerIframe.src = `https://www.youtube.com/embed/${{videoId}}?autoplay=1&playsinline=1`;
      playerModal.classList.add("open");
      playerModal.setAttribute("aria-hidden", "false");
    }}

    function closePlayer() {{
      playerModal.classList.remove("open");
      playerModal.setAttribute("aria-hidden", "true");
      playerIframe.src = "";
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

        const defaultGroup = period.available_groups[0];
        panel.innerHTML = `
          <div class="panel-head">
            <div>
              <h2>${{period.label}}ランキング</h2>
              <p>集計時刻: ${{period.calculated_at}}</p>
            </div>
            <div class="group-tabs"></div>
          </div>
          <div class="group-root"></div>
        `;

        const groupTabs = panel.querySelector(".group-tabs");
        const groupRoot = panel.querySelector(".group-root");

        period.available_groups.forEach((groupName) => {{
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

          const groupPanel = document.createElement("div");
          groupPanel.className = "group-panel" + (groupName === defaultGroup ? " active" : "");
          groupPanel.dataset.group = groupName;
          groupPanel.innerHTML = period.groups[groupName];
          groupRoot.appendChild(groupPanel);
        }});

        periodRoot.appendChild(panel);
      }});
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
        return;
      }}

      const trigger = event.target.closest(".thumb-play, .video-play");
      if (!trigger) {{
        return;
      }}
      event.preventDefault();
      openPlayer(trigger.dataset.videoId);
    }});
    playerClose.addEventListener("click", closePlayer);
    playerModal.addEventListener("click", (event) => {{
      if (event.target === playerModal) {{
        closePlayer();
      }}
    }});
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
    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path_only = parsed.path
        if path_only == "/assets/noto-sans-jp.ttf":
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
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

        if path_only not in {"/", "/index.html"}:
            self.send_error(404, "Not found")
            return

        is_admin = False
        if ADMIN_TOKEN:
            token = (query.get("admin_token") or [""])[0]
            is_admin = token == ADMIN_TOKEN

        try:
            body = render_homepage(is_admin=is_admin).encode("utf-8")
        except Exception as exc:
            logger.exception("Failed to render test site")
            body = render_error_page(exc).encode("utf-8")
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

