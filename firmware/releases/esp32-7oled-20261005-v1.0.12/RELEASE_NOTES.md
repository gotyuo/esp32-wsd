# ESP32 7OLED 20261005-v1.0.12

## 变更

- 修复 TTS I2S 播放状态机：不再在 phase 3 直接卸载未安装的 I2S 驱动。
- 新增 `_ttsI2sInstalled` 标志，只有成功安装 I2S 后才允许写 PCM / 卸载驱动。
- I2S 初始化与安装错误改为可观测日志：`init I2S`, `i2s_driver_install failed`, `i2s_set_pin failed`, `i2s_write failed`。
- 播放进度每 16384 字节打印一次 `playing X/Y bytes`，完成时打印 `playback complete`。
- 保持 v1.0.11 的 RIFF chunk WAV 解析逻辑不变。

## 验证记录

- PlatformIO build: success
- Flash to `/dev/ttyACM0`: success via PlatformIO at `upload_speed = 460800`
- 运行时验证目标：串口应出现 `20261005-v1.0.12`、TTS `init I2S`、`playing`、`playback complete`，且不再出现 `I2S port 0 has not installed`。

## 烧录地址

```
0x00000000 bootloader.bin
0x00008000 partitions.bin
0x00010000 firmware.bin
```
