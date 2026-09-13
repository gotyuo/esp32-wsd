#!/bin/sh
# Mosquitto 启动脚本（免认证模式）

mkdir -p /mosquitto/data /mosquitto/log

echo "[init] Starting mosquitto (allow_anonymous=true)..."
exec mosquitto -c /mosquitto/config/mosquitto.conf
