"""
test_site.py -- Local ranking viewer with period and group tabs.
"""

from __future__ import annotations

import html
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import GROUP_KEYWORDS
from db import fetchall

logger = logging.getLogger(__name__)

HOST = os.getenv("TEST_SITE_HOST", "127.0.0.1")
PORT = int(os.getenv("TEST_SITE_PORT", "8000"))
FONT_FILE = r"C:\Users\11bs0\OneDrive\デスクトップ\NotoSansJP-VariableFont_wght.ttf"

PERIODS: list[tuple[str, str]] = [
    ("daily_ranking", "24時間"),
    ("weekly_ranking", "7日"),
    ("monthly_ranking", "30日"),
]

GROUP_ORDER = [
    "all",
    "hololive",
    "nijisanji",
    "VSPO",
    "Neo-Porte",
    "UniReid",
    "774inc",
    "NoriPro",
    "MilliPro",
    "Aogiri",
    "DotLive",
    "other",
]
GROUP_LABELS = {
    "all": "全体",
    "hololive": "ホロライブ",
    "nijisanji": "にじさんじ",
    "VSPO": "ぶいすぽ / VSPO",
    "Neo-Porte": "Neo-Porte",
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
            v.group_name,
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
    return value.strftime("%Y-%m-%d %H:%M")


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


def _render_cards(rows: list[dict], card_class: str = "") -> str:
    if not rows:
        return '<div class="empty">このタブに該当する動画はありません。</div>'

    cards = []
    for row in rows:
        video_id = html.escape(row["video_id"])
        title = html.escape(row["title"])
        channel_id = html.escape(row["channel_id"])
        channel_name = html.escape(row["channel_name"])
        group_name = html.escape(_infer_group(row))
        published_at = _fmt_datetime(row["published_at"])
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        class_name = "video-card" if not card_class else f"video-card {card_class}"
        cards.append(
            f"""
            <article class="{class_name}">
              <a class="thumb" href="{video_url}" target="_blank" rel="noreferrer">
                <img src="{_thumbnail_url(video_id)}" alt="{title}" loading="lazy">
                <span class="rank-badge">#{row['rank']}</span>
              </a>
              <div class="video-body">
                <a class="video-title" href="{video_url}" target="_blank" rel="noreferrer">{title}</a>
                <div class="meta-row">
                  <a class="channel-link" href="{channel_url}" target="_blank" rel="noreferrer">{channel_name}</a>
                  <span class="pill">{group_name}</span>
                </div>
                <div class="meta-row compact">
                  <span>再生増加 +{row['view_growth']:,}</span>
                  <span>{published_at}</span>
                </div>
              </div>
            </article>
            """
        )
    return "".join(cards)


def _render_group_content(rows: list[dict]) -> str:
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


def render_error_page(error: Exception) -> str:
    message = html.escape(str(error) or error.__class__.__name__)
    database_url = os.getenv("DATABASE_URL", "(not set)")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VTuber切り抜きランキング</title>
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


def render_homepage() -> str:
    payload = _build_period_payload()
    first_period = payload[0]["table"] if payload else ""
    group_labels_json = json.dumps(GROUP_LABELS, ensure_ascii=False).replace("</", "<\\/")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VTuber切り抜きランキング</title>
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
    .hero, .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .hero {{
      padding: 28px;
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
      min-height: auto;
      line-height: 1.38;
    }}
    .feature-card .meta-row.compact {{
      font-size: 0.84rem;
    }}
    .feature-card .rank-badge {{
      top: 12px;
      left: 12px;
      padding: 7px 10px;
      font-size: 0.84rem;
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
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(29, 42, 51, 0.8);
      color: #fff;
      font-size: 0.9rem;
    }}
    .video-body {{
      padding: 14px;
    }}
    .video-title {{
      display: block;
      color: var(--ink);
      text-decoration: none;
      line-height: 1.45;
      min-height: 4.2em;
      font-weight: 600;
    }}
    .channel-link {{
      color: var(--accent-cool);
      text-decoration: none;
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
      <div class="hero-copy">
        <div>
          <h1>VTuber切り抜きランキング</h1>
          <p>テスト運用中です。。。</p>
        </div>
      </div>
      <div class="period-tabs" id="period-tabs"></div>
    </section>
    <div id="period-root"></div>
  </main>
  <script>
    const payload = {payload_json};
    const groupLabels = {group_labels_json};
    const periodTabs = document.getElementById("period-tabs");
    const periodRoot = document.getElementById("period-root");
    let activePeriod = "{first_period}";

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

    render();
  </script>
</body>
</html>
"""


class TestSiteHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        if self.path == "/assets/noto-sans-jp.ttf":
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        if self.path in {"/", "/index.html"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        self.send_error(404, "Not found")

    def do_GET(self) -> None:
        if self.path == "/assets/noto-sans-jp.ttf":
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

        if self.path not in {"/", "/index.html"}:
            self.send_error(404, "Not found")
            return

        try:
            body = render_homepage().encode("utf-8")
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
