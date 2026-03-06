-- ============================================================
-- VTuber Clip Ranking System — Database Schema
-- PostgreSQL
-- ============================================================

-- Tracked VTuber channels
CREATE TABLE IF NOT EXISTS channels (
    channel_id          VARCHAR(64)  PRIMARY KEY,
    channel_name        VARCHAR(256) NOT NULL,
    group_name          VARCHAR(128) NOT NULL,
    uploads_playlist_id VARCHAR(64)  NOT NULL DEFAULT '',
    is_tracked          BOOLEAN      NOT NULL DEFAULT TRUE,
    added_at            TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Discovered clip videos
CREATE TABLE IF NOT EXISTS videos (
    video_id         VARCHAR(32)  PRIMARY KEY,
    title            TEXT         NOT NULL,
    channel_id       VARCHAR(64)  NOT NULL,
    channel_name     VARCHAR(256) NOT NULL,
    group_name       VARCHAR(128) NOT NULL DEFAULT '',
    published_at     TIMESTAMP    NOT NULL,
    duration_seconds INTEGER      NOT NULL,
    tags_text        TEXT         NOT NULL DEFAULT '',
    channel_icon_url TEXT         NOT NULL DEFAULT '',
    content_type     VARCHAR(16)  NOT NULL DEFAULT 'video',
    added_at         TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_videos_published_at
    ON videos (published_at);

CREATE INDEX IF NOT EXISTS idx_videos_channel_id
    ON videos (channel_id);

-- Hourly view / like snapshots
CREATE TABLE IF NOT EXISTS video_stats (
    id          SERIAL       PRIMARY KEY,
    video_id    VARCHAR(32)  NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    timestamp   TIMESTAMP    NOT NULL DEFAULT NOW(),
    view_count  BIGINT       NOT NULL DEFAULT 0,
    like_count  BIGINT       NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_video_stats_video_ts
    ON video_stats (video_id, timestamp);

-- Ranking tables (24 h / 7 d / 30 d)
CREATE TABLE IF NOT EXISTS daily_ranking (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS weekly_ranking (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS monthly_ranking (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);
-- Split ranking tables by content type
CREATE TABLE IF NOT EXISTS daily_ranking_shorts (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS weekly_ranking_shorts (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS monthly_ranking_shorts (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS daily_ranking_video (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS weekly_ranking_video (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS monthly_ranking_video (
    video_id      VARCHAR(32) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    view_growth   BIGINT      NOT NULL DEFAULT 0,
    rank          INTEGER     NOT NULL,
    calculated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (video_id, calculated_at)
);

