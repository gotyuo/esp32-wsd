# esp32-4spi-st7735s-oled

| 项目 | 值 |
|------|-----|
| **版本** | **`20261005-v1.0.0`** |
| 固件自述 | `EnvMon ESP32-S3 (TFT7735) 1.0.0` |
| 入库日期 | 2026-10-05（实机烧录 + 读回校验，差异字节 0） |
| 屏显 | 分页显示，**不含** `SpO2:`（血氧需换 v2.0.0 目录） |
| 回退 | 见仓库根目录《版本清单.md》 |

> **ESP32-S3 + 0.96" ST7735S 4线SPI 彩屏** 专用烧录包。自包含、开箱即用。
> 本目录内的固件已于实机验证（COM9，MAC `68:ee:8f:4d:3f:54`），烧录后有画面。
>
> ⚠️ **本版屏上不显示血氧 SpO2**（固件不含 `SpO2:` 屏显串，取证确认）。
> 要在屏上看 MAX30102 的血氧／心率，请用并列目录 **`esp32-4spi-st7735s-tft-v2.0.0`**，接线完全通用、不用改线。

---

## ⚠️ 先读这一段：本目录不是 OLED 版

目录名里的 `oled` 是历史遗留称呼，容易误导。**这块屏是 SPI 彩屏（ST7735S），不是 I2C OLED（SSD1306）。**

本仓库存在两套互不兼容的固件，历史上就是因为混用导致"屏只有背光没内容"反复踩坑：

| 固件 | 驱动对象 | 能否点亮本屏 |
|------|---------|-------------|
| `envmon_esp32s3_**oled**.bin` | I2C OLED / SSD1306 | ❌ **不能**（内部根本无 ST7735 驱动） |
| 本目录 `firmware.bin` | SPI TFT / ST7735S | ✅ 能 |

> 仓库 `docs/04` 里写存在 `envmon_esp32s3_tft.bin`，但该文件实际并不存在——**别信文档，用本目录**。

**本固件自述（启动时串口 115200 会打印）：**
```
EnvMon ESP32-S3 (TFT7735) 1.0.0
[BOOT] TFT 0.96" OK (SPI CS=10 DC=7 RST=6 MOSI=11 SCK=12)
```

---

## 一、适用硬件

- 主控：ESP32-S3（已验证 QFN56 / revision v0.2 / PSRAM 8MB）
- 屏幕：0.96" TFT，**丝印 `4-SPI IC ST7735S Display Color 65K`**
- 分辨率 160×80，RGB565（65K 色），4 线 SPI

---

## 二、接线引脚（固件内部写死，不可改）

| 屏引脚 | ESP32-S3 GPIO | 备注 |
|--------|---------------|------|
| VCC | 3V3 | **必须 3.3V，严禁接 5V** |
| GND | GND | 共地 |
| SCK | **GPIO 12** | SPI 时钟 |
| MOSI / SDA | **GPIO 11** | SPI 数据 |
| CS | **GPIO 10** | 片选 |
| DC | **GPIO 7** | 数据/命令选择 |
| RST | **GPIO 6** | 复位 |
| BL / BLK | **GPIO 5** | 背光（固件拉高） |

接线示意：

```
ESP32-S3                      0.96" ST7735S 模块
┌──────────────┐           ┌──────────────┐
│ 3V3    ──────┼───────────┤ VCC          │
│ GND    ──────┼───────────┤ GND          │
│ GPIO12 ──────┼───────────┤ SCK          │
│ GPIO11 ──────┼───────────┤ MOSI(SDA)    │
│ GPIO10 ──────┼───────────┤ CS           │
│ GPIO7  ──────┼───────────┤ DC           │
│ GPIO6  ──────┼───────────┤ RST          │
│ GPIO5  ──────┼───────────┤ BL(背光)     │
└──────────────┘           └──────────────┘
```

**其它外设（与屏幕互不影响，固件同用一套引脚）**

| 模块 | SDA | SCL |
|------|-----|-----|
| AHT20 + BMP280（I2C0） | GPIO 8 | GPIO 9 |
| MAX30102（I2C1） | GPIO 14 | GPIO 13 |

> 注意：**不要把 MAX30102 的 SDA 接到 D13**——GPIO13 是 I2C1 的 **SCL**，接反会 `[MAX30102] not found`。

---

## 三、文件清单与落点地址

| 文件 | 大小 | 烧录地址 | sha256 |
|------|------|---------|--------|
| `bootloader.bin` | 15,104 B | `0x0` | `1776e4dd896a69d0a5c2e79957b0e2a88aa4129b1381d6478683515a1f6af343` |
| `partitions.bin` | 3,072 B | `0x8000` | `1d9cca96de0fe07ad7fc0648b9878ddecd9ce565e38b589ad20fea698ed4c80c` |
| `firmware.bin` | 883,824 B | `0x10000` | `6bf9e896aeb8a1f934e700c8502f4a5ab32a8b31f07a6f06351efe04305460cf` |

分区表：nvs `0x9000` / otadata `0xe000` / **app0 `0x10000`** / app1 `0x340000`

---

## 四、烧录（务必先全片擦除）

### ⚠️ 为什么必须先 erase-flash

跳过擦除直接覆盖写，app 段会报 **MD5 mismatch**：
```
Input MD5:  ea95603f20b16e86e1c8afabeeab9a5b
Flash MD5: b84b6d5187ab624d822250856a0c4d9a
```
这是 flash 里**残留的旧固件没擦净**（app 尾部约 26KB 不一致，末尾 32 字节却一致），不是串口噪声、不是文件损坏。
判据：两次不同波特率得到的 MD5 **完全相同**。全片擦除后重烧立刻通过。

### 方式 A：命令行（推荐，已验证）

```bash
# 0. 安装
pip install esptool

# 1. 全片擦除（必须）
esptool --chip esp32s3 --port COM9 erase-flash

# 2. 写入三件套
esptool --chip esp32s3 --port COM9 --baud 115200 write-flash ^
    0x0     bootloader.bin ^
    0x8000  partitions.bin ^
    0x10000 firmware.bin
```
> Windows cmd 用 `^` 换行；PowerShell / Linux 用反斜杠 `\` 或直接单行。

**或直接双击 `flash.bat`（Windows），会带端口参数自动完成擦除+写入+复位。**

```bat
flash.bat COM9
```

### 方式 B：乐鑫 Flash 下载工具（图形化，国内直连）

下载：`https://dl.espressif.com/public/flash_download_tool.zip`（乐鑫官方、纯中文、免安装）

按如下填写（三者**都要勾选**，可一次添加多行）：

| 文件 | 地址 | 勾选 |
|------|------|------|
| `bootloader.bin` | `0x0` | ✅ |
| `partitions.bin` | `0x8000` | ✅ |
| `firmware.bin` | `0x10000` | ✅ |

其它设置：`ChipType = ESP32-S3`，`WorkMode = Develop`，`LoadMode = UART`，`BAUD = 921600`。
点 START 前建议先用 esptool 跑一次 `erase-flash`（见上文），避免旧固件残留。

---

## 五、烧录后验证

1. **看屏幕**：应出现 EnvMon 界面（温湿度/状态等），不再是纯背光。
2. **串口**：115200 打开对应端口，应看到 `[TFT] Starting init...` → `[TFT] Init OK`。
   > 若串口无输出：本固件在应用模式未开 USB CDC，`Serial` 映射到硬件 UART0（GPIO43/44），需外接 USB-TTL 查看。
3. **自检文件是否被污染**：核对 `sha256sums.txt`
   ```bash
   sha256sum -c sha256sums.txt      # Linux/macOS
   certutil -hashfile firmware.bin SHA256   # Windows
   ```

---

## 六、通用方法：如何判断某个 .bin 到底驱动什么屏

避免下次再被别的固件搞混，直接查固件内部字符串：

```python
import re
b = open("xxx.bin","rb").read()
for kw in [b"st7735", b"ssd1306", b"u8g2", b"gc9109", b"ili9341"]:
    print(kw.decode(), b.lower().count(kw))
# 出现 st7735 / [TFT]     -> SPI 彩屏版（本目录这类）
# 出现 ssd1306 / u8g2 / [BOOT] OLED -> I2C OLED 版（不能点亮本屏）
```

---

## 七、排障速查

| 现象 | 原因 / 处理 |
|------|------------|
| 只有背光、无内容 | 90% 是烧了 OLED 版固件 → 用本目录重烧 |
| `MD5 mismatch` | 未全片擦除 → 先跑 `erase-flash` |
| 能点亮但花屏/偏色 | 接线错或屏尺寸不符；核对第二节接线表 |
| 串口无日志 | USB CDC 未开，走 UART0(43/44)；屏有画面即成功 |
| `[MAX30102] not found` | SDA/SCL 接反：`SDA→GPIO14`、`SCL→GPIO13` |
