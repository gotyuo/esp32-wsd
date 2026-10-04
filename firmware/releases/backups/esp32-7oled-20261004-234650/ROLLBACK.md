# 回退到 20261004-234650 备份

如果新版固件异常，使用下方命令恢复备份中的固件。

## 先确认串口
```bash
ls -l /dev/ttyACM0
```

## 回退烧录
```bash
cd /vol1/esp/esp32-wsd
python3 -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 write_flash --flash_mode dio --flash_freq 40m --flash_size detect \
  0x00000000 firmware/releases/backups/esp32-7oled-20261004-234650/bootloader.bin \
  0x00008000 firmware/releases/backups/esp32-7oled-20261004-234650/partitions.bin \
  0x00010000 firmware/releases/backups/esp32-7oled-20261004-234650/rollback-firmware.bin
```

## 校验备份
```bash
cd /vol1/esp/esp32-wsd/firmware/releases/backups/esp32-7oled-20261004-234650
sha256sum -c SHA256SUMS.ROLLBACK
```

## 备注
- 如果系统里 esptool 不可用，可使用 PlatformIO 烧录对应备份目录中的三件套。
- 回退成功后建议再读串口日志确认设备启动。