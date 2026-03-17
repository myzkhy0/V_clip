"""
Local GUI for channel refresh simulation.

Run:
  python scripts/simulation/sim_gui.py
Then open:
  http://127.0.0.1:8765
"""

from __future__ import annotations

import html
import json
import sys
from argparse import Namespace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
SIM_DIR = Path(__file__).resolve().parent
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulate_channel_priority import run_simulation  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
OUTPUT_BASE = ROOT_DIR / "scripts" / "simulation" / "output"

DEFAULTS = {
    "hot_threshold": 60,
    "warm_threshold": 20,
    "hot_cap": "",
    "hot_hours": 4,
    "warm_hours": 24,
    "cold_hours": 72,
    "current_fixed_hours": 4,
    "discovery_refresh_unit_cost": 1.0,
    "view_growth_threshold_48h": 50000,
    "rankable_rate_high": 0.40,
    "rankable_rate_low": 0.10,
    "strategy_mode": "cold_only",
    "cold_recent_growth_7d_max": 10000,
    "cold_min_inactive_days": 14,
    "cold_min_channel_age_days": 14,
    "cold_min_observed_videos": 1,
    "manual_protect_file": "",
}


def _to_int(data: dict[str, list[str]], key: str, default: int) -> int:
    raw = (data.get(key) or [""])[0].strip()
    if not raw:
        return default
    return int(raw)


def _to_float(data: dict[str, list[str]], key: str, default: float) -> float:
    raw = (data.get(key) or [""])[0].strip()
    if not raw:
        return default
    return float(raw)


def _to_optional_int(data: dict[str, list[str]], key: str) -> int | None:
    raw = (data.get(key) or [""])[0].strip()
    if not raw:
        return None
    return int(raw)


def _make_namespace(form: dict[str, list[str]]) -> Namespace:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Namespace(
        output_dir=OUTPUT_BASE / stamp,
        hot_threshold=_to_int(form, "hot_threshold", int(DEFAULTS["hot_threshold"])),
        warm_threshold=_to_int(form, "warm_threshold", int(DEFAULTS["warm_threshold"])),
        hot_cap=_to_optional_int(form, "hot_cap"),
        hot_hours=_to_int(form, "hot_hours", int(DEFAULTS["hot_hours"])),
        warm_hours=_to_int(form, "warm_hours", int(DEFAULTS["warm_hours"])),
        cold_hours=_to_int(form, "cold_hours", int(DEFAULTS["cold_hours"])),
        current_fixed_hours=_to_int(
            form, "current_fixed_hours", int(DEFAULTS["current_fixed_hours"])
        ),
        discovery_refresh_unit_cost=_to_float(
            form,
            "discovery_refresh_unit_cost",
            float(DEFAULTS["discovery_refresh_unit_cost"]),
        ),
        view_growth_threshold_48h=_to_int(
            form,
            "view_growth_threshold_48h",
            int(DEFAULTS["view_growth_threshold_48h"]),
        ),
        rankable_rate_high=_to_float(
            form, "rankable_rate_high", float(DEFAULTS["rankable_rate_high"])
        ),
        rankable_rate_low=_to_float(
            form, "rankable_rate_low", float(DEFAULTS["rankable_rate_low"])
        ),
        strategy_mode=(form.get("strategy_mode") or [str(DEFAULTS["strategy_mode"])])[0].strip() or str(DEFAULTS["strategy_mode"]),
        cold_recent_growth_7d_max=_to_int(form, "cold_recent_growth_7d_max", int(DEFAULTS["cold_recent_growth_7d_max"])),
        cold_min_inactive_days=_to_int(form, "cold_min_inactive_days", int(DEFAULTS["cold_min_inactive_days"])),
        cold_min_channel_age_days=_to_int(form, "cold_min_channel_age_days", int(DEFAULTS["cold_min_channel_age_days"])),
        cold_min_observed_videos=_to_int(form, "cold_min_observed_videos", int(DEFAULTS["cold_min_observed_videos"])),
        manual_protect_file=(form.get("manual_protect_file") or [str(DEFAULTS["manual_protect_file"])])[0].strip(),
    )


def _render_page(
    message: str = "",
    error: str = "",
    form_values: dict[str, str] | None = None,
    result: dict | None = None,
) -> str:
    values = {k: str(v) for k, v in DEFAULTS.items()}
    if form_values:
        values.update(form_values)

    def inp(name: str, label: str, hint: str = "") -> str:
        return (
            f"<label><span>{html.escape(label)}</span>"
            f"<input name='{name}' value='{html.escape(values.get(name, ''))}' /></label>"
            f"<small>{html.escape(hint)}</small>"
        )

    result_block = ""
    if result:
        metrics = result["metrics"]
        out_dir = result["summary"]["output_dir"]
        top_rows = sorted(result["rows"], key=lambda r: r["priority_score"], reverse=True)[:15]
        rows_html = "".join(
            "<tr>"
            f"<td>{html.escape(str(r['channel_id']))}</td>"
            f"<td>{html.escape(str(r.get('channel_name', '')))}</td>"
            f"<td>{r['priority_score']}</td>"
            f"<td>{r['simulated_tier']}</td>"
            f"<td>{r.get('recent_video_count_48h', 0)}</td>"
            f"<td>{r.get('ranking_count_7d', 0)}</td>"
            f"<td>{'yes' if r.get('risk_flag') else 'no'}</td>"
            "</tr>"
            for r in top_rows
        )
        metrics_json = html.escape(json.dumps(metrics, ensure_ascii=False, indent=2))
        result_block = f"""
        <section class='card'>
          <h2>Simulation Result</h2>
          <p><b>Output:</b> <code>{html.escape(out_dir)}</code></p>
          <p><b>Applied mode:</b> {metrics.get("strategy_mode")}</p>
          <p><b>Applied thresholds:</b> hot={metrics.get("applied_hot_threshold")}, warm={metrics.get("applied_warm_threshold")}, cold-growth-max={metrics.get("applied_cold_growth_7d_max")}</p>
          <div class='grid3'>
            <div class='metric'><b>{metrics['total_channels']}</b><span>Total channels</span></div>
            <div class='metric'><b>{metrics['hot_channels']}</b><span>Hot</span></div>
            <div class='metric'><b>{metrics['warm_channels']}</b><span>Warm</span></div>
            <div class='metric'><b>{metrics['cold_channels']}</b><span>Cold</span></div>
            <div class='metric'><b>{metrics['estimated_daily_api_cost']}</b><span>Sim daily API cost</span></div>
            <div class='metric'><b>{metrics['estimated_savings_ratio']:.2%}</b><span>Savings ratio</span></div>
          </div>
          <h3>Top Score Channels (preview)</h3>
          <table>
            <thead><tr><th>Channel ID</th><th>Name</th><th>Score</th><th>Tier</th><th>Videos 48h</th><th>Rank 7d</th><th>Risk</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          <h3>Metrics JSON</h3>
          <pre>{metrics_json}</pre>
        </section>
        """

    return f"""
<!doctype html>
<html lang='ja'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Simulation GUI</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#0b1220; color:#e6eefc; margin:0; }}
    .wrap {{ width:min(1080px, calc(100% - 28px)); margin:18px auto 40px; }}
    .card {{ background:#111c32; border:1px solid #314666; border-radius:12px; padding:16px; margin-bottom:14px; }}
    h1,h2,h3 {{ margin:0 0 10px; }}
    .desc {{ color:#a8bddc; margin-bottom:12px; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
    label {{ display:flex; flex-direction:column; gap:4px; }}
    input {{ background:#0a1428; color:#e6eefc; border:1px solid #3a5176; border-radius:8px; padding:8px; }}
    small {{ color:#8aa2c5; display:block; margin-top:2px; min-height:30px; }}
    button {{ background:#38bdf8; color:#071523; border:0; border-radius:8px; padding:10px 16px; font-weight:700; cursor:pointer; }}
    .msg {{ color:#8ef1a6; margin:0 0 8px; }}
    .err {{ color:#ff9ea5; margin:0 0 8px; }}
    .grid3 {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:10px 0; }}
    .metric {{ background:#0a1428; border:1px solid #334a70; border-radius:10px; padding:10px; }}
    .metric b {{ display:block; font-size:1.2rem; }}
    .metric span {{ color:#90a8cb; font-size:0.85rem; }}
    table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
    th,td {{ border-bottom:1px solid #2d405f; padding:7px; text-align:left; font-size:0.9rem; }}
    pre {{ background:#0a1428; border:1px solid #314666; border-radius:10px; padding:10px; overflow:auto; }}
    code {{ color:#a7f3d0; }}
    @media (max-width: 900px) {{ .form-grid, .grid3 {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width: 640px) {{ .form-grid, .grid3 {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <main class='wrap'>
    <section class='card'>
      <h1>Hot/Warm/Cold Simulation GUI</h1>
      <p class='desc'>本番スケジューラは変更せず、既存DBを読み取ってシミュレーションを実行します。</p>
      {f"<p class='msg'>{html.escape(message)}</p>" if message else ''}
      {f"<p class='err'>{html.escape(error)}</p>" if error else ''}
      <form method='post' action='/run'>
        <div class='form-grid'>
          {inp('hot_threshold', 'Hot threshold', 'score >= this => hot')}
          {inp('warm_threshold', 'Warm threshold', 'score >= this and < hot => warm')}
          {inp('hot_cap', 'Hot cap (optional)', 'blank means unlimited')}
          {inp('hot_hours', 'Hot refresh hours', 'default 4')}
          {inp('warm_hours', 'Warm refresh hours', 'default 24')}
          {inp('cold_hours', 'Cold refresh hours', 'default 72')}
          {inp('current_fixed_hours', 'Current fixed hours', 'baseline schedule, default 4')}
          {inp('discovery_refresh_unit_cost', 'Discovery unit cost', 'cost per channel refresh')}
          {inp('view_growth_threshold_48h', '48h growth threshold', 'for +20 score rule')}
          {inp('rankable_rate_high', 'Rankable high threshold', 'for +10 score rule')}
          {inp('rankable_rate_low', 'Rankable low threshold', 'for -15 score rule')}
          {inp('strategy_mode', 'Strategy mode', 'cold_only or full')}
          {inp('cold_recent_growth_7d_max', 'Cold growth 7d max', 'cold rule threshold')}
          {inp('cold_min_inactive_days', 'Cold min inactive days', 'default 14')}
          {inp('cold_min_channel_age_days', 'Cold min channel age', 'default 14')}
          {inp('cold_min_observed_videos', 'Cold min observed videos', 'default 1')}
          {inp('manual_protect_file', 'Manual protect file', 'optional path, one channel_id per line')}
        </div>
        <p style='margin-top:10px;'><button type='submit'>Run Simulation</button></p>
      </form>
    </section>
    {result_block}
  </main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_html(_render_page())
            return
        self._send_html(_render_page(error="Not found"), status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/run":
            self._send_html(_render_page(error="Not found"), status=404)
            return

        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        form = parse_qs(body)
        flat_values = {k: (v[0] if v else "") for k, v in form.items()}

        try:
            args = _make_namespace(form)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            result = run_simulation(args)
            self._send_html(
                _render_page(
                    message="Simulation completed successfully.",
                    form_values=flat_values,
                    result=result,
                )
            )
        except Exception as exc:
            self._send_html(
                _render_page(
                    error=f"Simulation failed: {exc}",
                    form_values=flat_values,
                ),
                status=500,
            )


def main() -> None:
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[sim-gui] http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()













