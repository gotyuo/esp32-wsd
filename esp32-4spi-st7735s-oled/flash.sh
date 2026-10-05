#!/usr/bin/env bash
# ============================================================
#  ESP32-S3 + ST7735S 0.96" 4-wire SPI TFT  -  one-click flash
#  Source: esp32-wsd / esp32-4spi-st7735s-oled
#  Usage : ./flash.sh /dev/ttyUSB0
#
#  IMPORTANT: always erase-flash first.
#  Overwriting without erase leaves old firmware residue and
#  makes esptool fail with "MD5 of file does not match data".
# ============================================================
set -e
cd "$(dirname "$0")"

PORT="${1:-/dev/ttyUSB0}"

echo
echo "=========================================================="
echo " Target port : $PORT"
echo " Files       : bootloader.bin  partitions.bin  firmware.bin"
echo " Chip        : ESP32-S3   Screen: 0.96\" ST7735S 160x80"
echo " Pins        : SCK=12 MOSI=11 CS=10 DC=7 RST=6 BL=5"
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
echo " * Screen should now show the EnvMon UI (not just backlight)."
echo " * Serial log (115200) needs hardware UART0 on GPIO43/44."
echo " * Never flash envmon_esp32s3_oled.bin here (I2C OLED only)."
