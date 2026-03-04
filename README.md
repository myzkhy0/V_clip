# VTuber Clip Ranking System

VTuber の切り抜き動画を YouTube Data API v3 で収集し、再生数の伸びでランキングするバックエンドシステムです。

## Features

- チャンネル別 & キーワード検索による動画収集
- 10 秒〜3 分のクリップのみをフィルタリング
- 1 時間ごとに再生数・いいね数のスナップショットを記録
- 24 時間 / 7 日間 / 30 日間の再生数伸びランキング
- APScheduler または cron による定期実行

## Requirements

- Python 3.11+
- PostgreSQL 14+
- YouTube Data API v3 key

## Quick Start

### 1. Clone & Install

```bash
cd V_clip
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

`.env` を編集して以下を設定:

| Variable | Description |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 キー |
| `DATABASE_URL` | PostgreSQL 接続文字列 (例: `postgresql://user:pass@localhost:5432/vclip`) |

### 3. Create Database

```bash
# PostgreSQL でデータベースを作成
createdb vclip

# テーブルを作成
python scheduler.py --init-db
```

### 4. (Optional) チャンネルを追加

`config.py` の `SEED_CHANNELS` にチャンネル ID を追加するか、直接 SQL で挿入:

```sql
INSERT INTO channels (channel_id, channel_name, group_name)
VALUES ('UCxxxxxx', 'Channel Name', 'hololive');
```

### 5. Run

```bash
# 一回だけ実行
python scheduler.py --once

# 継続実行 (1 時間ごと)
python scheduler.py

# ローカル確認用テストサイト
open_test_site.bat
run_test_site_once.bat

# 個別モジュールの実行
python collector.py          # 動画収集のみ
python stats_collector.py    # 統計スナップショットのみ
python ranking.py            # ランキング計算のみ
python test_site.py          # テストサイトのみ
```

## File Structure

```
V_clip/
├── config.py              # 設定 (API キー, DB URL, 定数)
├── db.py                  # DB 接続ヘルパー
├── youtube_client.py      # YouTube API ラッパー
├── collector.py           # 動画収集
├── stats_collector.py     # 統計スナップショット
├── ranking.py             # ランキング計算
├── scheduler.py           # スケジューラ (エントリポイント)
├── test_site.py           # 最新ランキングを表示するローカル確認ページ
├── open_test_site.bat     # テストサイトをブラウザで開く Windows 用ランチャー
├── check_postgres.py      # DATABASE_URL に対する PostgreSQL 接続確認
├── start_local_postgres.bat # 同梱したローカル PostgreSQL を起動
├── stop_local_postgres.bat  # 同梱したローカル PostgreSQL を停止
├── run_test_site_once.bat # DB初期化→1回収集→テストサイト起動の一括実行
├── schema.sql             # PostgreSQL DDL
├── requirements.txt       # Python 依存パッケージ
├── cron_example.txt       # cron 設定例
├── .env.example           # 環境変数テンプレート
└── README.md
```

## Database Schema

| Table | Purpose |
|---|---|
| `channels` | 追跡対象の VTuber チャンネル |
| `videos` | 発見されたクリップ動画 |
| `video_stats` | 1 時間ごとの再生数/いいね数スナップショット |
| `daily_ranking` | 24 時間再生数伸びランキング |
| `weekly_ranking` | 7 日間再生数伸びランキング |
| `monthly_ranking` | 30 日間再生数伸びランキング |

## Ranking Query Example

```sql
-- 24 時間ランキング Top 20
SELECT
    r.rank,
    v.title,
    v.channel_name,
    v.group_name,
    r.view_growth,
    r.calculated_at
FROM daily_ranking r
JOIN videos v ON r.video_id = v.video_id
ORDER BY r.rank
LIMIT 20;
```

## Local Test Site

Windows では `open_test_site.bat` をダブルクリックすると、`http://127.0.0.1:8000/` でローカル確認ページを開けます。
DB 接続に失敗した場合も、ブラウザ上に原因と確認項目が表示されます。

初回セットアップ込みで一気に試す場合は `run_test_site_once.bat` を使います。
これは `.env` の存在確認、`DATABASE_URL` に対する PostgreSQL 接続確認、`scheduler.py --init-db`、`scheduler.py --once`、ブラウザ起動、`test_site.py` 起動までを順に実行します。
同梱した `.postgresql` がある場合は、DB 未起動時に `start_local_postgres.bat` で自動起動を試みます。

前提:

- PostgreSQL が起動していること
- `.env` に `DATABASE_URL` が設定されていること
- `daily_ranking` / `weekly_ranking` / `monthly_ranking` にデータがあること

データがまだ無い場合は、先に `python scheduler.py --once` を実行してください。

## API Quota Notes

YouTube Data API v3 のデフォルトクォータは **10,000 units/day** です。

| API Call | Cost |
|---|---|
| `search.list` | 100 units |
| `videos.list` | 1 unit |

チャンネル数やキーワード数が多い場合はクォータに注意してください。
`config.py` の `SEARCH_MAX_RESULTS` で検索結果数を調整できます。

## License

MIT
