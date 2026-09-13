#!/bin/sh
# Mosquitto 启动脚本：自动生成 passwd 文件（如果不存在）
# 由 docker-compose 挂载并执行，避免 YAML 引号转义问题
PASSWD_FILE="/mosquitto/config/passwd"

if [ ! -f "$PASSWD_FILE" ] || ! grep -q "^${MQTT_USER}:" "$PASSWD_FILE" 2>/dev/null; then
  echo "[init] Generating mosquitto passwd for ${MQTT_USER}..."
  mosquitto_passwd -b "$PASSWD_FILE" "$MQTT_USER" "$MQTT_PASS" 2>&1
  echo "[init] passwd generated."
else
  echo "[init] passwd file exists, skipping generation."
fi

echo "[init] Starting mosquitto..."
exec mosquitto -c /mosquitto/config/mosquitto.conf
