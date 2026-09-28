# EnvMon ESP32-S3 (TFT7735) - ESP32-20260929 发布说明

## 主要变更
- 在 v2.0.3 基线上补充 TTS 语音播报链路。
- 主循环新增 TTS 接收、HTTP WAV 拉流、I2S 播放。
- 保留原有提示音 `playTtsAlert(level)`。

## 版本信息
- 发布名称：`esp32-20260929`
- 发布目录：`firmware/releases/esp32-20260929/`

## 发布产物
- `firmware.bin`
- `firmware.sha256`
- `flash.sh`
- `partitions_ota.csv`
- `platformio.ini`
- `README.md`

## 验证结果
- PlatformIO 编译成功
- 本次仅改动 7oled 的音频链，不动显示初始化顺序
