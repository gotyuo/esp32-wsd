#!/usr/bin/env bash
# ============================================================
#  EnvMon beacon 服务端 — Linux / macOS 一键启动
#  版本: 20261006-v1.1
#  用法: ./start-beacon.sh [出口网卡IP]
#        例: ./start-beacon.sh 192.168.2.220
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

IFACE="${1:-}"

echo "============================================================"
echo " EnvMon beacon 服务端  20261006-v1.1"
echo "============================================================"
echo " 配置: projects.json"
echo " 出口: ${IFACE:-(系统默认路由)}"
echo " 停止: Ctrl+C"
echo "============================================================"
echo

PY="$(command -v python3 || command -v python)"

if [[ -n "$IFACE" ]]; then
    exec "$PY" -u beacon_server.py --config projects.json --interface "$IFACE"
else
    exec "$PY" -u beacon_server.py --config projects.json
fi
