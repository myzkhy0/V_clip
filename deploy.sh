#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vclip}"
SERVICE_NAME="${SERVICE_NAME:-vclip-scheduler}"
BRANCH="${BRANCH:-main}"

cd "$APP_DIR"

echo "[1/4] Pulling latest code ($BRANCH)..."
git pull origin "$BRANCH"

echo "[2/4] Activating virtualenv..."
source .venv/bin/activate

echo "[3/4] Running syntax check..."
python3 -m py_compile scheduler.py collector.py stats_collector.py ranking.py test_site.py youtube_client.py

echo "[4/4] Restarting service: $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "Deploy completed."
