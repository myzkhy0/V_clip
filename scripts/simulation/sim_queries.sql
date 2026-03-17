-- Simulation-only read queries for hot/warm/cold analysis
-- These queries are reference-only and do not mutate production tables.

-- 1) Schema inventory
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;

-- 2) Channel recent upload features
SELECT
  channel_id,
  MAX(published_at) AS latest_video_published_at,
  COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '48 hours') AS recent_video_count_48h,
  COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '7 days') AS recent_video_count_7d,
  COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '30 days') AS recent_video_count_30d
FROM videos
GROUP BY channel_id;

-- 3) 48h view growth by channel
WITH growth AS (
  SELECT video_id, GREATEST(MAX(view_count) - MIN(view_count), 0) AS view_growth
  FROM video_stats
  WHERE timestamp >= NOW() - INTERVAL '48 hours'
  GROUP BY video_id
)
SELECT
  v.channel_id,
  COALESCE(SUM(g.view_growth), 0) AS recent_view_growth_48h
FROM videos v
LEFT JOIN growth g ON g.video_id = v.video_id
WHERE v.published_at >= NOW() - INTERVAL '48 hours'
GROUP BY v.channel_id;
