#!/usr/bin/env bash
set -euo pipefail
PORT="${1:-/dev/ttyACM0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW_DIR="$SCRIPT_DIR/firmware_bin"
BAUD="${BAUD:-921600}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3" >&2
  exit 1
fi

if ! python3 -m esptool --version >/dev/null 2>&1; then
  echo "[错误] 未安装 esptool。请先执行: python3 -m pip install esptool" >&2
  exit 1
fi

for f in bootloader.bin partitions.bin firmware.bin; do
  if [ ! -f "$FW_DIR/$f" ]; then
    echo "[错误] 缺少烧录文件: $FW_DIR/$f" >&2
    exit 1
  fi
done

if [ ! -e "$PORT" ]; then
  echo "[错误] 串口不存在: $PORT" >&2
  echo "       可指定其他串口，例如: ./flash_esp32_temp_sound.sh /dev/ttyACM1" >&2
  exit 1
fi

SUDO=""
if [ ! -w "$PORT" ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "[错误] 没有串口 $PORT 写权限，且未安装 sudo。请添加 dialout 组或修正权限。" >&2
    exit 1
  fi
fi

echo "=========================================="
echo " ESP32 温度声音固件烧录"
echo " 版本: 20261005-v1.0.11"
echo " 串口: $PORT"
echo " 波特率: $BAUD"
echo "=========================================="
echo "提示: 若提示 not in bootstrap mode，请按住 BOOT 后轻按 RESET，再松开 BOOT。"

$SUDO python3 -m esptool \
  --chip esp32s3 \
  --port "$PORT" \
  --baud "$BAUD" \
  write_flash \
  --flash_mode dio \
  --flash_freq 40m \
  --flash_size detect \
  0x00000000 "$FW_DIR/bootloader.bin" \
  0x00008000 "$FW_DIR/partitions.bin" \
  0x00010000 "$FW_DIR/firmware.bin"

echo "[完成] 烧录成功。打开串口 115200 查看: EnvMon ESP32-S3 (TFT7735) 20261005-v1.0.11"
