# esp32-7oled 20261005-v1.0.2

## 变更
- 修复 TTS HTTP 头读取导致的 `loopTask` 栈溢出
- 将 TTS 响应头缓冲从栈上数组改为静态缓冲区 `_ttsHdr[4096]`
- 保留 `20261005-v1.0.1` 的 POST TTS API 修复
- 版本升级：`20261005-v1.0.1` -> `20261005-v1.0.2`

## 回退
当前回退包：
- `firmware/releases/backups/esp32-7oled-20261005-002203/`

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
