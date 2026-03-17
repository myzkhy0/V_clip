"""
Simulation-only scoring rules for hot/warm/cold channel prioritization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ScoreConfig:
    view_growth_48h_threshold: int = 50000
    rankable_rate_high_threshold: float = 0.40
    rankable_rate_low_threshold: float = 0.10
    points_recent_video_48h: int = 40
    points_recent_video_7d: int = 20
    points_ranking_7d: int = 25
    points_ranking_30d: int = 10
    points_growth_48h: int = 20
    points_rankable_rate_high: int = 10
    penalty_no_new_14d: int = 20
    penalty_no_new_30d: int = 40
    penalty_rankable_rate_low: int = 15


def _days_since(dt: datetime | None, now_utc: datetime) -> float | None:
    if dt is None:
        return None
    value = dt
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now_utc - value.astimezone(timezone.utc)).total_seconds() / 86400.0


def score_channel(features: dict[str, Any], cfg: ScoreConfig) -> tuple[int, list[str]]:
    now_utc = datetime.now(timezone.utc)
    score = 0
    reasons: list[str] = []

    recent_video_count_48h = int(features.get("recent_video_count_48h", 0) or 0)
    recent_video_count_7d = int(features.get("recent_video_count_7d", 0) or 0)
    ranking_count_7d = int(features.get("ranking_count_7d", 0) or 0)
    ranking_count_30d = int(features.get("ranking_count_30d", 0) or 0)
    recent_view_growth_48h = int(features.get("recent_view_growth_48h", 0) or 0)
    rankable_rate = features.get("rankable_rate")
    latest_video_published_at = features.get("latest_video_published_at")

    if recent_video_count_48h >= 1:
        score += cfg.points_recent_video_48h
        reasons.append(f"+{cfg.points_recent_video_48h}: recent_upload_48h")
    if recent_video_count_7d >= 1:
        score += cfg.points_recent_video_7d
        reasons.append(f"+{cfg.points_recent_video_7d}: recent_upload_7d")
    if ranking_count_7d >= 1:
        score += cfg.points_ranking_7d
        reasons.append(f"+{cfg.points_ranking_7d}: ranking_appearance_7d")
    if ranking_count_30d >= 1:
        score += cfg.points_ranking_30d
        reasons.append(f"+{cfg.points_ranking_30d}: ranking_appearance_30d")
    if recent_view_growth_48h > cfg.view_growth_48h_threshold:
        score += cfg.points_growth_48h
        reasons.append(
            f"+{cfg.points_growth_48h}: growth_48h_gt_{cfg.view_growth_48h_threshold}"
        )

    if rankable_rate is not None:
        rate = float(rankable_rate)
        if rate >= cfg.rankable_rate_high_threshold:
            score += cfg.points_rankable_rate_high
            reasons.append(f"+{cfg.points_rankable_rate_high}: rankable_rate_high")
        elif rate < cfg.rankable_rate_low_threshold:
            score -= cfg.penalty_rankable_rate_low
            reasons.append(f"-{cfg.penalty_rankable_rate_low}: rankable_rate_low")

    days_since_latest = _days_since(latest_video_published_at, now_utc)
    if days_since_latest is None or days_since_latest > 30:
        score -= cfg.penalty_no_new_30d
        reasons.append(f"-{cfg.penalty_no_new_30d}: no_new_video_30d")
    elif days_since_latest > 14:
        score -= cfg.penalty_no_new_14d
        reasons.append(f"-{cfg.penalty_no_new_14d}: no_new_video_14d")

    return score, reasons
