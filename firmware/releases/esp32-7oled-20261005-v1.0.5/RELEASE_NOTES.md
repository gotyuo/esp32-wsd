# esp32-7oled 20261005-v1.0.5

## 变更
- 修复 TTS POST 响应头等待窗口过短：从 1 秒延长到 8 秒。
- 背景：设备 POST 已完整发出且本机后端返回 audio/wav，但 edge-tts 合成耗时超过 1 秒，旧窗口会提前判定 header incomplete。
- 保持 v1.0.4 的旧服务器 172.22.22.83 -> 172.22.22.75 运行时迁移。
- 不改动 TFT 显示、传感器、MQTT 上报逻辑。

## 回退
覆盖前备份包位于 firmware/releases/backups/esp32-7oled-<timestamp>/。
