# ESP32-20260929 烧录命令清单

## 1) PlatformIO 烧录（推荐）
```bash
cd /vol1/esp/esp32-wsd/firmware/esp32-7oled
. /home/hotyuo/envmon-firmware/.venv/bin/activate
pio run -e esp32-7oled
pio run -e esp32-7oled -t upload --upload-port /dev/ttyACM0
```

## 2) esptool 手动烧录
```bash
python3 -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 write_flash --flash_mode dio --flash_freq 40m --flash_size detect 0x00000000 firmware/releases/esp32-20260929/firmware_bin/bootloader.bin 0x00008000 firmware/releases/esp32-20260929/firmware_bin/partitions.bin 0x00010000 firmware/releases/esp32-20260929/firmware_bin/firmware.bin
```

## 3) 串口日志
```bash
python3 -m serial.tools.miniterm /dev/ttyACM0 115200
```

## 4) 校验和
```bash
sha256sum firmware/releases/esp32-20260929/firmware_bin/firmware.bin
```

## 备注
- 若端口权限不足，前面加 `sudo`。
- 烧录前确认设备进入下载模式（BOOT 按住后点 RESET）。
