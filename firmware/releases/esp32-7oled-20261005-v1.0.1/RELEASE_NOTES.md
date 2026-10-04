# esp32-7oled 20261005-v1.0.1

## 变更
- 修复 TTS 拉取失败：设备端不再使用 `GET /api/tts/speak?text=...`
- 改为 `POST /api/tts/speak`，body 为 JSON `{"text":"..."}`
- 保留 MQTT 下发 TTS 文本的播报路径
- 保留每 30 秒温湿度语音播报
- 版本升级：`20261004-v1.0.0` -> `20261005-v1.0.1`

## 回退
当前回退包：
- `firmware/releases/backups/esp32-7oled-20261005-001450/`

回退固件：
- `rollback-firmware.bin`
- `bootloader.bin`
- `partitions.bin`
- `SHA256SUMS.ROLLBACK`

## 烧录
```bash
cd /vol1/esp/esp32-wsd
/home/hotyuo/envmon-firmware/.venv/bin/pio run -e esp32-7oled -t upload --upload-port /dev/ttyACM0
```
