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

個人勢は `register_channel.py` で手動登録するのがおすすめです。

```bash
# 追加（URL / @handle / channel_id 対応）
python register_channel.py add "https://www.youtube.com/@example" --name "個人勢A" --group "other"

# 一覧
python register_channel.py list --tracked-only

# 追跡ON/OFF
python register_channel.py set-tracked UCxxxxxx --enabled
python register_channel.py set-tracked UCxxxxxx --disabled
```
### 5. Run

```bash
# 継続実行 (現在は 4 時間ごと)
python scheduler.py

# DB確認 + テストサイト起動（Windows）
open_test_site.bat

# 互換ランチャー（内部で open_test_site.bat を呼び出し）
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
├── register_channel.py    # チャンネル手動登録/追跡ON-OFFツール
├── open_test_site.bat     # テストサイトをブラウザで開く Windows 用ランチャー
├── check_postgres.py      # DATABASE_URL に対する PostgreSQL 接続確認
├── start_local_postgres.bat # 同梱したローカル PostgreSQL を起動
├── stop_local_postgres.bat  # 同梱したローカル PostgreSQL を停止
├── run_test_site_once.bat # 互換ランチャー（open_test_site.bat を呼び出し）
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

`open_test_site.bat` は `.env` の存在確認、`DATABASE_URL` に対する PostgreSQL 接続確認、`scheduler.py --init-db`、ブラウザ起動、`test_site.py` 起動までを順に実行します。
同梱した `.postgresql` がある場合は、DB 未起動時に `start_local_postgres.bat` で自動起動を試みます。

前提:

- PostgreSQL が起動していること
- `.env` に `DATABASE_URL` が設定されていること
- `daily_ranking` / `weekly_ranking` / `monthly_ranking` にデータがあること

データがまだ無い場合は、スケジューラ常駐（`python scheduler.py`）の初回実行で順次蓄積されます。

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

## Deploy Script

Lightsail 等のサーバーで更新反映を簡略化するため、`deploy.sh` を追加しています。

```bash
cd /opt/vclip
bash deploy.sh
```

環境変数で上書き可能です: `APP_DIR`, `SERVICE_NAME`, `BRANCH`。

