# ESP32 温度声音固件发布包 — 20261005-v1.0.11

这个独立文件夹用于后期直接烧录当前温度/声音 ESP32-S3 固件，避免污染其他工程。

## 版本信息

- 设备: EnvMon ESP32-S3 ST7735 TFT 7oled
- 分支: `release/esp32-7oled-v2.0.3`
- 版本: `20261005-v1.0.11`
- Git tag: `20261005-v1.0.11`
- Git commit: `54a59eda3964600d0c7240fa75a9dff0bf2f38c6`
- 主要修复: 后端 TTS WAV 按 RIFF chunk 解析，兼容 `LIST/INFO` 后移 `data` chunk。

## 目录说明

```text
esp32-7oled-20261005-v1.0.11/
├── README.md                 # 当前说明文档
├── WIRING.md                 # 引脚对应接线顺序
├── FLASH_INSTRUCTIONS.md     # 烧录说明
├── RELEASE_NOTES.md          # 发布说明快照
├── SHA256SUMS                # 烧录文件校验
├── flash_esp32_temp_sound.sh # Linux/macOS 一键烧录脚本
├── firmware_bin/
│   ├── bootloader.bin        # 必烧 @0x00000000
│   ├── partitions.bin        # 必烧 @0x00008000
│   ├── firmware.bin          # 必烧 @0x00010000
│   └── firmware.elf          # 调试符号，不烧录
└── source_snapshot/
    └── esp32-7oled/          # 当前版本源码快照，便于复现和审计
```

## 烧录必须的文件

必须烧录这 3 个文件，不能拼接成一个大文件：

| 文件 | Flash 地址 | 用途 |
|---|---:|---|
| `firmware_bin/bootloader.bin` | `0x00000000` | ESP32-S3 bootloader |
| `firmware_bin/partitions.bin` | `0x00008000` | OTA/分区表 |
| `firmware_bin/firmware.bin` | `0x00010000` | 应用固件 |

## 快速烧录

连接 USB 后执行：

```bash
cd esp32-temperature-sound-release/esp32-7oled-20261005-v1.0.11
./flash_esp32_temp_sound.sh
```

如果串口不是 `/dev/ttyACM0`：

```bash
./flash_esp32_temp_sound.sh /dev/ttyACM1
```

如果没有 esptool：

```bash
python3 -m pip install esptool
```

## 接线顺序

设备接线请先阅读 `WIRING.md`。关键顺序：

1. 先接 GND 和 3.3V，确认供电稳定。
2. 接 I2C 传感器：AHT20/BMP280 到 GPIO8/GPIO9。
3. 接 ST7735 屏幕：SCK=12, MOSI=11, CS=10, DC=7, RST=6, BLK=5。
4. 接 RGB LED、蜂鸣器、喇叭。
5. 最后接 USB/串口烧录线。

## 验证

烧录后打开串口 `115200`，应看到：

```text
EnvMon ESP32-S3 (TFT7735) 20261005-v1.0.11
```

校验烧录文件：

```bash
cd esp32-temperature-sound-release/esp32-7oled-20261005-v1.0.11
sha256sum -c SHA256SUMS
```
