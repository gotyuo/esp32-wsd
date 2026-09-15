# EnvMon ESP32-S3 (TFT7735) 2.0.3 更新说明

## 主要变更

- 将服务器配置改为 **MQTT / HTTP POST 二选一**。
- 选中 `MQTT` 页签时启用 MQTT 上报，HTTP POST 自动关闭。
- 选中 `HTTP POST` 页签时启用 HTTP POST 上报，MQTT 自动关闭。
- 修复 `/json` 输出非法 `nan` 的问题，NaN 现在统一输出为 `null`。
- 保留 `/` -> `/data` 根路径重定向，并保留 `/config` 配置页入口。

## 版本信息

- 固件版本：`2.0.3`
- 发布目录：`firmware/releases/v2.0.3/`
- 提交：`aee8457 feat(esp32-7oled): make MQTT and HTTP POST mutually exclusive`
- Tag：`v2.0.3`

## 发布产物

- `firmware.bin`
- `firmware.sha256`

## 验证结果

- PlatformIO 编译成功
- 设备烧录成功
- 串口确认 `2.0.3`
- MQTT 模式与 HTTP POST 模式已分别实测，互斥生效
