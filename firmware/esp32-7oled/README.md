# EnvMon ESP32-S3 ST7735 TFT 固件 — Release

**固件版本**: v1.1.0
**编译日期**: 2026-09-06
**编译环境**: `env:esp32-s3-tft7735`
**固件大小**: 853 KB (Flash 26.1%)

---

## 1. 目录结构

```
esp32s3-tft7735/
├── README.md              # 本文档
├── flash.sh               # 一键烧录脚本
├── partitions_ota.csv     # OTA 分区表
├── firmware_bin/          # 烧录镜像
│   ├── bootloader.bin     #  @ 0x00000000
│   ├── partitions.bin     #  @ 0x00008000
│   └── firmware.bin       #  @ 0x00010000
└── src/                   # 全部源码
```

---

## 2. 接线图

### 2.1 屏幕 (ST7735 0.96" 160x80 IPS TFT LCD, 6引脚 SPI)

| 屏幕引脚 | 功能 | ESP32-S3 GPIO |
|---------|------|---------------|
| SCK     | 时钟 | 12 |
| MOSI    | 数据 | 11 |
| CS      | 片选 | 10 |
| DC      | 数据/命令 | 7 |
| RES     | 复位 | 6 |
| BLK     | 背光 | 5 |
| VCC     | 电源 | 3.3V |
| GND     | 地 | GND |

### 2.2 传感器 (全部独立引脚，不共用总线)

| 传感器 | 功能 | 总线/接口 | ESP32-S3 GPIO |
|--------|------|----------|---------------|
| AHT20  | 温湿度 | I2C0 SDA/SCL | 8 / 9 |
| BMP280 | 气压 | I2C0 SDA/SCL | 8 / 9 (与AHT20共用) |
| MAX30102 | 心率血氧 | I2C1 SDA/SCL | 14 / 13 (独立) |
| MIC    | 麦克风 | ADC | 4 |
| LED R  | 红LED | GPIO | 15 |
| LED G  | 绿LED | GPIO | 16 |
| LED B  | 蓝LED | GPIO | 17 |
| Buzzer | 蜂鸣器 | PWM | 18 |
| Speaker| 喇叭 | PWM | 21 |

**MIC 注意**: 裸驻极体咪头必须外接 4.7kΩ 偏置电阻到 3.3V，否则 ADC 恒为 0。

---

## 3. 烧录方法

### 方法一：一键脚本（推荐）

```bash
cd esp32s3-tft7735
./flash.sh
```

脚本自动检测串口，用 esptool 按分区表地址分别烧录 3 个文件。

### 方法二：PlatformIO（修改源码后）

```bash
cd /home/hotyuo/tio/firmware
sudo -E /home/hotyuo/.local/bin/pio run -e esp32-s3-tft7735 -t upload --upload-port /dev/ttyACM0
```

### ⚠️ 烧录警告

**禁止**手动拼接 `boot+part+fw` 合并镜像！bootloader.bin 仅 15104 字节 (0x3B00)，拼接后分区表会落在 0x3B00 而非 0x8000，导致 bootloader 找不到分区表 → 反复重启。必须用 `pio upload` 或 esptool 多地址分别烧录。

---

## 4. 固件功能

### 4.1 屏幕显示

```
+----------------------------------+
| SSID:home_wifi  ▉▉▉▉ (WiFi信号)   |
+----------------------------------+
| T: 25.3C        SpO2: 98%        |
| H: 60.5%         HR:  72 bpm     |
| P: 1013hPa      MIC: ▉▉▉░░░░░░░ |
+----------------------------------+
| MQTT v1.1.0    L:2               |
+----------------------------------+
```

- **左侧**: 温度(T)、湿度(H)、气压(P)
- **右侧**: MAX30102 血氧(SpO2)、心率(HR)、MIC 电平条
- **顶部**: WiFi SSID + 信号强度条
- **底部**: MQTT 状态 + 固件版本 + 报警级别
- **报警时**: 红边框闪烁

### 4.2 Web 实时数据页面

设备连上 WiFi 后，通过 **设备 IP** 访问：

| 页面 | URL | 说明 |
|------|-----|------|
| 配网页面 | `http://<设备IP>/` | WiFi/MQTT 配置 + 恢复出厂 |
| 实时数据 | `http://<设备IP>/data` | 实时传感器数据 (温度/湿度/气压/血氧/心率/MIC) |
| 数据接口 | `http://<设备IP>/json` | JSON 格式数据，可供第三方集成 |
| 恢复出厂 | `http://<设备IP>/factory` | 清除所有配置，重启进入配网 |

配网模式下（AP热点），访问 `http://192.168.4.1/`

### 4.3 串口命令 (115200)

| 命令 | 功能 |
|------|------|
| `config` | 进入 AP 配网模式（热点连接后访问 192.168.4.1） |
| `factory` | 恢复出厂设置（清除所有配置，重启） |
| `status` | 查看状态（WiFi/MQTT/堆内存/传感器数据） |

### 4.4 恢复出厂

三种方式：
1. **Web 界面**: 配网页面底部红色「🔄 恢复出厂设置」按钮
2. **Web URL**: 访问 `http://<设备IP>/factory`
3. **串口命令**: 输入 `factory`

恢复后设备重启，无 WiFi 配置，自动进入 AP 配网模式。

### 4.5 语音输出

喇叭通过 GPIO21 LEDC PWM 驱动（独立通道 SPK_CH=1），报警时与蜂鸣器同时发声：
- **正常**: 无声
- **预警**: 无声（仅 LED 闪烁）
- **报警**: 蜂鸣器 2700Hz + 喇叭 880Hz，间歇鸣叫（500ms 响 / 500ms 停）

---

## 5. 引脚修改

所有引脚集中在 `src/pins_esp32s3_tft7735.h`，修改后重新编译上传：

```bash
sudo -E /home/hotyuo/.local/bin/pio run -e esp32-s3-tft7735 -t upload
```

---

## 6. FAQ

**Q: 烧录后反复重启？**
A: 不能用合并镜像烧录。用 `flash.sh` 或 `pio upload` 按地址分别烧录。

**Q: 屏幕黑屏有背光？**
A: 检查 SPI 接线是否正确 (SCK=12 MOSI=11 CS=10 DC=7 RST=6 BLK=5)。

**Q: MIC 读数为 0？**
A: 裸咪头需要外接 4.7kΩ 偏置电阻到 3.3V。

**Q: MAX30102 不显示？**
A: 检查 I2C1 接线 (SDA=14 SCL=13)，确认传感器供电和 GND。

**Q: 如何查看实时数据？**
A: 设备连 WiFi 后，浏览器访问 `http://<设备IP>/data`。
