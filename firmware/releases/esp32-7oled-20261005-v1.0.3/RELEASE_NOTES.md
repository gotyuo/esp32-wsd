# esp32-7oled 20261005-v1.0.3

## 变更
- 添加 TTS POST 诊断日志：打印目标 host/port、写入字节数、响应头片段
- 保留 `20261005-v1.0.2` 的静态 TTS header buffer，避免 loopTask 栈溢出
- 版本升级：`20261005-v1.0.2` -> `20261005-v1.0.3`

## 当前验证结论
设备串口已显示：
- `POST 172.22.22.83:12090 wrote 204/204 hdr+len`
- `header incomplete len=0 peer_connected=1`

说明设备侧 POST 已完整发出，但 1 秒内未收到响应头。进一步验证发现：
- `172.22.22.75:12090/api/tts/speak` 返回 200 audio/wav
- `172.22.22.83:12090/api/tts/speak` 返回 500 JSON
- `.83` 500 内容：`TTS 合成失败: [Errno -5] No address associated with hostname`

## 回退
当前回退包：
- `firmware/releases/backups/esp32-7oled-20261005-002550/`

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
