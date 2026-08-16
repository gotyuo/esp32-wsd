#!/usr/bin/env bash
# ============================================================
#  ESP32-S3 固件烧录脚本（Linux/macOS）
#  用法: bash scripts/flash_firmware.sh [/dev/ttyACM0]
#  依赖: pip install esptool
# ============================================================
set -e
PORT="${1:-/dev/ttyACM0}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo ">> 烧录完整固件镜像到 ${PORT} ..."
python3 -m esptool --chip esp32s3 --port "$PORT" --baud 460800 \
    write_flash -z 0x0 "$HERE/../firmware/release/envmon-v1.2.0-full.bin"
echo ">> 完成！设备将自动启动，首次运行进入配网模式。"
