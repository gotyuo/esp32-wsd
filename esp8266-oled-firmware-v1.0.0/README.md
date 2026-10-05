# EnvMon ESP8266 OLED v1.0.0 — 血氧版固件（独立归档）

## 版本信息

| 项目 | 值 |
|------|-----|
| 版本号 | **1.0.0** |
| 发布日期 | 2026-10-05 |
| 目标芯片 | ESP8266 ESP-12F / ESP-12E (`board = esp12e`) |
| 编译环境 | PlatformIO, `espressif8266` (Arduino framework) |
| 固件文件 | `firmware_esp8266_oled_v1.0.0.bin` (353 KB) |
| 源码提交 | `esp8266oled` 仓库 `5f42dd1` |
| Gitee tag | `20261005-v1.0.0` |
| 源码仓库 | https://gitee.com/hotyuo/esp8266oled (分支 `main`) |

## SHA256 校验

```
8bf2ee34fa927dc77016818564fae8aa7870a2a86407db709dbf9189cd9ae515  firmware_esp8266_oled_v1.0.0.bin
db92e50d78fbd0ce27798581714dc066dc9d5ab5f7951f2378f2c3744324a249  firmware_esp8266_oled_v1.0.0.elf
```

验证：

```bash
sha256sum -c firmware_esp8266_oled_v1.0.0.bin.sha256
```

## 目录结构

```
esp8266-oled-firmware-v1.0.0/
├── firmware_esp8266_oled_v1.0.0.bin      编译固件（烧录用）
├── firmware_esp8266_oled_v1.0.0.bin.sha256
├── firmware_esp8266_oled_v1.0.0.elf      ELF（调试用，无需烧录）
├── firmware_esp8266_oled_v1.0.0.elf.sha256
├── platformio.ini                        构建配置（复现编译）
├── WIRING.md                             接线表
├── README.md                             本文档
└── src/                                  完整源码（23 个文件）
```

## 与 `firmware/releases/v6.1.x` 的关系

这是**独立产品线**，不是 v6.1 系列的后续：

| | `firmware/releases/v6.1.x` | 本目录 v1.0.0 |
|---|---|---|
| 工程 | 主仓库 `firmware/` | 子仓库 `esp8266oled/`（嵌套独立 git） |
| 产品名 | envmon（ICU 环境+体征多参数） | esp8266oled（床头卡血氧专版） |
| 屏幕 | 4 引脚 I2C SSD1306（D5/D6） | 7 引脚 **SPI** SSD1306（SCK/MOSI/CS/DC/RST/BL） |
| MAX30102 总线 | 独立 I2C（D7/D8） | 与环境传感器**共用**一条 I2C（D3/D4） |
| 版本体系 | 6.1.0 → 6.1.5 | 1.0.0（独立计数） |

> 注：`esp8266oled/src/pins.h` 里仍保留 `v6.1 引脚定义` 的历史注释，但实际生效的
> OLED 引脚是 `PIN_OLED_SDA=GPIO12(D6)` / `PIN_OLED_SCL=GPIO14(D5)`，
> 接线以 `WIRING.md` 为准。

## 本版核心变更（v1.0.0，相对 v6.1.x）

### 1. MAX30102 算法重写 —— DC/AC SpO2

`fix(max30102): use dc/ac spo2 and 50hz hr scaling`（`7b86035`）

改用标准直流/交流分量比计算血氧饱和度，心率采样按 50 Hz 标定。
之前的版本血氧读数不稳定，此改动后 SpO2 与脉搏读数趋于连续。

### 2. FIFO 读取改为指针推进 —— 不再依赖中断位

`fix(max30102): consume fifo by wr/rd instead of interrupt bit`（`1b02e35`）
`fix(max30102): consume fifo by wr/rd and stabilize vitals`（`63658ef`）

原先靠 `INT` 中断状态位判断数据可用性，在 ESP8266 上时序抖动导致漏读或重复读。
改为显式追踪写指针（WR）与读指针（RD）：

- 每次读取后推进 RD 指针
- RD 追上 WR 即视为空，不再产生重复采样
- 数据流稳定，心电波峰识别连续

### 3. 波形峰值间距放宽 —— 恢复心率输出

`fix(max30102): loosen heartbeat peak spacing for hr output`（`b1ec534`）
`fix(max30102): make hr buffer bound local`（`ce95cbc`）

原峰值间距限制过严，正常心率区间（60–100 bpm）的波峰被误过滤，导致心率为 0。
放宽间距阈值后心率可正常输出；同时把心率缓冲区的边界变量改为局部变量，
避免多路径读取时的状态污染。

### 4. 初始化时序对齐参考驱动

`fix(max30102): align FIFO config and reset with reference driver`（`b5c2515`）
`fix(max30102): stabilize init sequence and expose FIFO diagnostics`（`ca0d6bf`）
`diag(max30102): add raw FIFO byte probe`（`412423a`）

按官方参考驱动重写 FIFO 配置与复位序列，并新增原始 FIFO 字节探针，
串口可打印每个原始采样字节用于现场诊断。

### 5. 工程目录隔离

`chore: isolate ESP8266 spo2 device directory`（`5f42dd1`）
`release: packaging for v1.0.0 with wiring docs`（`bdc1045`）

血氧版工程从主工程拆出为独立 `esp8266-血氧/` 子目录，补齐接线文档与发布包。

## 硬件接线（完整表格见 `WIRING.md`）

### OLED 0.96" SPI（SSD1306, 128x64, 7 引脚）

| OLED 引脚 | ESP8266 引脚 | GPIO |
|-----------|-------------|------|
| SCK | D6 | GPIO12 |
| MOSI / SDA | D5 | GPIO14 |
| CS | D2 | GPIO4 |
| DC | D1 | GPIO5 |
| RST | D0 | GPIO16 |
| BL（背光） | 3V3 | — |
| GND | GND | — |

### 传感器总线（共用一条硬件 I2C）

| 器件 | VCC | GND | SCL | SDA | 地址 |
|------|-----|-----|-----|-----|------|
| **MAX30102** | 5V 或 3V3 | GND | D4 | D3 | 0x57 |
| **AHT20** | 3V3 | GND | D4 | D3 | 0x38 |
| **BMP280** | 3V3 | GND | D4 | D3 | 0x76 |

ESP8266 主板：VCC → 5V，GND → GND。

> ⚠️ **MAX30102 的 VIN 若模块支持 3.3V 输入则接 3V3**；误接 5V 可能造成数据异常或反复复位。
> OLED 走 SPI，与 I2C 传感器总线互不干扰。

## 烧录

### 方式一：esptool（推荐）

```bash
# 串口通常是 /dev/ttyUSB0 (CP210x) 或 /dev/ttyACM0 (CH340)
esptool.py --chip esp8266 --baud 460800 \
  write_flash 0x0 firmware_esp8266_oled_v1.0.0.bin

# 校验（可选）
esptool.py --chip esp8266 flash_id && esptool.py --chip esp8266 verify_file firmware_esp8266_oled_v1.0.0.bin
```

### 方式二：PlatformIO（可复现编译 + 烧录）

```bash
cd esp8266-oled-firmware-v1.0.0
pio run -e esp8266oled
pio run -e esp8266oled -t upload --upload-port /dev/ttyUSB0
```

### 串口监控

```bash
pio device monitor -p /dev/ttyUSB0 -b 115200
```

启动后 OLED 应显示 `ESP8266 v1.0.0` 与 `EnvMon v1.0.0`。

## 复现编译

`platformio.ini` 已包含完整依赖：

```ini
[env:esp8266oled]
platform = espressif8266
board = esp12e
framework = arduino
build_flags =
  -D CORE_DEBUG_LEVEL=1
  -D PROJECT_NAME="esp8266oled"
lib_deps =
  olikraus/U8g2@^2.35.4
  ESP8266WebServer@^2.0.4
```

首次编译会自动下载 espressif8266 工具链与库依赖。

## 备注

- 本目录是 **v1.0.0 的只读快照**，后续修改请在 `esp8266oled/` 子仓库进行。
- 源码快照中的 `FW_VERSION` 宏位于 `src/pins.h`，当前值为 `"1.0.0"`。
- `firmware.elf` 仅用于调试符号与反汇编，烧录只需 `.bin`。
