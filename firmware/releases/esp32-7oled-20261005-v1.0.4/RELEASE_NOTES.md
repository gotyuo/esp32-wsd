# esp32-7oled 20261005-v1.0.4

## 变更
- 运行时配置迁移：若设备保存的 MQTT/TTS 目标仍为旧节点 172.22.22.83，则启动时纠偏到已修复本机节点 172.22.22.75。
- 保留原有 WiFi、阈值、设备 ID 配置；不影响 TFT 显示和传感器链路。
- 配合服务端 TTS 最终兜底：/api/tts/speak 总返回 audio/wav。

## 回退
覆盖前备份包位于 firmware/releases/backups/esp32-7oled-<timestamp>/。
回退使用 rollback-firmware.bin 对应地址重新烧录，或恢复前一版本 release 包。
