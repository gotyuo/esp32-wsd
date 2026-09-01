# ESP8266 (ESP-12F) 烧录与接线指南

## 一、硬件清单

| 元件 | 型号 | 数量 | 说明 |
|------|------|------|------|
| 主控 | ESP-12F (ESP8266) | 1 | 4MB Flash, 80MHz |
| 温湿度 | AHT20 | 1 | I2C 0x38 |
| 气压 | BMP280 | 1 | I2C 0x76 |
| 血氧/脉率 | MAX30102 | 1 | I2C 0x57 |
| LED | 共阴 RGB LED | 1 | 仅用 R/G 两色 |
| 蜂鸣器 | 无源蜂鸣器 | 1 | PWM 驱动 |
| 电阻 | 220Ω ×2 | 2 | LED 限流 |
| USB 转串口 | CH340/CP2102 | 1 | 3.3V 电平 |

> ESP8266 无屏幕、无体征 ADC（只有单通道 A0），但支持 MAX30102 血氧/脉率。

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
4. 执行 `pio run -e esp8266 -t upload --upload-port /dev/ttyUSB0`

### 2. 传感器接线（两组独立 I2C）

> ESP8266 只有一个硬件 I2C（Wire），通过时分复用实现两组独立引脚。
> AHT20/BMP280 与 MAX30102 **不共用 SDA/SCL**。

```
AHT20 / BMP280       ESP-12F
─────────────       ───────
VCC          ────→  3V3
GND          ────→  GND
SDA          ────→  D7 (GPIO13)
SCL          ────→  D6 (GPIO12)

MAX30102             ESP-12F
────────             ───────
VCC          ────→  3V3
GND          ────→  GND
SDA          ────→  D2 (GPIO4)
SCL          ────→  D1 (GPIO5)
INT          ────→  不接（轮询模式）
```

> AHT20 和 BMP280 并联在同一 I2C 总线（D7/D6）。
> MAX30102 单独走另一组引脚（D2/D1），与 AHT20/BMP280 完全独立。

### 3. 报警输出接线

```
RGB LED (共阴)        ESP-12F
──────────────       ───────
阴极(公共)    ────→  GND
R 阳极        ───→  220Ω ──→ D6 (GPIO12)
G 阳极        ───→  220Ω ──→ D7 (GPIO13)
(B 阳极不接，ESP8266 引脚不够)

无源蜂鸣器            ESP-12F
───────────         ───────
+             ────→  D5 (GPIO14)
-             ────→  GND
```

### 4. 麦克风接线（可选）

```
驻极体咪头            ESP-12F
───────────         ───────
正极           ───→  分压电路 ──→ A0
负极           ───→  GND
```

> ⚠ ESP8266 的 A0 输入范围是 0-1V（裸芯片）或 0-3.3V（开发板带分压器）。
> 如果用 ESP-12F 裸芯片，必须用电阻分压将信号限制到 1V 以下。
> 大多数 ESP-12F 模块板已内置 100kΩ+220kΩ 分压器，可直接 3.3V 信号。

## 三、引脚分配总表

| ESP-12F 引脚 | GPIO | 功能 | 接设备 |
|:---:|:---:|:---|:---|
| D0 | GPIO16 | （不可用，不能中断/PWM） | - |
| D1 | GPIO5  | I2C SCL | MAX30102 SCL |
| D2 | GPIO4  | I2C SDA | MAX30102 SDA |
| D3 | GPIO0  | Boot strap（烧录用） | FLASH 键 |
| D4 | GPIO2  | Boot strap（TX1） | 不接 |
| D5 | GPIO14 | PWM 输出 | 蜂鸣器 + |
| D6 | GPIO12 | I2C SCL | AHT20/BMP280 SCL |
| D7 | GPIO13 | I2C SDA | AHT20/BMP280 SDA |
| D8 | GPIO15 | Boot strap | 下拉 10kΩ |
| A0 | ADC0   | 模拟输入 | 麦克风（可选） |

## 四、烧录命令

```
# 本机 PlatformIO（已装 python3 + platformio）
cd firmware
sudo -E $(which pio) run -e esp8266
sudo -E $(which pio) run -e esp8266 -t upload --upload-port /dev/ttyUSB0

# 或通过 Docker 一键（免装 PlatformIO，见 firmware/docker/README.md）
cd firmware/docker
sudo docker build -t envmon-firmware .
sudo docker run --rm --device=/dev/ttyUSB0 \
  -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp8266 /dev/ttyUSB0
```

> 串口识别：CP210x/CP2102 通常为 `/dev/ttyUSB0`（`dmesg | grep ttyUSB` 确认），CH340 通常为 `/dev/ttyACM0`。
> 端口权限不足加 `--group-add dialout` 或把当前用户加入 `dialout` 组。

```bash
# 串口监视
pio device monitor -e esp8266 --port /dev/ttyUSB0
```

## 五、配网与使用

1. 首次上电，ESP8266 无 WiFi 配置 → 自动进入 AP 配网模式
2. 手机连接热点 `ENVMON8266-XXXX`（XXXX 为 MAC 后 2 位）
3. 浏览器访问 `http://192.168.4.1`
4. 填写 WiFi 名称/密码、MQTT 服务器地址/端口/凭据
5. 点"保存并连接" → 设备自动重启
6. 正常工作后，LED 绿色呼吸 = 正常，橙色闪烁 = 预警，红色快闪+蜂鸣 = 报警

## 六、与 ESP32 版的差异

| 特性 | ESP32-S3 版 | ESP8266 版 |
|------|------------|-----------|
| 屏幕 | ST7735 160x80 TFT | 无 |
| 体征 ADC | GPIO1/2/3 三通道 | 无（单通道 A0 留给麦克风） |
| OTA 升级 | 支持 | 不支持 |
| 配置存储 | NVS (Preferences) | EEPROM |
| LED | RGB 三色 | RG 两色 |
| 固件版本 | 1.7.0 | 1.0.0 |
| Flash 占用 | 26.1% | 31.5% |
| MQTT 数据格式 | 相同 | 相同 |
| 服务器端 | 共用 | 共用 |
