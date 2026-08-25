# ESP8266 / ESP32-S3 OLED 屏幕接线说明（分引脚 · 4线/6线）

> 0.96" OLED 屏幕（SSD1306/SSD1315 兼容）接入指南。
> 屏幕、传感器（AHT20+BMP280）、报警 LED 三组使用**完全不同的引脚**。
>
> 适用固件变体：
> - ESP8266 → `env:esp8266`（4 线 I2C OLED，D3/D5）
> - ESP32-S3 → `env:esp32-s3-oled`（4 线 I2C OLED，走独立 I2C1 总线 GPIO14/13）
> - ESP32-S3 6 线 SPI TFT → `env:esp32-s3`（ST7735，6 线，**非 OLED**）

---

## 一、屏幕规格（两种形态）

| 项目 | 4 线板 | 6 线板 |
|------|--------|--------|
| 驱动 | SSD1306 / SSD1315 兼容 | ST7735 / ILI9163 SPI TFT |
| 通信 | I2C（SCL + SDA） | SPI（SCK + MOSI + CS + DC + RST + 背光） |
| 典型尺寸 | 0.96" / 1.3" | 1.8" / 2.4" |
| 接线数 | 4 根 | 6–8 根 |
| ESP8266 支持 | **支持（4 线）** | ❌ 不支持（无空闲 SPI 引脚） |
| ESP32-S3 支持 | **支持（I2C1）** | **支持（SPI）** |

> ⚠️ ESP8266 没有空闲的 SPI 引脚（D5=OLED、D6/D7=LED、D3 是上电复位引脚不可靠），
> 所以 **ESP8266 只能接 4 线 I2C OLED，无法接 6 线 SPI TFT**。6 线 TFT 只能用 ESP32-S3。

---

## 二、ESP8266 引脚分配（4 线 OLED 变体）

### 三组完全分引脚，互不共享：

| 功能组 | 引脚 | GPIO | 说明 |
|--------|------|------|------|
| **屏幕 OLED** | **SCL → D5** | **GPIO14** | 独立 I2C 信号（OLED 专用） |
| **屏幕 OLED** | **SDA → D3** | **GPIO2**  | 独立 I2C 信号（OLED 专用） |
| 传感器 AHT20+BMP280 | SCL → D1 | GPIO5 | 传感器 I2C（AHT20 0x38 / BMP280 0x76） |
| 传感器 AHT20+BMP280 | SDA → D2 | GPIO4 | 传感器 I2C |
| 报警 LED 红 | → D6 | GPIO12 | 共阴 RGB 红 |
| 报警 LED 绿 | → D7 | GPIO13 | 共阴 RGB 绿 |
| （蓝） | — | — | 省略 |

**OLED 4 线接线：** VSS→GND，VDD→3V3，**SCL→D5(GPIO14)，SDA→D3(GPIO2)**。
I2C 地址 0x3C，与传感器 0x38/0x76 不冲突。

> ⚠️ **重要限制**：ESP8266 只有 **一个 I2C 接口（Wire 单例）**。
> 屏幕与传感器**必须在同一 I2C 总线上**才能同时工作。因此：
> - 方案是「屏幕独占 D3/D5，**传感器也迁到 D3/D5**」——屏幕+传感器共用 D3/D5，LED 单独 D6/D7。
> - 三者分成**两组**（屏+传感一组 / LED 一组），不能再拆成三组。
> - 旧版是 OLED 与传感器共用 D1/D2；本版把整套迁到 D3/D5 腾出 D1/D2。

### 代价：ESP8266 舍弃蜂鸣器
- 旧版 D5 是蜂鸣器（GPIO14），现改为 OLED SCL。
- 报警只通过 RGB LED + 服务器推送，无蜂鸣音。
- `alarm_esp8266.cpp` 内蜂鸣器 `tone()/ledc` 初始化与鸣响代码已移除。

### ESP8266 空闲/保留引脚一览
- GPIO0（D0）：可用，但**上电前必须拉高**，否则无法启动（boot 敏感）
- GPIO6–11：**Flash 引脚，禁止使用**
- GPIO16：无 I2C 功能，仅作普通 IO
- D3（GPIO2）：现用作 OLED SDA（之前是蜂鸣器的邻居，注意上电时序）

---

## 三、ESP32-S3 引脚分配（两种变体）

ESP32-S3 有**多路独立 I2C / SPI**，三组功能可真正完全分离。

### 变体 A：4 线 I2C OLED（`env:esp32-s3-oled`）

| 功能组 | 引脚 | 说明 |
|--------|------|------|
| **屏幕 OLED** | **SDA → GPIO14** | **独立 I2C1 总线**（与传感器物理隔离） |
| **屏幕 OLED** | **SCL → GPIO13** | I2C1 时钟 |
| 传感器 AHT20+BMP280 | SDA → GPIO8 | **独立 I2C0 总线** |
| 传感器 AHT20+BMP280 | SCL → GPIO9 | I2C0 时钟 |
| 报警 LED 红 | GPIO15 | 共阴 RGB 红 |
| 报警 LED 绿 | GPIO16 | 共阴 RGB 绿 |
| 蜂鸣器 | GPIO18 | 无源蜂鸣器 |
| 麦克风 | GPIO4（ADC） | ADC1_CH3 |
| 喇叭 | GPIO21 | PWM 音频 |

**OLED 4 线接线：** VSS→GND，VDD→3V3，**SDA→GPIO14，SCL→GPIO13**。
I2C 地址 0x3C。屏幕走 **I2C1**、传感器走 **I2C0**，**两条物理独立的 I2C 总线**，互不干扰。

### 变体 B：6 线 SPI TFT（`env:esp32-s3`，ST7735）

> 这是 ESP32-S3 **默认**变体，用的是 **SPI TFT 彩屏**（ST7735），**不是 OLED**。
> 6 根信号线全部独立，与传感器/LED/蜂鸣/MIC 各走各的。

| 信号 | 引脚 |
|------|------|
| TFT CS  | GPIO10 |
| TFT DC  | GPIO7 |
| TFT RST | GPIO6 |
| TFT MOSI| GPIO11 |
| TFT SCK | GPIO12 |
| TFT BL  | GPIO5 |
| 传感器 SDA | GPIO8（I2C0） |
| 传感器 SCL | GPIO9（I2C0） |
| 报警 LED R | GPIO15 |
| 报警 LED G | GPIO16 |
| 蜂鸣器 | GPIO18 |
| 麦克风 | GPIO4 |
| 喇叭 | GPIO21 |

6 线板接线：VCC→3V3，GND→GND，**MOSI→GPIO11，SCK→GPIO12，CS→GPIO10，DC→GPIO7，RST→GPIO6，BL→GPIO5**。

---

## 四、编译 / 烧录

| 设备 | 变体 | 编译命令 |
|------|------|----------|
| ESP8266 | 4 线 OLED | `pio run -e esp8266` |
| ESP32-S3 | 4 线 I2C OLED | `pio run -e esp32-s3-oled` |
| ESP32-S3 | 6 线 SPI TFT | `pio run -e esp32-s3` |

> 编译目录：`/home/hotyuo/tio/firmware`
> 若 `Permission denied`：`sudo chown -R hotyuo:Users firmware/.pio ~/.platformio` 后重编。

### 烧录
```
# ESP8266
pio run -e esp8266 -t upload --upload-port /dev/ttyUSB0

# ESP32-S3 OLED 变体
pio run -e esp32-s3-oled -t upload --upload-port /dev/ttyACM0

# ESP32-S3 TFT 变体
pio run -e esp32-s3 -t upload --upload-port /dev/ttyACM0
```

### 已导出烧录镜像（合并 flash 镜像，可直接 `esptool write_flash 0x0 xxx.bin`）
```
firmware/firmware_bin/envmon_esp8266_oled.bin        ESP8266 4线OLED版
firmware/firmware_bin/envmon_esp32s3_oled.bin         ESP32-S3 4线OLED版
firmware/firmware_bin/envmon_esp32s3_tft.bin          ESP32-S3 6线SPI TFT版（默认）
```

> 缺屏不阻断启动：OLED 探测失败时固件打印 `[BOOT] OLED not found` 并继续在串口/服务器显示。

---

## 五、串口调试命令（115200）

- `config`   → 进入 AP 配网模式
- `factory`  → 恢复出厂（清空配置后重启）
- `status`   → 打印 wifi/mqtt/heap/温度/湿度/气压

---

## 六、故障排查

1. **屏幕不亮但 I2C 探测不到 0x3C** → 检查 SCL/SDA 是否插反（4 线板 SCL 和 SDA 相邻，极易插错）。
2. **传感器读数消失**（ESP8266）→ 屏幕与传感器必须接在同一对 I2C 引脚上；本版统一用 D3/D5，确认传感器也迁到了 D3/D5。
3. **ESP32-S3 OLED 变体不显示** → 确认选的是 `env:esp32-s3-oled`（不是默认 `esp32-s3`，后者是 TFT 编译，不含 OLED 代码）。
4. **ESP8266 上电不启动** → D3（GPIO2）上电前电平异常会触发 boot 模式；确保 VDD 稳定上电。
