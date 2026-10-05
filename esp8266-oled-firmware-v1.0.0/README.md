# EnvMon ESP8266 OLED v20261005 — 血氧版固件（独立归档）

> 本归档对应**已烧录并通过 MAX30102 实测验证**的固件。
> 版本命名规则：**年月日 + 语义版本** → `20261005-v1.0.0`。

## 版本信息

| 项目 | 值 |
|------|-----|
| 归档版本 | **20261005-v1.0.0** |
| 发布日期 | 2026-10-05 |
| 目标芯片 | ESP8266 ESP-12F / ESP-12E (`board = esp12e`) |
| 编译环境 | PlatformIO, `espressif8266` (Arduino framework) |
| 固件文件 | `firmware_esp8266_oled_v1.0.0.bin` (361808 bytes) |
| 源码提交 | **`63658ef`**（与已烧录固件逐字节一致） |
| Gitee tag | `20261005-v1.0.0` |
| 源码仓库 | https://gitee.com/hotyuo/esp8266oled |
| 内存占用 | Flash 34.2% (357659/1044464)，RAM 44.2% (36204/81920) |

### ⚠️ 关于固件内部版本号

固件代码内 `FW_VERSION` 宏的值为 `"6.1.3"`（串口 banner 与 OLED 均显示
`ESP8266 v6.1.3` / `EnvMon v6.1.3`）。这是因为 `63658ef` 处于 `6.1.x`
开发线，`pins.h` 改成 `"1.0.0"` 是在其后的 `bdc1045` 提交。

本归档采用**发布线版本** `20261005-v1.0.0`（年月日+语义版本），与固件内部
宏值无关。如需固件界面也显示 `1.0.0`，需基于本归档源码修改 `pins.h`
后重新编译烧录——但那会改变已验证的固件内容，本归档不做此改动。

## 源码对应关系（已逐字节验证）

已烧录固件的二进制与提交 `63658ef` 的编译产物**完全一致**：

```
重编 63658ef  →  sha256 8bf2ee34fa927dc77016818564fae8aa7870a2a86407db709dbf9189cd9ae515
已烧录 bin    →  sha256 8bf2ee34fa927dc77016818564fae8aa7870a2a86407db709dbf9189cd9ae515
```

判定依据：`63658ef`（author date 10:56）晚于 bin 文件时间戳（10:54），说明
10:54 编译时工作区已包含该提交的全部改动但尚未 `git commit`。重编后
Flash 占用 `357659 bytes` 与已烧录固件精确吻合，sha256 相同。

`63658ef` 之前的 `7b86035` 重编产物 Flash 为 `357563 bytes`（少 96 字节），
不含 FIFO 稳定化修复，因此排除。

## SHA256 校验

```
8bf2ee34fa927dc77016818564fae8aa7870a2a86407db709dbf9189cd9ae515  firmware_esp8266_oled_v1.0.0.bin
db92e50d78fbd0ce27798581714dc066dc9d5ab5f7951f2378f2c3744324a249  firmware_esp8266_oled_v1.0.0.elf
```

验证：

```bash
sha256sum -c firmware_esp8266_oled_v1.0.0.bin.sha256 firmware_esp8266_oled_v1.0.0.elf.sha256
```

## 目录结构

```
esp8266-oled-firmware-v1.0.0/
├── firmware_esp8266_oled_v1.0.0.bin      编译固件（已烧录验证，烧录用）
├── firmware_esp8266_oled_v1.0.0.bin.sha256
├── firmware_esp8266_oled_v1.0.0.elf      ELF（调试用，无需烧录）
├── firmware_esp8266_oled_v1.0.0.elf.sha256
├── platformio.ini                        构建配置（复现编译）
├── WIRING.md                             接线表
├── README.md                             本文档
└── src/                                  完整源码（23 文件，对应 63658ef）
```

## 与 `firmware/releases/v6.1.x` 的关系

这是**独立产品线**，不是 v6.1 系列的后续：

| | `firmware/releases/v6.1.x` | 本归档 20261005-v1.0.0 |
|---|---|---|
| 工程 | 主仓库 `firmware/` | 子仓库 `esp8266oled/`（嵌套独立 git） |
| 产品名 | envmon（ICU 环境+体征多参数） | esp8266oled（床头卡血氧专版） |
| 屏幕 | 4 引脚 I2C SSD1306（D5/D6） | 7 引脚 **SPI** SSD1306（SCK/MOSI/CS/DC/RST/BL） |
| MAX30102 总线 | 独立 I2C（D7/D8） | 与环境传感器**共用**一条 I2C（D3/D4） |
| 内部 FW_VERSION | 6.1.0 → 6.1.5 | 6.1.3（发布线记为 20261005-v1.0.0） |

## 本版核心变更（相对 `7b86035`）

本归档源码含 4 项 MAX30102 稳定性修复，**实测血氧/心率可正常输出**：

### 1. DC/AC SpO2 算法（`7b86035`，本归档已含）

`fix(max30102): use dc/ac spo2 and 50hz hr scaling`

改用标准直流/交流分量比计算血氧饱和度，心率采样按 50 Hz 标定。

### 2. FIFO 读取改为 wr/rd 指针推进（`1b02e35` + `63658ef`，本归档已含）

`fix(max30102): consume fifo by wr/rd instead of interrupt bit`
`fix(max30102): consume fifo by wr/rd and stabilize vitals`

原先靠 `INT` 中断状态位判断数据可用性，在 ESP8266 上时序抖动导致漏读或
重复读。改为显式追踪写指针（WR）与读指针（RD）：

- 每次读取后推进 RD 指针
- RD 追上 WR 即视为空，不再产生重复采样
- 数据流稳定，SpO2/HR 连续输出

### 3. 波形峰值间距放宽（`b1ec534`，本归档已含）

`fix(max30102): loosen heartbeat peak spacing for hr output`

原峰值间距限制过严，正常心率区间（60–100 bpm）的波峰被误过滤，导致心率为 0。
放宽间距阈值后心率可正常输出。

### 4. 原始 FIFO 字节探针（`412423a`，本归档已含）

`diag(max30102): add raw FIFO byte probe`

新增原始 FIFO 字节探针，串口打印 `[FIFO_RAW] ok=...`，用于现场诊断采样链路。
（`63658ef` 之前提交的 `7b86035` 不含此项，重编体积差 96 字节即源于此。）

### 后续提交（**不在**本归档内）

`63658ef` 之后的提交不改变已验证固件内容，故不纳入本归档：

- `bdc1045` (10:56) — 仅改 `pins.h` 的 `FW_VERSION` 为 `"1.0.0"` + 新增 WIRING.md
- `5f42dd1` (11:07) — 目录隔离重构

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

| 器件 | SCL | SDA | 地址 |
|------|-----|-----|------|
| **MAX30102** | D4 | D3 | 0x57 |
| **AHT20** | D4 | D3 | 0x38 |
| **BMP280** | D4 | D3 | 0x76 |

ESP8266 主板：VCC → 5V，GND → GND。

> ⚠️ **MAX30102 的 VIN 若模块支持 3.3V 输入则接 3V3**；误接 5V 可能造成
> 数据异常或反复复位。OLED 走 SPI，与 I2C 传感器总线互不干扰。

## 烧录

### 方式一：esptool（推荐）

```bash
# 串口通常是 /dev/ttyUSB0 (CP210x) 或 /dev/ttyACM0 (CH340)
python3 -m esptool --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
  --before default-reset --after hard-reset \
  write-flash 0x0 firmware_esp8266_oled_v1.0.0.bin
```

> ESP8266 的 `firmware.bin` 本身是**完整镜像**（含 bootloader 拼接），
> 直接写 `0x0` 即可，无需单独烧 bootloader / partition table。
> `--before default-reset` 用于固件运行时强制复位进入下载模式。

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

启动后串口应输出 `EnvMon ESP8266 firmware 6.1.3`，OLED 显示
`ESP8266 v6.1.3` 与 `EnvMon v6.1.3`。

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

编译产物 Flash 占用应为 `357659 bytes`（34.2%），与本文档记录的
sha256 一致方可确认源码未被改动。

## 备注

- 本目录是 **20261005-v1.0.0 的只读快照**，后续修改请在 `esp8266oled/`
  子仓库进行，并新建归档目录（不覆盖本目录）。
- 源码快照中的 `FW_VERSION` 宏位于 `src/pins.h`，当前值为 `"6.1.3"`。
- `firmware.elf` 仅用于调试符号与反汇编，烧录只需 `.bin`。
- 固件 HTML 配网页标题仍显示 `v4.0`（`src/net_mgr.cpp` 内硬编码），
  属遗留文案，不影响功能，未做改动以保持与已烧录固件一致。
