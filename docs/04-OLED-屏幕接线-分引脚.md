# ESP8266 / ESP32-S3 OLED 屏幕接线说明（分引脚 · 4线/6线）

> 0.96" OLED 屏幕（SSD1306/SSD1315 兼容）接入指南。
> 屏幕、传感器（AHT20+BMP280）、报警 LED 三组使用**完全不同的引脚**。
>
> 适用固件变体：
> - ESP8266 → `env:esp8266`（4 线 I2C OLED，软件 I2C D0/D1，与传感器硬件 I2C 物理分离）
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
| ESP8266 支持 | **支持（4 线, 软件 I2C）** | ❌ 不支持（无空闲 SPI 引脚） |
| ESP32-S3 支持 | **支持（I2C1）** | **支持（SPI）** |

> ⚠️ ESP8266 没有空闲的 SPI 引脚（D5=OLED、D6/D7=LED、D3 是上电复位引脚不可靠），
> 所以 **ESP8266 只能接 4 线 I2C OLED，无法接 6 线 SPI TFT**。6 线 TFT 只能用 ESP32-S3。

---

## 二、ESP8266 引脚分配（4 线 OLED 变体）

### 实测引脚分配（2026-08 版，u8g2 驱动）

| 功能组 | 引脚 | GPIO | 通信 | 说明 |
|--------|------|------|------|------|
| **屏幕 OLED** | **SDA → D4** | **GPIO2** | **u8g2 软件 I2C** | SSD1306 0x3C，**实测接线** |
| **屏幕 OLED** | **SCL → D5** | **GPIO14** | u8g2 软件 I2C | 屏幕时钟 |
| 传感器 AHT20+BMP280 | SDA → D7 | GPIO13 | Wire 硬件 I2C | AHT20 0x38 / BMP280 0x77（实测） |
| 传感器 AHT20+BMP280 | SCL → D6 | GPIO12 | Wire 硬件 I2C | 传感器 I2C 时钟 |

**OLED 4 线接线：** VSS→GND，VDD→3V3，**SCL→D5(GPIO14)，SDA→D4(GPIO2)**。
I2C 地址 0x3C，与传感器 0x38/0x77 不冲突。

> ⚠️ **2026-08 实测修正**：原文档标称"OLED=D0/D1、传感器=D4/D5"，与实物不符。
> 用 I2C 扫描固件实测：OLED(0x3C) 实际接 **GPIO2(SDA)/GPIO14(SCL)**，
> 传感器(0x38/0x77) 实际接 **GPIO13(SDA)/GPIO12(SCL)**。已按实物更新固件与本文档。
>
> ⚠️ **驱动已从自研 ssd1306 换成 u8g2 库**：自研字库 FONT5X7 被链接到 irom0 高位
> （0x4024783a 附近）超出 ESP8266 IROM 缓存窗口，读字库即 `Exception (3)`；
> u8g2 字库位于低地址不受影响（参考 eink_esp 项目 + 腾讯云 1920918 文章例程）。
> OLED 渲染改用 `U8G2_SSD1306_128X64_NONAME_F_SW_I2C`。

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
2. **传感器读数消失**（ESP8266）→ 确认传感器接在 **D4(SDA)/D5(SCL)**，OLED 接在 **D0(SDA)/D1(SCL)**，两组不要接反。
3. **ESP32-S3 OLED 变体不显示** → 确认选的是 `env:esp32-s3-oled`（不是默认 `esp32-s3`，后者是 TFT 编译，不含 OLED 代码）。
4. **ESP8266 上电不启动** → D0(D1) 上电前被屏幕拉低会触发 boot 模式；确保 OLED 的 VDD 跟随主控上电，开机前 SDA/SCL 不被拉低。
