# esp32-4spi-st7735s-tft-v2.0.0

> **ESP32-S3 + 0.96" ST7735S 4线SPI 彩屏** 烧录包 —— **TFT7735 2.0.0** 版本。
> 与同级目录 `esp32-4spi-st7735s-oled`（v1.0.0）**并列**，接线完全通用，任选其一。
> 不确定用哪个时，**优先用 `esp32-4spi-st7735s-oled`（v1.0.0）**，它是实机烧录验证过的。

---

## ⚠️ 验证状态（务必先读）

| 目录 | 固件版本 | 状态 |
|------|---------|------|
| `esp32-4spi-st7735s-oled` | TFT7735 **1.0.0** | ✅ **已实机烧录 + 读回校验通过**（首选） |
| 本目录 `esp32-4spi-st7735s-tft-v2.0.0` | TFT7735 **2.0.0** | ⚠️ **镜像体检通过，尚未实机点亮验证** |

本目录固件已通过的静态检查：

```
esptool image-info firmware.bin
  Detected image type : ESP32-S3
  Flash size / freq / mode : 8MB / 80m / DIO      <- 与 bootloader、v1.0.0 完全一致
  Segments : 5
  Checksum : 0xb5 (valid)
  Validation hash : fad6e4e4b2ee0420... (valid)
  App version : esp-idf v4.4.7    Compile time : Mar 5 2024 12:12:53
```

关键点：本固件与已验证的 v1.0.0 **编译时间完全相同（Mar 5 2024 12:12:53）、项目名相同（`arduino-lib-builder`）、IDF 版本与 Flash 配置相同**，属于同一套代码的不同版本号。因此 v1.2.0 配套的 `bootloader.bin` / `partitions.bin` 可直接搭配使用（本目录这两个文件与 v1 目录**哈希完全一致**）。

> 若本版本烧录后屏幕异常，请用 esptool 重烧 `../esp32-4spi-st7735s-oled` 目录即可回退（接线不用动）。

---

## 一、适用硬件

与 v1.0.0 完全相同：

- 主控：ESP32-S3（已验证 QFN56 / revision v0.2 / PSRAM 8MB）
- 屏幕：0.96" TFT，**丝印 `4-SPI IC ST7735S Display Color 65K`**，160×80，RGB565

---

## 二、接线引脚（固件内部写死，与 v1.0.0 一致）

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

其它外设：AHT20+BMP280 → `SDA=GPIO8 / SCL=GPIO9`；MAX30102 → `SDA=GPIO14 / SCL=GPIO13`（勿接反，D13 是 SCL）。

**本固件串口标识（115200 启动时打印）：**
```
EnvMon ESP32-S3 (TFT7735) 2.0.0
[BOOT] TFT 0.96" OK (SPI CS=10 DC=7 RST=6 MOSI=11 SCK=12)
```
> 相比 v1.0.0，2.0.0 增加了 `[TFT] page -> %d` 的分页日志。

---

## 三、文件清单与落点

| 文件 | 大小 | 烧录地址 | sha256 |
|------|------|---------|--------|
| `bootloader.bin` | 15,104 B | `0x0` | `1776e4dd896a69d0a5c2e79957b0e2a88aa4129b1381d6478683515a1f6af343` |
| `partitions.bin` | 3,072 B | `0x8000` | `1d9cca96de0fe07ad7fc0648b9878ddecd9ce565e38b589ad20fea698ed4c80c` |
| `firmware.bin` | 1,048,576 B | `0x10000` | `5a6e71b840634c76c969d7e5c107b62c7634c9a8b005befaab21e03161a50643` |

> `firmware.bin` 大小为整 1MB，是其原文件形态（内含 padding），直接按 `0x10000` 写入即可，无需裁剪。
> app0 分区 3264KB，容量充足。

分区表：nvs `0x9000` / otadata `0xe000` / **app0 `0x10000`** / app1 `0x340000`

---

## 四、烧录（务必先全片擦除）

### ⚠️ 必须 erase-flash 的原因

跳过擦除直接覆盖写，esptool 会报 **`MD5 of file does not match data in flash`**。
这是 flash 里**旧固件残留没擦净**（app 尾部约 26KB 不一致），不是串口噪声也不是文件损坏。
判据：两次不同波特率得到的 MD5 **完全相同**。全片擦除后重烧即通过。

### 方式 A：命令行

```bash
esptool --chip esp32s3 --port COM9 erase-flash

esptool --chip esp32s3 --port COM9 --baud 115200 write-flash ^
    0x0     bootloader.bin ^
    0x8000  partitions.bin ^
    0x10000 firmware.bin
```

**Windows 直接双击 `flash.bat`（或 `flash.bat COM9`），自动完成擦除 + 写入 + 复位。**

### 方式 B：乐鑫 Flash 下载工具（图形化）

下载：`https://dl.espressif.com/public/flash_download_tool.zip`
设置 `ChipType=ESP32-S3`、`WorkMode=Develop`、`LoadMode=UART`，三个文件全部勾选并填对应地址，点 START。

---

## 五、烧录后验证

1. **看屏幕**：应出现 EnvMon 界面（不再只是背光）。
2. **串口**（115200）：`[TFT] Starting init...` → `[TFT] Init OK`。
   > 本固件应用模式未开 USB CDC，`Serial` 走硬件 UART0（GPIO43/44），需外接 USB-TTL 才能看日志。
3. **回退方案**：若本版本异常，**接线不用改**，直接重烧 `../esp32-4spi-st7735s-oled` 目录即可。

---

## 六、排障速查

| 现象 | 原因 / 处理 |
|------|------------|
| 只有背光、无内容 | 确认没烧 `envmon_esp32s3_oled.bin`（I2C OLED 版，不能点亮本屏） |
| `MD5 mismatch` | 未全片擦除 → 先跑 `erase-flash` |
| 本版本不可用 | 回退 v1.0.0 目录（`esp32-4spi-st7735s-oled`），接线完全不动 |
| `[MAX30102] not found` | SDA/SCL 接反：`SDA→GPIO14`、`SCL→GPIO13` |
