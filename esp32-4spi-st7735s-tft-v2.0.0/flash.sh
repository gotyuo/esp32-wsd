#!/usr/bin/env bash
# ============================================================
#  ESP32-S3 + ST7735S 0.96" 4-wire SPI TFT  -  firmware v2.0.0
#  Source: esp32-wsd / esp32-4spi-st7735s-tft-v2.0.0
#  Usage : ./flash.sh /dev/ttyUSB0
#
#  IMPORTANT: always erase-flash first.
#  Overwriting without erase leaves old firmware residue and
#  makes esptool fail with "MD5 of file does not match data".
#
#  If this version misbehaves, fall back to the sibling folder
#  ../esp32-4spi-st7735s-oled (v1.0.0, verified). Same wiring.
# ============================================================
set -e
cd "$(dirname "$0")"

PORT="${1:-/dev/ttyUSB0}"

echo
echo "=========================================================="
echo " Firmware   : TFT7735 v2.0.0  (static-check OK, not yet"
echo "              verified on hardware - prefer v1.0.0)"
echo " Target port: $PORT"
echo " Files      : bootloader.bin  partitions.bin  firmware.bin"
echo " Chip       : ESP32-S3   Screen: 0.96\" ST7735S 160x80"
echo " Pins       : SCK=12 MOSI=11 CS=10 DC=7 RST=6 BL=5"
echo "=========================================================="
echo

echo "[1/3] Erasing whole flash (removes old firmware residue)..."
esptool --chip esp32s3 --port "$PORT" erase-flash

echo
echo "[2/3] Writing bootloader / partitions / firmware ..."
esptool --chip esp32s3 --port "$PORT" --baud 115200 write-flash \
    0x0     bootloader.bin \
    0x8000  partitions.bin \
    0x10000 firmware.bin

echo
echo "[3/3] DONE - board has been reset."
echo
echo " * Screen should show the EnvMon UI (not just backlight)."
echo " * Serial log needs hardware UART0 (GPIO43/44); USB CDC is off."
echo " * If it fails, reflash ../esp32-4spi-st7735s-oled (v1.0.0)."
