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
