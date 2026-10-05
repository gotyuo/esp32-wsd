# esp32-7oled 20261005-v1.0.6

## 变更
- 修复 TTS 响应头读取：改为按截止时间轮询，避免一次性 `read()` 返回 0 后提前退出。
- 保持旧 MQTT/TTS 目标迁移逻辑不变。
- 补 `<stdint.h>`，修正 `uint32_t` 编译问题。

## 回退
覆盖前备份包位于 firmware/releases/backups/esp32-7oled-<timestamp>/。
