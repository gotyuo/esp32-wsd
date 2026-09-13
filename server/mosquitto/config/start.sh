#!/bin/sh
set -e

# Mosquitto 启动脚本：自动生成 passwd 文件（如果不存在）
# 由 docker-compose 挂载并执行，避免 YAML 引号转义问题

# 确保数据目录存在（persistence_location 和 passwd 文件都需要）
mkdir -p /mosquitto/data /mosquitto/log

PASSWD_FILE="/mosquitto/data/passwd"

# 生成 passwd 文件（如果不存在或不含当前用户）
if [ ! -f "$PASSWD_FILE" ] || ! grep -q "^${MQTT_USER}:" "$PASSWD_FILE" 2>/dev/null; then
  echo "[init] Generating mosquitto passwd for ${MQTT_USER}..."
  mosquitto_passwd -b "$PASSWD_FILE" "$MQTT_USER" "$MQTT_PASS"
  echo "[init] passwd generated."
else
  echo "[init] passwd file exists, skipping generation."
fi

echo "[init] Starting mosquitto..."
exec mosquitto -c /mosquitto/config/mosquitto.conf
