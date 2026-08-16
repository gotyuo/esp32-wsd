#!/usr/bin/env bash
# ============================================================
# 本地直接运行（不用 Docker，便于调试）
# 需要 Python 3.9+，且已有一个可用的 MQTT Broker（可选）
# ============================================================
set -e
cd "$(dirname "$0")/.."

python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -r requirements.txt

export DB_PATH="${DB_PATH:-data/envmon.db}"
export SCHEMA_FILE="${SCHEMA_FILE:-schema.sql}"
export MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
export MQTT_PORT="${MQTT_PORT:-18830}"
export MQTT_USER="${MQTT_USER:-}"
export MQTT_PASS="${MQTT_PASS:-}"

echo ">> 启动后端 http://127.0.0.1:8627"
uvicorn app.main:app --host 0.0.0.0 --port 8627
