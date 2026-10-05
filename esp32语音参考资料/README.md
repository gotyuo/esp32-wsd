ESP 烧录文件包（ESP32 + ESP8266）
===============================

生成时间: 2026-10-05
说明: 两台设备，一个是 ESP32，一个是 ESP8266。当前插在 **COM7** 的是 ESP32。

目录结构
--------
esp_flash_package/
├── README.md                  (本文件)
├── esp32_com7/                ← ESP32 设备（当前在 COM7）
│   ├── bootloader.bin         (14KB)
│   ├── partitions.bin         (3KB)
│   ├── firmware.bin           (863KB)  ESP32-S3 envmon v1.2.0 (TFT7735 变体)
│   ├── flash_com7_esp32.py    COM7 烧录脚本
│   └── README_esp32.md
└── esp8266/                   ← ESP8266 设备（历史在 COM6，非 COM7）
    ├── firmware_pin_scan_2026-09-14.bin   (299KB)
    ├── firmware_audio_test_2026-09-14.bin (301KB)
    ├── max30102_esp8266.ino   (59KB, 主固件源码，未编译)
    ├── flash_esp8266.py        COM6 烧录脚本（端口可用参数覆盖）
    └── README_esp8266.md

【ESP32 这台（COM7）的重要说明】
- 固件来源: icu/esp32-wsd（ICU 监护仪），发布基线 v1.2.0，构建目标 esp32-s3-tft7735。
- 该变体开启 ARDUINO_USB_CDC_ON_BOOT=1 → 设备走**原生 USB CDC**（不需要 CH340），
  COM7 显示为 \Device\USBSER000 正相符。
- 烧录偏移（与官方 flash.sh / README 一致）：
    bootloader.bin @ 0x00000000
    partitions.bin @ 0x00008000
    firmware.bin   @ 0x00010000
  （工作区那份 partitions_ota.csv 把 ota_0 改到 0x12000，与已发布 bin 不匹配，勿用。）
- 没有单独的 boot_app0，按官方脚本 3 段直刷即可。
- 该 bin 是 TFT7735 屏变体；若你的 ESP32 板是 OLED 或不是 S3，需重新构建对应变体。

【ESP8266 这台（COM6）的重要说明】
- 固件来源: max30102-esp8266-fw。已编译的是两个测试/工具固件（pin_scan / audio_test）。
- 主固件 max30102_esp8266.ino（Web UI/MAX30102/HL7/波形）当前无预编译产物。
- 端口历史为 COM6；若现在插在别的口，用参数指定：flash_esp8266.py scan COMx

【通用烧录要点】
- 两台设备本机都无 DTR/RTS 自动复位，需手动进 download 模式（见各子目录 README）。
- 所有脚本使用 .workbuddy\binaries\python\envs\esp 里的 esptool（已验证可用）。
