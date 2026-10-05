# ESP32 温度声音固件 20261005-v1.0.12

## 变更

- 修复 TTS I2S 播放状态机，避免在 I2S 未安装时卸载。
- 新增播放进度日志和完成日志。
- 保持 RIFF chunk WAV 解析，可处理 LIST/INFO 后移的 data chunk。

## 实测串口

```text
[TTS] 温度 28.3 摄氏度, 湿度 37.7 百分比
[TTS] received 154446 bytes
[TTS] init I2S sdout=19 bclk=18 rate=16000 ch=1 data=154446 bytes
[TTS] playing 16462/154446 bytes
...
[TTS] playback complete
```

## 版本

- Tag: `20261005-v1.0.12`
- Release branch: `release/esp32-7oled-v2.0.3`
