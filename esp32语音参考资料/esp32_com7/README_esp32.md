ESP32-S3 烧录说明（COM7）
=====================

固件: envmon v1.2.0（构建目标 esp32-s3-tft7735）
设备: ESP32-S3 DevKitC，原生 USB CDC（USBSER，COM7）

【进入 download 模式（原生 USB 版）】
  1) 按住 BOOT 键（GPIO0）
  2) 点一下 RESET 键
  3) 松开 BOOT 键
  —— 芯片停在 download 模式

【烧录】
  python flash_com7_esp32.py
  脚本会在 120s 窗口内反复尝试连接并写入：
    bootloader.bin @ 0x0
    partitions.bin @ 0x8000
    firmware.bin   @ 0x10000   (dio / 40m / size=detect)
  成功后按一下 RESET 键运行（首次进入配网模式）。

【若失败】
  - 确认 COM7 确实是这台 ESP32-S3（设备管理器看 USBSER）
  - BOOT/RESET 时序：先按住 BOOT，再点 RESET，再松 BOOT
  - 换 USB 数据线（部分线仅充电）

【注意】
  - 此 bin 为 TFT7735 彩屏变体；若为 OLED 屏需另构建 esp32-s3-oled 变体。
  - 工作区 partitions_ota.csv（ota_0@0x12000）与已发布 bin 不一致，烧录请以本包偏移（0x10000）为准。
