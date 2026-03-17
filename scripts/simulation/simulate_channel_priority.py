"""
Simulation-only tool for hot/warm/cold channel refresh strategy analysis.

This script is read-only against production tables and only writes local report files.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import fetchall  # noqa: E402

try:
    from scripts.simulation.scoring import ScoreConfig, score_channel  # noqa: E402
    from scripts.simulation.report_writer import (  # noqa: E402
        write_channel_tier_csv,
        write_metrics_json,
        write_risk_csv,
        write_summary_md,
    )
except ModuleNotFoundError:
    from scoring import ScoreConfig, score_channel  # type: ignore # noqa: E402
    from report_writer import (  # type: ignore # noqa: E402
        write_channel_tier_csv,
        write_metrics_json,
        write_risk_csv,
        write_summary_md,
    )


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class TierConfig:
    hot_threshold: int = 80
    warm_threshold: int = 30
    hot_cap: int | None = None
    hot_hours: int = 4
    warm_hours: int = 24
    cold_hours: int = 72
    current_fixed_hours: int = 4
    discovery_refresh_unit_cost: float = 1.0
    strategy_mode: str = "cold_only"
    cold_recent_growth_7d_max: int = 10000
    cold_min_inactive_days: int = 14
    cold_min_channel_age_days: int = 14
    cold_min_observed_videos: int = 1
    manual_protect_file: str = ""


class SimContext:
    def __init__(self) -> None:
        self.missing_inputs: list[str] = []
        self.assumptions: list[str] = []

    def note_missing(self, msg: str) -> None:
        if msg not in self.missing_inputs:
            self.missing_inputs.append(msg)

    def note_assumption(self, msg: str) -> None:
        if msg not in self.assumptions:
            self.assumptions.append(msg)


def _safe_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def _fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return fetchall(sql, params)


def inspect_schema(ctx: SimContext) -> dict[str, Any]:
    rows = _fetchall(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    )
    table_cols: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        table_cols[row["table_name"]].add(row["column_name"])

    channels_table = "channels" if "channels" in table_cols else None
    videos_table = "videos" if "videos" in table_cols else None
    video_stats_table = "video_stats" if "video_stats" in table_cols else None

    ranking_tables = sorted(
        [
            t
            for t, cols in table_cols.items()
            if "ranking" in t and {"video_id", "calculated_at"}.issubset(cols)
        ]
    )

    if not channels_table:
        ctx.note_missing("channels table not found; channel base will be inferred from videos")
    if not videos_table:
        ctx.note_missing("videos table not found; video-derived features unavailable")
    if not video_stats_table:
        ctx.note_missing("video_stats table not found; view-growth features unavailable")
    if not ranking_tables:
        ctx.note_missing("ranking tables not found; ranking-appearance features unavailable")

    relevant_columns: dict[str, list[str]] = {}
    for t in [channels_table, videos_table, video_stats_table, *ranking_tables]:
        if t:
            relevant_columns[t] = sorted(table_cols.get(t, set()))

    return {
        "table_cols": table_cols,
        "channels_table": channels_table,
        "videos_table": videos_table,
        "video_stats_table": video_stats_table,
        "ranking_tables": ranking_tables,
        "relevant_columns": relevant_columns,
    }


def _get_channels(schema: dict[str, Any], ctx: SimContext) -> dict[str, dict[str, Any]]:
    table_cols: dict[str, set[str]] = schema["table_cols"]
    channels: dict[str, dict[str, Any]] = {}

    channels_table = schema["channels_table"]
    if channels_table:
        cols = table_cols[channels_table]
        selected = ["channel_id"]
        for c in ["channel_name", "group_name", "is_tracked", "last_checked_at", "added_at"]:
            if c in cols:
                selected.append(c)
        sql = f"SELECT {', '.join(selected)} FROM {_safe_ident(channels_table)}"
        rows = _fetchall(sql)
        for r in rows:
            cid = r["channel_id"]
            channels[cid] = {
                "channel_id": cid,
                "channel_name": r.get("channel_name") or cid,
                "group_name": r.get("group_name") or "",
                "is_tracked": r.get("is_tracked") if "is_tracked" in r else True,
                "last_refresh_at": r.get("last_checked_at") or r.get("added_at"),
                "channel_added_at": r.get("added_at"),
            }

    if channels:
        return channels

    videos_table = schema["videos_table"]
    if videos_table:
        cols = table_cols[videos_table]
        if "channel_id" in cols:
            name_expr = "MAX(channel_name) AS channel_name" if "channel_name" in cols else "NULL AS channel_name"
            sql = f"""
            SELECT channel_id, {name_expr}
            FROM {_safe_ident(videos_table)}
            GROUP BY channel_id
            """
            rows = _fetchall(sql)
            for r in rows:
                cid = r["channel_id"]
                channels[cid] = {
                    "channel_id": cid,
                    "channel_name": r.get("channel_name") or cid,
                    "group_name": "",
                    "is_tracked": True,
                    "last_refresh_at": None,
                    "channel_added_at": None,
                }
            ctx.note_assumption("channels inferred from videos.channel_id due to missing/empty channels table")

    return channels


def _apply_video_features(
    channels: dict[str, dict[str, Any]], schema: dict[str, Any], ctx: SimContext
) -> None:
    videos_table = schema["videos_table"]
    if not videos_table:
        ctx.note_missing("videos table unavailable: recent upload features set to 0")
        return

    cols = schema["table_cols"][videos_table]
    if "channel_id" not in cols:
        ctx.note_missing("videos.channel_id missing: recent upload features set to 0")
        return

    has_published_at = "published_at" in cols
    has_added_at = "added_at" in cols

    published_ref = "published_at" if has_published_at else ("added_at" if has_added_at else None)
    if not published_ref:
        ctx.note_missing("videos.published_at/added_at missing: recent upload features set to 0")
        return
    if published_ref != "published_at":
        ctx.note_assumption("videos.added_at used as fallback for publish-time-derived features")

    sql = f"""
    SELECT
      channel_id,
      MAX({published_ref}) AS latest_video_published_at,
      COUNT(*) FILTER (WHERE {published_ref} >= NOW() - INTERVAL '48 hours') AS recent_video_count_48h,
      COUNT(*) FILTER (WHERE {published_ref} >= NOW() - INTERVAL '7 days') AS recent_video_count_7d,
      COUNT(*) FILTER (WHERE {published_ref} >= NOW() - INTERVAL '14 days') AS recent_video_count_14d,
      COUNT(*) FILTER (WHERE {published_ref} >= NOW() - INTERVAL '30 days') AS recent_video_count_30d
    FROM {_safe_ident(videos_table)}
    GROUP BY channel_id
    """
    rows = _fetchall(sql)
    for r in rows:
        cid = r["channel_id"]
        if cid not in channels:
            channels[cid] = {
                "channel_id": cid,
                "channel_name": cid,
                "group_name": "",
                "is_tracked": True,
                "last_refresh_at": None,
                    "channel_added_at": None,
                }
        channels[cid]["latest_video_published_at"] = r.get("latest_video_published_at")
        channels[cid]["recent_video_count_48h"] = int(r.get("recent_video_count_48h") or 0)
        channels[cid]["recent_video_count_7d"] = int(r.get("recent_video_count_7d") or 0)
        channels[cid]["recent_video_count_14d"] = int(r.get("recent_video_count_14d") or 0)
        channels[cid]["recent_video_count_30d"] = int(r.get("recent_video_count_30d") or 0)


def _apply_growth_features(
    channels: dict[str, dict[str, Any]], schema: dict[str, Any], ctx: SimContext
) -> None:
    videos_table = schema["videos_table"]
    stats_table = schema["video_stats_table"]
    if not videos_table or not stats_table:
        ctx.note_missing("videos/video_stats not fully available: view-growth features set to 0")
        return

    v_cols = schema["table_cols"][videos_table]
    s_cols = schema["table_cols"][stats_table]
    if not {"video_id", "channel_id"}.issubset(v_cols):
        ctx.note_missing("videos.video_id/channel_id missing: view-growth features set to 0")
        return
    if "published_at" not in v_cols:
        ctx.note_missing("videos.published_at missing: growth limited; set to 0")
        return
    if not {"video_id", "timestamp", "view_count"}.issubset(s_cols):
        ctx.note_missing("video_stats required columns missing: view-growth features set to 0")
        return

    windows = {
        "48h": "48 hours",
        "7d": "7 days",
    }
    for label, interval_expr in windows.items():
        sql = f"""
        WITH growth AS (
          SELECT video_id, GREATEST(MAX(view_count) - MIN(view_count), 0) AS view_growth
          FROM {_safe_ident(stats_table)}
          WHERE timestamp >= NOW() - INTERVAL '{interval_expr}'
          GROUP BY video_id
        )
        SELECT
          v.channel_id,
          COALESCE(SUM(g.view_growth), 0) AS total_growth
        FROM {_safe_ident(videos_table)} v
        LEFT JOIN growth g ON g.video_id = v.video_id
        WHERE v.published_at >= NOW() - INTERVAL '{interval_expr}'
        GROUP BY v.channel_id
        """
        rows = _fetchall(sql)
        key = f"recent_view_growth_{label}"
        for r in rows:
            cid = r["channel_id"]
            if cid in channels:
                channels[cid][key] = int(r.get("total_growth") or 0)


def _collect_ranking_counts(
    channels: dict[str, dict[str, Any]], schema: dict[str, Any], ctx: SimContext
) -> None:
    ranking_tables: list[str] = schema["ranking_tables"]
    videos_table = schema["videos_table"]
    if not ranking_tables or not videos_table:
        ctx.note_missing("ranking features unavailable due to missing ranking/videos tables")
        return

    v_cols = schema["table_cols"][videos_table]
    if not {"video_id", "channel_id"}.issubset(v_cols):
        ctx.note_missing("videos.video_id/channel_id missing: ranking features unavailable")
        return

    counts_7d: dict[str, int] = defaultdict(int)
    counts_30d: dict[str, int] = defaultdict(int)
    ranked_video_ids_30d: dict[str, set[str]] = defaultdict(set)

    for table in ranking_tables:
        sql_7d = f"""
        SELECT v.channel_id, COUNT(DISTINCT r.video_id) AS cnt
        FROM {_safe_ident(table)} r
        JOIN {_safe_ident(videos_table)} v ON v.video_id = r.video_id
        WHERE r.calculated_at >= NOW() - INTERVAL '7 days'
        GROUP BY v.channel_id
        """
        sql_30d = f"""
        SELECT v.channel_id, COUNT(DISTINCT r.video_id) AS cnt
        FROM {_safe_ident(table)} r
        JOIN {_safe_ident(videos_table)} v ON v.video_id = r.video_id
        WHERE r.calculated_at >= NOW() - INTERVAL '30 days'
        GROUP BY v.channel_id
        """
        sql_ids_30d = f"""
        SELECT DISTINCT v.channel_id, r.video_id
        FROM {_safe_ident(table)} r
        JOIN {_safe_ident(videos_table)} v ON v.video_id = r.video_id
        WHERE r.calculated_at >= NOW() - INTERVAL '30 days'
        """
        for r in _fetchall(sql_7d):
            counts_7d[r["channel_id"]] += int(r.get("cnt") or 0)
        for r in _fetchall(sql_30d):
            counts_30d[r["channel_id"]] += int(r.get("cnt") or 0)
        for r in _fetchall(sql_ids_30d):
            ranked_video_ids_30d[r["channel_id"]].add(r["video_id"])

    for cid, data in channels.items():
        data["ranking_count_7d"] = counts_7d.get(cid, 0)
        data["ranking_count_30d"] = counts_30d.get(cid, 0)
        denom = int(data.get("recent_video_count_30d", 0) or 0)
        if denom > 0:
            data["rankable_rate"] = len(ranked_video_ids_30d.get(cid, set())) / denom
        else:
            data["rankable_rate"] = None


def _apply_default_features(channels: dict[str, dict[str, Any]]) -> None:
    defaults = {
        "latest_video_published_at": None,
        "recent_video_count_48h": 0,
        "recent_video_count_7d": 0,
        "recent_video_count_14d": 0,
        "recent_video_count_30d": 0,
        "recent_view_growth_48h": 0,
        "recent_view_growth_7d": 0,
        "ranking_count_7d": 0,
        "ranking_count_30d": 0,
        "rankable_rate": None,
    }
    for row in channels.values():
        for key, value in defaults.items():
            row.setdefault(key, value)


def _assign_tiers(rows: list[dict[str, Any]], cfg: TierConfig) -> None:
    for row in rows:
        score = int(row["priority_score"])
        if score >= cfg.hot_threshold:
            row["simulated_tier"] = "hot"
        elif score >= cfg.warm_threshold:
            row["simulated_tier"] = "warm"
        else:
            row["simulated_tier"] = "cold"

    if cfg.hot_cap is None:
        return

    hot_rows = [r for r in rows if r["simulated_tier"] == "hot"]
    if len(hot_rows) <= cfg.hot_cap:
        return

    sorted_hot = sorted(
        hot_rows,
        key=lambda r: (
            r["priority_score"],
            r.get("recent_video_count_48h", 0),
            r.get("ranking_count_7d", 0),
            r.get("recent_view_growth_48h", 0),
        ),
        reverse=True,
    )
    keep_ids = {r["channel_id"] for r in sorted_hot[: cfg.hot_cap]}
    for row in rows:
        if row["simulated_tier"] == "hot" and row["channel_id"] not in keep_ids:
            row["simulated_tier"] = "warm"
            row["reason_details"].append("hot_cap_overflow_to_warm")


def _days_since(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    value = ts
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds() / 86400.0


def _load_manual_protect_ids(path_value: str) -> set[str]:
    if not path_value:
        return set()
    path = Path(path_value)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            ids.add(v)
    return ids


def _is_cold_candidate(row: dict[str, Any], cfg: TierConfig, protected_ids: set[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if row["channel_id"] in protected_ids:
        return False, ["manual_protect"]

    c7 = int(row.get("recent_video_count_7d", 0) or 0)
    c14 = int(row.get("recent_video_count_14d", 0) or 0)
    r30 = int(row.get("ranking_count_30d", 0) or 0)
    g7 = int(row.get("recent_view_growth_7d", 0) or 0)
    recent30 = int(row.get("recent_video_count_30d", 0) or 0)

    latest_days = _days_since(row.get("latest_video_published_at"))
    channel_age_days = _days_since(row.get("channel_added_at"))

    is_inactive = latest_days is None or latest_days >= cfg.cold_min_inactive_days
    observed = recent30 >= cfg.cold_min_observed_videos or (
        channel_age_days is not None and channel_age_days >= cfg.cold_min_channel_age_days
    )

    if c7 == 0:
        reasons.append("no_upload_7d")
    if c14 == 0:
        reasons.append("no_upload_14d")
    if r30 == 0:
        reasons.append("no_ranking_30d")
    if g7 < cfg.cold_recent_growth_7d_max:
        reasons.append(f"growth_7d_lt_{cfg.cold_recent_growth_7d_max}")
    if is_inactive:
        reasons.append(f"inactive_ge_{cfg.cold_min_inactive_days}d")
    if observed:
        reasons.append("observation_sufficient")

    ok = r30 == 0 and g7 < cfg.cold_recent_growth_7d_max and is_inactive and observed
    return ok, reasons


def _refreshes_per_day(row: dict[str, Any], cfg: TierConfig) -> float:
    tier = row["simulated_tier"]
    if cfg.strategy_mode == "cold_only":
        if tier == "cold":
            return 24.0 / cfg.cold_hours
        return 24.0 / cfg.current_fixed_hours

    if tier == "hot":
        return 24.0 / cfg.hot_hours
    if tier == "warm":
        return 24.0 / cfg.warm_hours
    return 24.0 / cfg.cold_hours


def _build_risk(row: dict[str, Any], cfg: TierConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    tier = row["simulated_tier"]
    if row.get("recent_video_count_48h", 0) > 0 and tier != "hot":
        reasons.append("recent_upload_48h_but_not_hot")
    if row.get("recent_video_count_7d", 0) > 0 and tier == "cold":
        reasons.append("recent_upload_7d_but_cold")
    if row.get("ranking_count_7d", 0) > 0 and tier == "cold":
        reasons.append("ranking_7d_presence_but_cold")

    if cfg.strategy_mode == "cold_only":
        if tier == "cold":
            reasons.append("refresh_delay_vs_current:+68h")
    else:
        if tier == "warm":
            reasons.append("refresh_delay_vs_current:+20h")
        if tier == "cold":
            reasons.append("refresh_delay_vs_current:+68h")

    return (len(reasons) > 0), reasons


def _score_rules_text(cfg: ScoreConfig) -> list[str]:
    return [
        f"+{cfg.points_recent_video_48h} if >=1 new video in 48h",
        f"+{cfg.points_recent_video_7d} if >=1 new video in 7d",
        f"+{cfg.points_ranking_7d} if ranking appearance in 7d",
        f"+{cfg.points_ranking_30d} if ranking appearance in 30d",
        f"+{cfg.points_growth_48h} if 48h view growth > {cfg.view_growth_48h_threshold}",
        f"+{cfg.points_rankable_rate_high} if rankable rate >= {cfg.rankable_rate_high_threshold:.2f}",
        f"-{cfg.penalty_no_new_14d} if no new videos in 14d",
        f"-{cfg.penalty_no_new_30d} if no new videos in 30d",
        f"-{cfg.penalty_rankable_rate_low} if rankable rate < {cfg.rankable_rate_low_threshold:.2f}",
    ]


def run_simulation(args: argparse.Namespace) -> dict[str, Any]:
    ctx = SimContext()
    schema = inspect_schema(ctx)

    channels = _get_channels(schema, ctx)
    if not channels:
        raise RuntimeError("No channels could be identified from channels/videos tables.")

    _apply_video_features(channels, schema, ctx)
    _apply_growth_features(channels, schema, ctx)
    _collect_ranking_counts(channels, schema, ctx)
    _apply_default_features(channels)

    score_cfg = ScoreConfig(
        view_growth_48h_threshold=args.view_growth_threshold_48h,
        rankable_rate_high_threshold=args.rankable_rate_high,
        rankable_rate_low_threshold=args.rankable_rate_low,
    )
    tier_cfg = TierConfig(
        hot_threshold=args.hot_threshold,
        warm_threshold=args.warm_threshold,
        hot_cap=args.hot_cap,
        hot_hours=args.hot_hours,
        warm_hours=args.warm_hours,
        cold_hours=args.cold_hours,
        current_fixed_hours=args.current_fixed_hours,
        discovery_refresh_unit_cost=args.discovery_refresh_unit_cost,
        strategy_mode=args.strategy_mode,
        cold_recent_growth_7d_max=args.cold_recent_growth_7d_max,
        cold_min_inactive_days=args.cold_min_inactive_days,
        cold_min_channel_age_days=args.cold_min_channel_age_days,
        cold_min_observed_videos=args.cold_min_observed_videos,
        manual_protect_file=args.manual_protect_file,
    )

    rows: list[dict[str, Any]] = []
    for data in channels.values():
        score, reasons = score_channel(data, score_cfg)
        row = dict(data)
        row["priority_score"] = score
        row["reason_details"] = reasons
        rows.append(row)

    rows.sort(key=lambda r: (r["priority_score"], r.get("recent_video_count_48h", 0)), reverse=True)
    _assign_tiers(rows, tier_cfg)

    protected_ids = _load_manual_protect_ids(tier_cfg.manual_protect_file)
    for row in rows:
        cold_ok, cold_reasons = _is_cold_candidate(row, tier_cfg, protected_ids)
        row["cold_candidate"] = cold_ok
        row["cold_reason_summary"] = "; ".join(cold_reasons)
        if tier_cfg.strategy_mode == "cold_only":
            row["simulated_tier"] = "cold" if cold_ok else "active"
        elif cold_ok:
            row["simulated_tier"] = "cold"
            row["reason_details"].append("cold_rule_matched")

    hot = warm = cold = active = 0
    daily_jobs = 0.0
    weekly_jobs = 0.0
    risk_rows: list[dict[str, Any]] = []

    for row in rows:
        tier = row["simulated_tier"]
        if tier == "hot":
            hot += 1
        elif tier == "warm":
            warm += 1
        elif tier == "cold":
            cold += 1
        else:
            active += 1

        row["estimated_daily_refreshes"] = round(_refreshes_per_day(row, tier_cfg), 4)
        row["estimated_weekly_refreshes"] = round(row["estimated_daily_refreshes"] * 7.0, 4)
        daily_jobs += row["estimated_daily_refreshes"]
        weekly_jobs += row["estimated_weekly_refreshes"]

        risk_flag, risk_reasons = _build_risk(row, tier_cfg)
        row["risk_flag"] = risk_flag
        row["risk_reasons"] = "; ".join(risk_reasons)

        reasons = row["reason_details"] + ([row["cold_reason_summary"]] if row.get("cold_reason_summary") else []) + (risk_reasons[:2] if risk_reasons else [])
        row["reason_summary"] = "; ".join(reasons[:6])

        if risk_flag:
            risk_rows.append(row)

    total = len(rows)
    current_daily_jobs = total * (24.0 / tier_cfg.current_fixed_hours)
    current_weekly_jobs = current_daily_jobs * 7.0

    simulated_daily_api = daily_jobs * tier_cfg.discovery_refresh_unit_cost
    simulated_weekly_api = weekly_jobs * tier_cfg.discovery_refresh_unit_cost
    current_daily_api = current_daily_jobs * tier_cfg.discovery_refresh_unit_cost
    current_weekly_api = current_weekly_jobs * tier_cfg.discovery_refresh_unit_cost

    savings_ratio = 0.0
    if current_daily_api > 0:
        savings_ratio = max(0.0, 1.0 - (simulated_daily_api / current_daily_api))

    metrics = {
        "total_channels": total,
        "hot_channels": hot,
        "warm_channels": warm,
        "cold_channels": cold,
        "active_channels": active,
        "strategy_mode": tier_cfg.strategy_mode,
        "applied_hot_threshold": tier_cfg.hot_threshold,
        "applied_warm_threshold": tier_cfg.warm_threshold,
        "applied_cold_growth_7d_max": tier_cfg.cold_recent_growth_7d_max,
        "applied_cold_min_inactive_days": tier_cfg.cold_min_inactive_days,
        "estimated_daily_refresh_jobs": round(daily_jobs, 4),
        "estimated_weekly_refresh_jobs": round(weekly_jobs, 4),
        "estimated_daily_api_cost": round(simulated_daily_api, 4),
        "estimated_weekly_api_cost": round(simulated_weekly_api, 4),
        "current_strategy_daily_api_cost": round(current_daily_api, 4),
        "current_strategy_weekly_api_cost": round(current_weekly_api, 4),
        "estimated_savings_ratio": round(savings_ratio, 6),
    }

    promote_candidates = [
        r
        for r in rows
        if r["simulated_tier"] != "hot"
        and (r.get("recent_video_count_48h", 0) > 0 or r.get("ranking_count_7d", 0) > 0)
    ][:10]
    demote_candidates = [
        r
        for r in rows
        if r["simulated_tier"] == "hot"
        and r.get("recent_video_count_7d", 0) == 0
        and r.get("ranking_count_7d", 0) == 0
    ][:10]

    if args.hot_cap is not None:
        ctx.note_assumption(f"hot cap enabled: top {args.hot_cap} channels remain hot")

    ctx.note_assumption(
        f"current baseline assumes all channels refreshed every {tier_cfg.current_fixed_hours}h"
    )
    ctx.note_assumption(
        "refresh cost estimated with fixed per-channel discovery unit cost"
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(args.output_dir),
        "schema": {
            "channels_table": schema["channels_table"] or "(missing)",
            "videos_table": schema["videos_table"] or "(missing)",
            "video_stats_table": schema["video_stats_table"] or "(missing)",
            "ranking_tables": schema["ranking_tables"],
            "relevant_columns": schema["relevant_columns"],
        },
        "assumptions": ctx.assumptions,
        "score_rules": _score_rules_text(score_cfg),
        "metrics": metrics,
        "risk_count": len(risk_rows),
        "risk_examples": [
            {
                "channel_id": r["channel_id"],
                "channel_name": r.get("channel_name", ""),
                "risk_reasons": r["risk_reasons"],
            }
            for r in risk_rows[:15]
        ],
        "promote_candidates": [
            {
                "channel_id": r["channel_id"],
                "channel_name": r.get("channel_name", ""),
                "priority_score": r["priority_score"],
                "simulated_tier": r["simulated_tier"],
            }
            for r in promote_candidates
        ],
        "demote_candidates": [
            {
                "channel_id": r["channel_id"],
                "channel_name": r.get("channel_name", ""),
                "priority_score": r["priority_score"],
                "simulated_tier": r["simulated_tier"],
            }
            for r in demote_candidates
        ],
        "missing_inputs": ctx.missing_inputs,
        "recommendations": [
            "Validate 10-20 risk-flagged channels manually before applying tiered scheduling in production.",
            "Tune view-growth threshold and hot cap to keep ranking-sensitive channels in hot/warm tiers.",
            "Run this simulation weekly and compare drift in tier counts and risk set size.",
        ],
    }

    return {
        "rows": rows,
        "risk_rows": risk_rows,
        "metrics": metrics,
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate hot/warm/cold channel refresh strategy using existing DB data."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "scripts" / "simulation" / "output" / datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Directory for simulation outputs",
    )
    parser.add_argument("--hot-threshold", type=int, default=80)
    parser.add_argument("--warm-threshold", type=int, default=30)
    parser.add_argument("--hot-cap", type=int, default=None)
    parser.add_argument("--hot-hours", type=int, default=4)
    parser.add_argument("--warm-hours", type=int, default=24)
    parser.add_argument("--cold-hours", type=int, default=72)
    parser.add_argument("--current-fixed-hours", type=int, default=4)
    parser.add_argument("--discovery-refresh-unit-cost", type=float, default=1.0)
    parser.add_argument("--view-growth-threshold-48h", type=int, default=50000)
    parser.add_argument("--rankable-rate-high", type=float, default=0.40)
    parser.add_argument("--rankable-rate-low", type=float, default=0.10)
    parser.add_argument("--strategy-mode", choices=["full", "cold_only"], default="cold_only")
    parser.add_argument("--cold-recent-growth-7d-max", type=int, default=10000)
    parser.add_argument("--cold-min-inactive-days", type=int, default=14)
    parser.add_argument("--cold-min-channel-age-days", type=int, default=14)
    parser.add_argument("--cold-min-observed-videos", type=int, default=1)
    parser.add_argument("--manual-protect-file", type=str, default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    result = run_simulation(args)

    write_summary_md(args.output_dir / "simulation_summary.md", result["summary"])
    write_channel_tier_csv(args.output_dir / "channel_tier_simulation.csv", result["rows"])
    write_metrics_json(args.output_dir / "simulation_metrics.json", result["metrics"])
    write_risk_csv(args.output_dir / "risk_channels.csv", result["risk_rows"])

    print(f"[simulation] completed: {args.output_dir}")
    print(
        "[simulation] outputs: simulation_summary.md, channel_tier_simulation.csv, "
        "simulation_metrics.json, risk_channels.csv"
    )


if __name__ == "__main__":
    main()



























