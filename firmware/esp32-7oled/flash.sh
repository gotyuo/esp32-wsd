#!/usr/bin/env bash
# ============================================================
# EnvMon ESP32-S3 ST7735 TFT 固件 — 一键烧录脚本
# 用法: ./flash.sh [串口]
#   不传串口参数时自动检测 /dev/ttyACM0
#   例: ./flash.sh /dev/ttyACM1
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIRMWARE_DIR="$SCRIPT_DIR/firmware_bin"

# 串口（可用参数覆盖）
PORT="${1:-/dev/ttyACM0}"

# 波特率
BAUD=921600

# esptool 命令
ESPTOOL="python3 -m esptool"
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未找到 python3"
    exit 1
fi

# 检查 esptool
if ! python3 -m esptool --version &>/dev/null 2>&1; then
    echo "[提示] 正在检查 esptool..."
    python3 -m pip install esptool --quiet 2>/dev/null || \
    pip3 install esptool --quiet 2>/dev/null || \
    echo "[警告] esptool 未安装，尝试用 PlatformIO..."
fi

# 检查固件文件
for f in bootloader.bin partitions.bin firmware.bin; do
    if [ ! -f "$FIRMWARE_DIR/$f" ]; then
        echo "[错误] 缺少固件文件: $FIRMWARE_DIR/$f"
        exit 1
    fi
done

# 检查串口权限（esp32s3 通常需 sudo）
if ! [ -e "$PORT" ]; then
    echo "[错误] 串口不存在: $PORT"
    echo "       请检查 USB 连接，或指定其他串口: ./flash.sh /dev/ttyACM1"
    exit 1
fi

SUDO=""
if ! [ -w "$PORT" ]; then
    SUDO="sudo"
fi

echo "=========================================="
echo " EnvMon ESP32-S3 ST7735 TFT 固件烧录"
echo "=========================================="
echo " 串口:   $PORT"
echo " 波特率: $BAUD"
echo " 固件:"
echo "   bootloader.bin  @ 0x00000000"
echo "   partitions.bin  @ 0x00008000"
echo "   firmware.bin    @ 0x00010000"
echo "=========================================="
echo ""
echo "提示: 如果烧录失败，按住 BOOT 键 -> 按一下 RESET -> 松开 BOOT，再重试"
echo ""
read -r -p "按 Enter 开始烧录，或 Ctrl+C 取消..."

echo ""
echo ">>> 开始烧录..."
echo ""

$SUDO $ESPTOOL \
    --chip esp32s3 \
    --port "$PORT" \
    --baud "$BAUD" \
    write_flash \
    --flash_mode dio \
    --flash_freq 40m \
    --flash_size detect \
    0x00000000 "$FIRMWARE_DIR/bootloader.bin" \
    0x00008000 "$FIRMWARE_DIR/partitions.bin" \
    0x00010000 "$FIRMWARE_DIR/firmware.bin"

echo ""
echo "=========================================="
echo " 烧录完成！"
echo "=========================================="
echo ""
echo "下一步:"
echo "  1. 读取串口日志: sudo python3 -m serial.tools.miniterm $PORT 115200"
echo "  2. 配置 WiFi:   串口输入 config，连热点 192.168.4.1"
echo "  3. 查看状态:   串口输入 status"
echo ""
