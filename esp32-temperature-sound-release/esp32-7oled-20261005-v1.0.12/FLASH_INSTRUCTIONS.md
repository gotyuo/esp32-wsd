# 烧录说明

## 1. 确认串口

```bash
ls /dev/ttyACM*
```

## 2. PlatformIO 方式

如果在本机源码树中：

```bash
cd /vol1/esp/esp32-wsd/firmware/esp32-7oled
/home/hotyuo/envmon-firmware/.venv/bin/pio run -e esp32-7oled -t upload --upload-port /dev/ttyACM0
```

## 3. esptool 方式

```bash
python3 -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 write_flash \
  0x00000000 firmware_bin/bootloader.bin \
  0x00008000 firmware_bin/partitions.bin \
  0x00010000 firmware_bin/firmware.bin
```

## 4. 校验

```bash
sha256sum -c SHA256SUMS
```
