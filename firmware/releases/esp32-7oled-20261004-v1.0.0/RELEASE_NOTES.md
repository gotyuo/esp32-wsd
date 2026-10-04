# ESP32-7OLED 20261004-v1.0.0

## 本版新增
- 支持温湿度语音播报，在无报警且联网时周期性播报当前温湿度。
- TTS I2S 引脚避让无源喇叭 PWM，避免与现有扬声器引脚冲突。
- 新增本地 PlatformIO 平台/包缓存配置，减少重复联网下载。

## 固件版本
- `FW_VERSION = 20261004-v1.0.0`

## 烧录方式
- 推荐使用 PlatformIO：
  `pio run -e esp32-7oled -t upload --upload-port /dev/ttyACM0`
- 也可使用 esptool 手动写入 `firmware.bin`。

## 校验
- 目录内附 `SHA256SUMS`，可用 `sha256sum -c` 校验固件文件。
