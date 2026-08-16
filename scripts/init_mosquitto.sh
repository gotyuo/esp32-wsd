#!/usr/bin/env bash
# ============================================================
# 初始化 Mosquitto 账号密码文件（首次部署执行一次）
# 用法: bash scripts/init_mosquitto.sh [用户名] [密码]
# 默认: envmon / envmon-secret（强烈建议修改）
# ============================================================
set -e
cd "$(dirname "$0")/.."

USER="${1:-envmon}"
PASS="${2:-envmon-secret}"
CONF_DIR="$(pwd)/mosquitto/config"
mkdir -p "$CONF_DIR" "$(pwd)/mosquitto/data"

echo ">> 生成 Mosquitto 密码文件: 用户 ${USER}"
# -b 批处理模式：密码直接作参数，避免交互式输入
docker run --rm -v "$CONF_DIR:/mosquitto/config" eclipse-mosquitto:2 \
    mosquitto_passwd -b -c /mosquitto/config/passwd "$USER" "$PASS"

chmod 600 "$CONF_DIR/passwd" 2>/dev/null || true
echo ">> 完成: $CONF_DIR/passwd"
echo ">> 请确保 docker-compose 中 MQTT_USER/MQTT_PASS 与之一致，"
echo ">> 并在设备配网页填写相同的 MQTT 账号密码。"
