# esp32-7oled 20261005-v1.0.7

## 变更
- TTS WAV 接收上限从 128KB 提升到 256KB。
- 背景：v1.0.6 已能读取 TTS HTTP 响应头，但服务端返回 WAV content-length=152910 超过 131072，被固件拒绝。
- 保持 v1.0.6 的响应头截止时间轮询读取逻辑。

## 回退
覆盖前备份包位于 firmware/releases/backups/esp32-7oled-<timestamp>/。
