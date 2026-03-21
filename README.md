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
| `ENABLE_COLD_SCHEDULING` | `1` で cold 判定によるチャンネル更新間引きを有効化（既定 `0`） |
| `COLD_REFRESH_HOURS` | cold 判定チャンネルの更新間隔（時間, 例 `72`） |
| `COLD_MIN_INACTIVE_DAYS` | cold 判定の最小 inactive 日数（既定 `14`） |
| `COLD_RECENT_GROWTH_7D_MAX` | cold 判定の 7日再生増分しきい値（既定 `5000`） |
| `COLD_MIN_OBSERVED_VIDEOS` | cold 判定の最小観測動画本数（既定 `1`） |
| `COLD_MIN_CHANNEL_AGE_DAYS` | 観測不足時の代替条件となるチャンネル経過日数（既定 `14`） |
| `COLD_MANUAL_PROTECT_FILE` | cold 対象から除外する channel_id リストファイル |

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

### 4.5 (Optional) 個人勢URLを一括登録

`other_channels.txt` に 1 行 1 URL で記載して、以下を実行します。

```bash
python bulk_register_other.py
```

別ファイルを使う場合:

```bash
python bulk_register_other.py my_channels.txt --group other
```

内部的には各行ごとに `register_channel.py add ... --group other` を順番実行します。
`channels` テーブルは `ON CONFLICT` で upsert しているため、重複投入しても安全です。
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
├── bulk_register_other.py # other_channels.txt を順次登録する一括登録ツール
├── other_channels.txt     # 個人勢チャンネルURL管理（1行1URL）
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
`KEYWORD_SEARCH_BATCH_SIZE` を小さくすると、1回の実行で回すキーワード数を減らせます（ローテーション実行）。

## License

MIT

## Deploy Script

Lightsail 等のサーバーで更新反映を簡略化するため、`deploy.sh` を追加しています。

```bash
cd /opt/vclip
bash deploy.sh
```

環境変数で上書き可能です: `APP_DIR`, `SERVICE_NAME`, `BRANCH`。




## Simulation: Hot/Warm/Cold Refresh Analysis

本番スケジューラを変更せずに、既存DBを読み取り分析して
`hot / warm / cold` の更新戦略をシミュレーションするツールです。

- 実装場所: `scripts/simulation/`
- 本番ロジックへの影響: なし（読み取り専用）
- 出力: Markdown / CSV / JSON レポート

### Run

```bash
python scripts/simulation/simulate_channel_priority.py
```

### GUI Run (Local Web UI)

```bash
python scripts/simulation/sim_gui.py
```

ブラウザで `http://127.0.0.1:8765` を開くと、パラメータ入力フォームから
シミュレーションを実行できます。

Windows では `open_simulation_gui.bat` をダブルクリックで一発起動できます。

実行すると既定で次に出力されます:

- `scripts/simulation/output/<timestamp>/simulation_summary.md`
- `scripts/simulation/output/<timestamp>/channel_tier_simulation.csv`
- `scripts/simulation/output/<timestamp>/simulation_metrics.json`
- `scripts/simulation/output/<timestamp>/risk_channels.csv`

### Main Assumptions

- 現在戦略（比較対象）: 全チャンネルを 4 時間ごとに更新
- シミュレーション戦略:
  - hot: 4時間ごと
  - warm: 24時間ごと
  - cold: 72時間ごと
- チャンネルごとのディスカバリ更新コストは固定値（既定: `1.0`）

### Score Rules and Threshold Tuning

主な調整パラメータ:

- `--hot-threshold` (既定 `80`)
- `--warm-threshold` (既定 `30`)
- `--hot-cap` (任意。hot上限件数)
- `--strategy-mode` (`cold_only` または `full`, 既定 `cold_only`)
- `--cold-recent-growth-7d-max` (既定 `5000`)
- `--cold-min-inactive-days` (既定 `21`)
- `--cold-min-channel-age-days` (既定 `14`)
- `--cold-min-observed-videos` (既定 `3`)
- `--manual-protect-file` (任意。cold除外チャネルIDファイル)
- `--view-growth-threshold-48h` (既定 `50000`)
- `--rankable-rate-high` (既定 `0.40`)
- `--rankable-rate-low` (既定 `0.10`)

例:

```bash
python scripts/simulation/simulate_channel_priority.py \
  --hot-cap 120 \
  --view-growth-threshold-48h 80000 \
  --discovery-refresh-unit-cost 100
```

### Compare Current vs Simulated Strategy

`simulation_metrics.json` に以下が出力されます:

- `estimated_daily_api_cost` / `estimated_weekly_api_cost`
- `current_strategy_daily_api_cost` / `current_strategy_weekly_api_cost`
- `estimated_savings_ratio`

この値を使って、現在の固定4時間戦略との差分を評価できます。

### Notes

- スキーマの存在を自動検出して、足りない列があればレポートに `Missing Inputs` として明示します。
- スキーマ差異がある場合でも可能な範囲で推定を続行します。
