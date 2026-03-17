# Channel Refresh Strategy Simulation

- Generated at: `2026-03-17T04:06:55.195963+00:00`
- Output dir: `scripts\simulation\output\_tmp_cold_step2`

## Schema Summary

- Channels table: `channels`
- Videos table: `videos`
- Video stats table: `video_stats`
- Ranking tables detected (9): `daily_ranking`, `daily_ranking_shorts`, `daily_ranking_video`, `monthly_ranking`, `monthly_ranking_shorts`, `monthly_ranking_video`, `weekly_ranking`, `weekly_ranking_shorts`, `weekly_ranking_video`
- Relevant columns:
  - `channels`: `added_at`, `channel_id`, `channel_name`, `empty_streak`, `group_name`, `is_tracked`, `last_checked_at`, `paused_until`, `uploads_playlist_id`
  - `videos`: `added_at`, `channel_icon_url`, `channel_id`, `channel_name`, `content_type`, `duration_seconds`, `group_name`, `published_at`, `tags_text`, `title`, `video_id`
  - `video_stats`: `id`, `like_count`, `timestamp`, `video_id`, `view_count`
  - `daily_ranking`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `daily_ranking_shorts`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `daily_ranking_video`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `monthly_ranking`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `monthly_ranking_shorts`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `monthly_ranking_video`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `weekly_ranking`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `weekly_ranking_shorts`: `calculated_at`, `rank`, `video_id`, `view_growth`
  - `weekly_ranking_video`: `calculated_at`, `rank`, `video_id`, `view_growth`

## Assumptions

- current baseline assumes all channels refreshed every 4h
- refresh cost estimated with fixed per-channel discovery unit cost

## Scoring Rules Used

- +40 if >=1 new video in 48h
- +20 if >=1 new video in 7d
- +25 if ranking appearance in 7d
- +10 if ranking appearance in 30d
- +20 if 48h view growth > 50000
- +10 if rankable rate >= 0.40
- -20 if no new videos in 14d
- -40 if no new videos in 30d
- -15 if rankable rate < 0.10

## Tier Distribution

- Total channels: **381**
- Hot: **0**
- Warm: **0**
- Cold: **0**

## Refresh / API Cost Estimate

- Simulated daily refresh jobs: **2286.00**
- Simulated weekly refresh jobs: **16002.00**
- Simulated daily API cost: **2286.00**
- Simulated weekly API cost: **16002.00**
- Current (fixed) daily API cost: **2286.00**
- Current (fixed) weekly API cost: **16002.00**
- Estimated savings ratio: **0.00%**

## Coverage / Risk Highlights

- Risk-flagged channels: **0**
- No risk channels were flagged under current rules.

## Promotion / Demotion Suggestions

- Promote candidates: none
- Demote candidates: none

## Missing Inputs / Fallbacks

- None

## Recommendations

- Validate 10-20 risk-flagged channels manually before applying tiered scheduling in production.
- Tune view-growth threshold and hot cap to keep ranking-sensitive channels in hot/warm tiers.
- Run this simulation weekly and compare drift in tier counts and risk set size.
