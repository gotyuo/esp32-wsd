# ESP8266 (ESP-12F) 烧录与接线指南

> 本文档适用于 **esp8266-4oled v6.1.4**（4 针 OLED + AHT20 + BMP280 + MAX30102 全功能版）。

## 一、硬件清单

| 元件 | 型号 | 数量 | 说明 |
|------|------|------|------|
| 主控 | ESP-12F (ESP8266) | 1 | 4MB Flash, 80MHz |
| 屏幕 | SSD1306 0.96" OLED (4 针 I2C) | 1 | I2C 0x3C |
| 温湿度 | AHT20 | 1 | I2C 0x38 |
| 气压 | BMP280 | 1 | I2C 0x76 |
| 血氧/脉率 | MAX30102 | 1 | I2C 0x57 |
| LED | 共阴 RGB LED | 1 | 仅用 R/G 两色 |
| 蜂鸣器 | 无源蜂鸣器 | 1 | PWM 驱动 |
| 电阻 | 220Ω ×2 | 2 | LED 限流 |
| USB 转串口 | CH340/CP2102 | 1 | 3.3V 电平 |

## 二、接线表

### 1. ESP-12F 串口烧录接线

```
USB转串口          ESP-12F
─────────         ───────
3V3      ────→   VCC (3.3V)
GND      ────→   GND
TXD      ────→   RXD (GPIO3)
RXD      ────→   TXD (GPIO1)

烧录时需要拉低 GPIO0：
GPIO0    ←─── 通过 10kΩ 电阻接地（或按住 FLASH 键）
EN       ←─── 通过 10kΩ 电阻接 3V3（或按住 RESET 键）
```

烧录步骤：
1. 按住 FLASH 键（GPIO0 接地）
2. 按一下 RESET 键（EN 接 3V3 脉冲）
3. 松开 FLASH 键
4. 执行 `pio run -e esp8266-4oled -t upload --upload-port /dev/ttyUSB0`

### 2. 屏幕（OLED SSD1306，u8g2 软件 I2C）

```
OLED 0.96"            ESP-12F
─────────           ───────
VDD          ────→  3V3
GND          ────→  GND
SCL          ────→  D5 (GPIO14)
SDA          ────→  D6 (GPIO12)
```

> OLED 走 u8g2 **软件 I2C**（D5/D6），不占用 ESP8266 硬件 Wire。

### 3. 传感器接线（AHT20 + BMP280 + MAX30102 共用一条硬件 I2C 总线）

> **v6.1.4 变更（重要）**：MAX30102 从 D7/D8 改接到 **D1/D2**，与 AHT20/BMP280
> 共用同一硬件 I2C 总线。三个传感器地址不冲突（0x38 / 0x76 / 0x57），I2C 支持一总多设备。

```
AHT20 / BMP280 / MAX30102   ESP-12F
─────────────────────       ───────
VCC / VDD          ────→  3V3
GND                ────→  GND
SDA（所有传感器）  ────→  D2 (GPIO4)
SCL（所有传感器）  ────→  D1 (GPIO5)
MAX30102 INT       ────→  不接（轮询模式）
```

> ⚠️ **为什么 MAX30102 不能接 D8 (GPIO15)？**
> 这是 v6.1.2 黑屏 bug 的根因（详见 v6.1.4 README）：
> - GPIO15 是 ESP8266 **启动跳线引脚（boot strap）**，上电/复位时必须保持**低电平**才能从 Flash 启动；
> - MAX30102 模块板上自带 I2C **上拉电阻**，SDA/SCL 空闲时被拉高；
> - 把 SDA 或 SCL 接到 D8 后，模块上拉会把 GPIO15 拉高 → 芯片无法启动 → **黑屏**；
> - 拔掉 MAX30102（GPIO15 恢复板上 10kΩ 下拉）→ 正常启动。
> D1/D2 无启动跳线功能，接 MAX30102 安全。

### 4. 报警输出接线

```
RGB LED (共阴)        ESP-12F
──────────────       ───────
阴极(公共)    ────→  GND
R 阳极        ───→  220Ω ──→ D6 (GPIO12)   （与 OLED SDA 共用，本方案不用 LED 时忽略）
G 阳极        ───→  220Ω ──→ D7 (GPIO13)
(B 阳极不接，ESP8266 引脚不够)

无源蜂鸣器            ESP-12F
───────────         ───────
+             ────→  D5 (GPIO14)   （与 OLED SCL 共用，本方案不用蜂鸣器时忽略）
-             ────→  GND
```

> ⚠️ 注意：v6.1.4 中 D5/D6/D7 已被屏幕/传感器占用：
> - D5 (GPIO14) = OLED SCL
> - D6 (GPIO12) = OLED SDA
> - D7 (GPIO13) = 空闲（可用作报警 LED G，需自行确认与屏幕不冲突时使用）
> 报警 LED / 蜂鸣器如需使用，请按实际可用引脚调整并在代码中修改。

### 5. 麦克风接线（可选）

```
驻极体咪头            ESP-12F
───────────         ───────
正极           ───→  分压电路 ──→ A0
负极           ───→  GND
```

> ⚠ ESP8266 的 A0 输入范围是 0-1V（裸芯片）或 0-3.3V（开发板带分压器）。
> 如果用 ESP-12F 裸芯片，必须用电阻分压将信号限制到 1V 以下。
> 大多数 ESP-12F 模块板已内置 100kΩ+220kΩ 分压器，可直接 3.3V 信号。

## 三、引脚分配总表（v6.1.4）

| ESP-12F 引脚 | GPIO | 功能 | 接设备 |
|:---:|:---:|:---|:---|
| D0 | GPIO16 | （不可用，不能中断/PWM） | - |
| D1 | GPIO5  | I2C SCL（硬件 Wire） | AHT20/BMP280/MAX30102 SCL |
| D2 | GPIO4  | I2C SDA（硬件 Wire） | AHT20/BMP280/MAX30102 SDA |
| D3 | GPIO0  | Boot strap（烧录用） | FLASH 键 |
| D4 | GPIO2  | Boot strap（TX1） | 不接 |
| D5 | GPIO14 | u8g2 软件 I2C SCL | OLED SCL |
| D6 | GPIO12 | u8g2 软件 I2C SDA | OLED SDA |
| D7 | GPIO13 | 空闲 | - |
| D8 | GPIO15 | Boot strap（**必须保持低电平**） | **禁止接任何 I2C 线/上拉** |
| A0 | ADC0   | 模拟输入 | 麦克风（可选） |

> ⚠️ **D8/GPIO15 铁律**：GPIO15 上电时必须为低电平。**严禁**把带有上拉的信号（尤其 I2C SDA/SCL、复位线）接到 D8，否则 ESP8266 无法启动（黑屏）。

## 四、烧录命令

```
# 本机 PlatformIO（已装 python3 + platformio）
cd firmware
sudo -E $(which pio) run -e esp8266-4oled
sudo -E $(which pio) run -e esp8266-4oled -t upload --upload-port /dev/ttyUSB0

# 或直接烧录 release 固件文件
esptool.py --before default-reset --chip esp8266 \
    --port /dev/ttyUSB0 --baud 460800 write_flash 0x0 releases/v6.1.4/firmware_v6.1.4.bin

# 或通过 Docker 一键（免装 PlatformIO，见 firmware/docker/README.md）
cd firmware/docker
sudo docker build -t envmon-firmware .
sudo docker run --rm --device=/dev/ttyUSB0 \
  -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp8266-4oled /dev/ttyUSB0
```

> 串口识别：CP210x/CP2102 通常为 `/dev/ttyUSB0`（`dmesg | grep ttyUSB` 确认），CH340 通常为 `/dev/ttyACM0`。
> 端口权限不足加 `--group-add dialout` 或把当前用户加入 `dialout` 组。

```bash
# 串口监视
pio device monitor -e esp8266-4oled --port /dev/ttyUSB0
```

## 五、配网与使用

1. 首次上电，ESP8266 无 WiFi 配置 → 自动进入 AP 配网模式
2. 手机连接热点 `ENVMON8266-XXXX`（XXXX 为 MAC 后 2 位）
3. 浏览器访问 `http://192.168.4.1`
4. 填写 WiFi 名称/密码、MQTT 服务器地址/端口/凭据
5. 点"保存并连接" → 设备自动重启
6. 正常工作后，OLED 显示环境页 → 体征页（血氧/心率）→ 网络页 → 历史页，每 5 秒轮播

## 六、v6.1.4 相对 v6.1.2 的接线差异

| 项目 | v6.1.2 | v6.1.4 |
|------|--------|--------|
| MAX30102 SDA | D8 (GPIO15) ❌ | **D2 (GPIO4)** ✅ |
| MAX30102 SCL | D7 (GPIO13) | **D1 (GPIO5)** ✅ |
| MAX30102 总线 | 独立 D7/D8（含启动跳线脚） | 与 AHT20/BMP280 共用 D1/D2 |
| 现象 | 接 MAX30102 开机黑屏 | 正常启动，MAX30102 可用 |