# 硬件接线说明 (ESP8266 ESP-12F v4.0)

## 接线总览

```
            ┌─────────────────────────────────┐
            │      ESP8266 ESP-12F 模块        │
            │                                  │
 OLED 线    │  3V3 ◄─ VDD                      │
            │  GND ◄─ VSS                      │
            │  D5  ◄─ SCL  (GPIO14)            │
            │  D6  ◄─ SDA  (GPIO12)            │
            │                                  │
 传感器线   │  3V3 ◄─ VDD/VCC/VIN              │
            │  GND ◄─ GND/GND/GND              │
            │  D7  ◄─ SDA/SDA/SDA (GPIO13)     │
            │  D8  ◄─ SCL/SCL/SCL (GPIO15)     │
            │                                  │
 电源       │  VIN ── 5V  (USB/电源适配器)      │
            │  GND ── GND                       │
            └─────────────────────────────────┘
```

## 详细接线

### 1. OLED 0.96" I2C (SSD1306)

| OLED 标记 | 模块引脚 | ESP8266 | GPIO | 备注 |
|-----------|----------|---------|------|------|
| VDD | 4-pin 最上 | 3V3 | - | 3.3V 供电 |
| VSS | 4-pin 第二 | GND | - | 地 |
| SCL | 4-pin 第三 | D5 | GPIO14 | I2C 时钟 |
| SDA | 4-pin 第四 | D6 | GPIO12 | I2C 数据 |

> ⚠️ **OLED 是 4 引脚版本**，没有 RST/DC 引脚。
> OLED 地址自动识别为 `0x3C`。
> OLED 走 u8g2 软件 I2C，不占用硬件 Wire 外设。

### 2. AHT20 温湿度传感器

| AHT20 引脚 | ESP8266 | GPIO | 备注 |
|-----------|---------|------|------|
| VDD | 3V3 | - | 3.3V 供电 |
| GND | GND | - | 地 |
| SDA | D7 | GPIO13 | 共用传感器总线 |
| SCL | D8 | GPIO15 | 共用传感器总线 |

地址 `0x38`，量程：温度 -40~85℃、湿度 0~100%RH。

### 3. BMP280 气压传感器

| BMP280 引脚 | ESP8266 | GPIO | 备注 |
|------------|---------|------|------|
| VCC | 3V3 | - | 3.3V 供电 |
| GND | GND | - | 地 |
| SDA | D7 | GPIO13 | 共用传感器总线 |
| SCL | D8 | GPIO15 | 共用传感器总线 |
| CSB | 悬空 | - | 低有效，悬空表示 CSB 不启用 |
| INT | 悬空 | - | 中断输出，本版本不用 |

地址 `0x76` 或 `0x77`（自动识别），量程：气压 300~1100 hPa、温度 -40~85℃。

### 4. MAX30102 血氧/心率传感器

> 你给的 "SDA=SD, SCL=SK" 是模块上的缩写：**SD = SDA, SK = SCL**
> （S=Serial, D=Data, K=Clock）

| MAX30102 引脚 | 模块标记 | ESP8266 | GPIO | 备注 |
|--------------|---------|---------|------|------|
| VIN | VIN | VIN | - | 5V 或 3.3V 均可（模组内置 LDO） |
| GND | GND | GND | - | 地 |
| SDA | **SD** | D7 | GPIO13 | 共用传感器总线 |
| SCL | **SK** | D8 | GPIO15 | 共用传感器总线 |
| INT | 悬空 | - | - | 中断输出，本版本用轮询不用 |

地址 `0x57`，量程：SpO2 70~100%、心率 30~220 bpm。

> ⚠️ MAX30102 工作时需要手指遮挡（贴到手指）才能测到信号。
> 没贴手指时 OLED 显示 `--`，Web 显示 `null`。
> 首次测量需要 4 秒窗口积累数据。

---

## 关键注意事项

### ⚠️ D8=GPIO15 必须上电低电平
ESP-12F 的 GPIO15 上电时必须为**低电平**，否则芯片会进入下载模式无法启动固件。

**解决方法：** 在 D8 引脚和 GND 之间加一个 4.7kΩ 下拉电阻，或者在 D8 引脚和 3V3 之间加一个 4.7kΩ 上拉电阻（取决于传感器要求）。

通常传感器模块的 SCL/SDA 内部已有上拉电阻，不需要额外接线。

### ⚠️ 不要共用 GPIO0 作为 I2C
GPIO0 (D3) 是 ESP-12F 的下载模式触发脚，上电时低电平进入下载模式。**本版本未使用 GPIO0**。

### ⚠️ 不要使用 GPIO16 (D0)
GPIO16 是 RTC 唤醒脚，不能用作普通 GPIO。

### ⚠️ 上拉电阻
- OLED 的 SCL/SDA 内部已有上拉电阻（模块自带）
- AHT20 / BMP280 / MAX30102 模块内部通常已有 4.7kΩ 上拉
- 如需外接，推荐 4.7kΩ 到 3V3

### ⚠️ 电源
- ESP-12F 推荐 5V 供电（VIN 引脚），板载 LDO 降压到 3.3V
- 三颗传感器都用 3V3 或 VIN，**不要共用 5V 直接到传感器**
- MAX30102 模组通常标 VIN，但内部是 3.3V 逻辑

### ⚠️ 走线长度
- I2C 总线走线应短（<10cm）
- 三颗传感器共用一根 SDA、一根 SCL，物理上并联到 D7/D8
- 如果总线长，可在传感器模块的 SCL/SDA 加 100pF 去耦电容

---

## 完整接线示意图

```
                    ESP-12F
                ┌──────────────┐
   3V3 ────────►│ 3V3          │◄──────── 电源 5V (VIN)
   GND ────────►│ GND          │◄──────── 电源 GND
                │              │
   OLED VDD ───►│ 3V3          │
   OLED VSS ───►│ GND          │
   OLED SCL ───►│ D5 (GPIO14)  │
   OLED SDA ───►│ D6 (GPIO13)  │
                │              │
   AHT20 VDD ──►│ 3V3          │
   AHT20 GND ──►│ GND          │
   AHT20 SDA ──►│ D7 (GPIO13)  │──┐
   AHT20 SCL ──►│ D8 (GPIO15)  │──┤
                │              │  │  共用总线
   BMP280 VCC ─►│ 3V3          │  │
   BMP280 GND ─►│ GND          │  │
   BMP280 SDA ─►│ D7 (GPIO13)  │◄─┤
   BMP280 SCL ─►│ D8 (GPIO15)  │◄─┘
   BMP280 CSB ──► 悬空
                │              │
   MAX30102 VIN►│ VIN          │
   MAX30102 GND►│ GND          │
   MAX30102 SDA►│ D7 (GPIO13)  │──┐
   MAX30102 SCL►│ D8 (GPIO15)  │──┤
   MAX30102 INT─► 悬空         │──┘
                └──────────────┘
```

---

## 验证接线

上电后用串口（115200）应看到：

```
======================================
 EnvMon ESP8266 firmware 4.0.0
======================================
[BOOT] config NOT found (first boot)
[SENSOR] AHT20 OK (SW I2C)
[SENSOR] BMP280 OK (SW I2C)
[SENSOR] MAX30102 OK (HW I2C 400kHz)
[HIST] ring buffer init: 12 points, interval=300000ms
[BOOT] OLED OK (SW I2C: SDA=GPIO12 SCL=GPIO14)
[NET] No WiFi config, entering AP config mode
[NET] AP started: ENVMON8266-XXXX (http://192.168.4.1)
```

如果某颗传感器显示 `not found`，检查：
1. 对应传感器接线是否正确
2. 电源是否充足（5V 电源是否稳定）
3. D8 上电是否低电平
4. 总线是否有短路
