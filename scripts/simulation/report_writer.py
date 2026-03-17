"""
Report writers for channel refresh simulation outputs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_channel_tier_csv(path: Path, channel_rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "channel_id",
        "channel_name",
        "priority_score",
        "simulated_tier",
        "recent_video_count_48h",
        "recent_video_count_7d",
        "ranking_count_7d",
        "ranking_count_30d",
        "estimated_daily_refreshes",
        "estimated_weekly_refreshes",
        "risk_flag",
        "reason_summary",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in channel_rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def write_risk_csv(path: Path, risk_rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "channel_id",
        "channel_name",
        "priority_score",
        "simulated_tier",
        "risk_flag",
        "risk_reasons",
        "latest_video_published_at",
        "recent_video_count_48h",
        "recent_video_count_7d",
        "ranking_count_7d",
        "ranking_count_30d",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in risk_rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def write_metrics_json(path: Path, metrics: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")


def write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Channel Refresh Strategy Simulation")
    lines.append("")
    lines.append(f"- Generated at: `{summary['generated_at']}`")
    lines.append(f"- Output dir: `{summary['output_dir']}`")
    lines.append("")
    lines.append("## Schema Summary")
    lines.append("")
    lines.append(f"- Channels table: `{summary['schema']['channels_table']}`")
    lines.append(f"- Videos table: `{summary['schema']['videos_table']}`")
    lines.append(f"- Video stats table: `{summary['schema']['video_stats_table']}`")
    lines.append(
        f"- Ranking tables detected ({len(summary['schema']['ranking_tables'])}): "
        + ", ".join(f"`{t}`" for t in summary["schema"]["ranking_tables"])
    )
    lines.append("- Relevant columns:")
    for table_name, cols in summary["schema"]["relevant_columns"].items():
        lines.append(f"  - `{table_name}`: {', '.join(f'`{c}`' for c in cols)}")
    lines.append("")
    lines.append("## Assumptions")
    lines.append("")
    for item in summary["assumptions"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Scoring Rules Used")
    lines.append("")
    for item in summary["score_rules"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Tier Distribution")
    lines.append("")
    lines.append(f"- Total channels: **{summary['metrics']['total_channels']}**")
    lines.append(f"- Hot: **{summary['metrics']['hot_channels']}**")
    lines.append(f"- Warm: **{summary['metrics']['warm_channels']}**")
    lines.append(f"- Cold: **{summary['metrics']['cold_channels']}**")
    lines.append("")
    lines.append("## Refresh / API Cost Estimate")
    lines.append("")
    lines.append(
        f"- Simulated daily refresh jobs: **{summary['metrics']['estimated_daily_refresh_jobs']:.2f}**"
    )
    lines.append(
        f"- Simulated weekly refresh jobs: **{summary['metrics']['estimated_weekly_refresh_jobs']:.2f}**"
    )
    lines.append(
        f"- Simulated daily API cost: **{summary['metrics']['estimated_daily_api_cost']:.2f}**"
    )
    lines.append(
        f"- Simulated weekly API cost: **{summary['metrics']['estimated_weekly_api_cost']:.2f}**"
    )
    lines.append(
        f"- Current (fixed) daily API cost: **{summary['metrics']['current_strategy_daily_api_cost']:.2f}**"
    )
    lines.append(
        f"- Current (fixed) weekly API cost: **{summary['metrics']['current_strategy_weekly_api_cost']:.2f}**"
    )
    lines.append(
        f"- Estimated savings ratio: **{summary['metrics']['estimated_savings_ratio']:.2%}**"
    )
    lines.append("")
    lines.append("## Coverage / Risk Highlights")
    lines.append("")
    lines.append(f"- Risk-flagged channels: **{summary['risk_count']}**")
    if summary["risk_examples"]:
        lines.append("- Examples:")
        for item in summary["risk_examples"]:
            lines.append(f"  - `{item['channel_id']}` {item['channel_name']}: {item['risk_reasons']}")
    else:
        lines.append("- No risk channels were flagged under current rules.")
    lines.append("")
    lines.append("## Promotion / Demotion Suggestions")
    lines.append("")
    if summary["promote_candidates"]:
        lines.append("- Promote candidates:")
        for item in summary["promote_candidates"]:
            lines.append(
                f"  - `{item['channel_id']}` {item['channel_name']} (score={item['priority_score']}, tier={item['simulated_tier']})"
            )
    else:
        lines.append("- Promote candidates: none")
    if summary["demote_candidates"]:
        lines.append("- Demote candidates:")
        for item in summary["demote_candidates"]:
            lines.append(
                f"  - `{item['channel_id']}` {item['channel_name']} (score={item['priority_score']}, tier={item['simulated_tier']})"
            )
    else:
        lines.append("- Demote candidates: none")
    lines.append("")
    lines.append("## Missing Inputs / Fallbacks")
    lines.append("")
    if summary["missing_inputs"]:
        for item in summary["missing_inputs"]:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    for item in summary["recommendations"]:
        lines.append(f"- {item}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
