# 备份版本说明

日期：2026-09-19

## 旧版归档（保留用于回退）

- backups/envmon-device-backups-2026-09-19.tar.gz
- backups/envmon-device-backups-2026-09-19.tar.gz.sha256

说明：这是本轮 MAX30102 修复之前的原始归档，保留用于回退或对比。

## 新版归档（MAX30102 修复版）

- backups/envmon-device-backups-2026-09-19-max30102-fix.tar.gz
- backups/envmon-device-backups-2026-09-19-max30102-fix.tar.gz.sha256

说明：包含 MAX30102 修复后的 ESP8266 源码快照和当前已编译成功的固件产物。

## 当前修复内容

- OLED 优先初始化，避免传感器初始化期间黑屏。
- MAX30102 VIN 必须接 3.3V 的说明已写入设备文档。
- MAX30102 显式绑定传感器 I2C：SDA=D7/GPIO13，SCL=D8/GPIO15。
- MAX30102 FIFO 读取改为完整 6 字节样本，并分别提取 Red/IR。
- MAX30102 LED 电流从 0x1F 降到 0x0D，降低 3V3 负载。
- esp8266-4oled 已编译通过。

## 回退方法

如需回退到旧归档：

```bash
tar -xzf backups/envmon-device-backups-2026-09-19.tar.gz
```

如需使用 MAX30102 修复版：

```bash
tar -xzf backups/envmon-device-backups-2026-09-19-max30102-fix.tar.gz
```

校验：

```bash
cd backups
sha256sum -c envmon-device-backups-2026-09-19.tar.gz.sha256
sha256sum -c envmon-device-backups-2026-09-19-max30102-fix.tar.gz.sha256
```

## 当前排查结论（2026-09-19 17:xx）

已验证设备：`172.22.22.63`，串口 `/dev/ttyUSB0`，MAC `d8:bf:c0:11:78:4a`

现象：
- OLED 先点亮正常
- D7/D8 I2C 总线扫描无 ACK，`mask=0`
- AHT20/BMP280/MAX30102 均未发现
- 已临时交换 SDA/SCL 烧录验证一次，仍无 ACK
- 已恢复原接线 `SDA=D7/GPIO13`, `SCL=D8/GPIO15`

当前判断：
- 不是 OLED 顺序问题
- 不是 MAX30102 FIFO 解析能单独解决的问题
- 当前优先怀疑 MAX30102 模块供电/VIN/GND、I2C 接线、上拉电阻或模块损坏
- 下一步需要硬件侧确认 MAX30102 模块供电 3.3V/GND 和 SDA/SCL 实际连通性

## MAX30102 接线结论更新（2026-09-19）

确认有效接线：
- MAX30102 SDA = ESP8266 D1 / GPIO5
- MAX30102 SCL = ESP8266 D2 / GPIO4
- MAX30102 GND = GND
- MAX30102 VIN = 3V3（不要接 5V）

固件已调整：
- `firmware/src_esp8266_4oled/pins.h` 将传感器 I2C 改为 D1/D2
- `MAX30102` 初始化不再因芯片 ID 不匹配直接失败，改为打印告警后继续初始化

验证日志摘要：
- `I2C addr 0x57 ACK`
- `MAX30102 chip id mismatch: 0x03, continue`
- `MAX30102 OK`
- 设备联网：`172.22.22.63`

## 当前修复版归档（HTTP JSON contract 2.0.1）
- backups/pre-change/envmon-device-backups-2026-09-19-pre-commit-20260919-204702.tar.gz
- backups/pre-change/envmon-device-backups-2026-09-19-pre-commit-20260919-204702.tar.gz.sha256

说明：对应本次提交，版本 2.0.1，包含 ESP32/ESP8266 HTTP JSON 字段统一、服务器扫描路径收紧、以及预提交快照。

## 当前烧录镜像归档（FW 2.0.1）
- backups/envmon-device-backups-2026-09-19-fw201.tar.gz
- backups/envmon-device-backups-2026-09-19-fw201.tar.gz.sha256
- backups/2026-09-19/envmon_esp32s3_oled.bin
- backups/2026-09-19/envmon_esp32s3_oled.bin.sha256
- backups/2026-09-19/envmon_esp8266.bin
- backups/2026-09-19/envmon_esp8266.bin.sha256

说明：最终烧录镜像按 FW 2.0.1 重新生成，并包含 ESP8266 FW_VERSION 宏修正。

## 当前烧录镜像归档（FW 2.0.2 / MAX30102 hrcalc 算法）
- firmware/firmware_bin/envmon_esp32s3_oled.bin
- firmware/firmware_bin/envmon_esp32s3_oled.bin.sha256
- firmware/firmware_bin/envmon_esp8266.bin
- firmware/firmware_bin/envmon_esp8266.bin.sha256
- backups/envmon-device-backups-2026-09-19-fw202.tar.gz
- backups/envmon-device-backups-2026-09-19-fw202.tar.gz.sha256

说明：对照 vrano714 的 `hrcalc.py` 参考实现，MAX30102 采样率改为 25Hz，4 秒窗口改为 100 点，峰值检测/HR/SpO2 中值比值算法移植到 ESP8266/ESP32；同时新增 MAX30102 die temperature 读取，并在 HTTP JSON 中增加 `max_temp_c` / `max30102_temp_c`。

## 当前烧录镜像归档（FW 2.0.4 / MAX30102 HR 稳定化）
- firmware/firmware_bin/envmon_esp32s3_oled.bin
- firmware/firmware_bin/envmon_esp32s3_oled.bin.sha256
- firmware/firmware_bin/envmon_esp8266.bin
- firmware/firmware_bin/envmon_esp8266.bin.sha256
- backups/envmon-device-backups-2026-09-19-fw204.tar.gz
- backups/envmon-device-backups-2026-09-19-fw204.tar.gz.sha256

说明：在 2.0.3 基础上优化 MAX30102 HR 算法。先过滤生理合理 HR 区间（40-150 bpm）对应的 RR 峰间期，再取中位 RR 计算心率，避免噪声峰/短周期误检把 HR 拉到 140+。回滚点为 `envmon-device-backups-2026-09-19-fw203.tar.gz`。
