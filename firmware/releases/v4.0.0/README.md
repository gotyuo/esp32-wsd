# EnvMon ESP8266 v4.0 — 完整功能固件

## 版本: 4.0.0
**发布日期:** 2026-09-13
**目标芯片:** ESP8266 ESP-12F
**编译环境:** PlatformIO 6.1.19, espressif8266 @ 4.2.0
**烧录文件:** `firmware_v4.0.0.bin` (34.1% Flash, 43.5% RAM)

---

## 1. 硬件引脚接线 (ESP-12F)

### OLED 0.96" I2C (SSD1306, 128x64, 4 引脚)
| OLED 引脚 | ESP8266 引脚 | 说明 |
|-----------|-------------|------|
| VDD | 3V3 | 3.3V 供电 |
| VSS | GND | 地 |
| SCL | **D5 (GPIO14)** | I2C 时钟 |
| SDA | **D6 (GPIO12)** | I2C 数据 |

> OLED 走 u8g2 **软件 I2C**，不占用 ESP8266 的硬件 Wire 外设。

### 传感器总线 (AHT20 + BMP280 + MAX30102 共用)
| 传感器 | 引脚 | ESP8266 引脚 |
|--------|------|-------------|
| **AHT20** | VDD | 3V3 |
| | GND | GND |
| | SDA | **D7 (GPIO13)** |
| | SCL | **D8 (GPIO15)** |
| **BMP280** | VCC | 3V3 |
| | GND | GND |
| | SDA | **D7 (GPIO13)** (共用) |
| | SCL | **D8 (GPIO15)** (共用) |
| | INT/CSB | 悬空 |
| **MAX30102** | VIN | VIN |
| | GND | GND |
| | SDA | **D7 (GPIO13)** (共用) |
| | SCL | **D8 (GPIO15)** (共用) |
| | INT | 悬空 |

**地址分配（不冲突）:**
- AHT20: `0x38`
- BMP280: `0x76` (或 `0x77` 自动识别)
- MAX30102: `0x57`

> ⚠️ **D8=GPIO15 上电必须为低电平**，需外接 4.7kΩ 上拉电阻到 3V3（否则 ESP-12F 会进入下载模式无法启动）。

**总线策略:**
- OLED 走 u8g2 **软件 I2C**（独立于硬件 Wire）
- AHT20 / BMP280 / MAX30102 共用硬件 **Wire** (400kHz)，地址不冲突
- "不共用引脚" 指 OLED (D5/D6) 与传感器 (D7/D8) 两组 GPIO 不交叉

**MAX30102 引脚说明:** 你给的 "SDA=SD, SCL=SK" 理解为模块上的缩写：SD=SCL? 不，正确是 **SD=SDA, SK=SCL**（S=Signal/Serial, D=Data, K=Clock）。
所以 MAX30102 的 SDA→D7, SCL→D8，与 AHT20/BMP280 共用同一组 D7/D8。

---

## 2. 功能清单

### OLED 4 界面 (128x64, u8g2 软件 I2C)
1. **环境页** — 温度/湿度/气压，超阈值显示 `!` 警示
2. **体征页** — 血氧 SpO2 / 心率 HR，无 MAX30102 时显示 `no dev`
3. **网络页** — 当前 SSID / IP / MQTT 状态 / 信号条
4. **历史曲线页** — 最近 1 小时温度曲线（每 5 分钟 1 点，12 点窗口）

- 每 5 秒自动翻页
- 串口命令 `page1`-`page4` 手动切换，`page` 下一页，`auto` 恢复自动

### 网络管理 (AP↔STA 状态机)
- **首次上电（无 WiFi 配置）:** 直接进 AP 配网
- **有 WiFi 配置:** 先尝试 STA (15s 超时)，同时开 AP 兜底
  - STA 成功 → 关 AP，跑 Web 数据页
  - STA 超时 → 切回纯 AP 重新配网
- **STA 掉线 60s:** 自动切回 AP 配网

### Web 数据页 (STA 联网后)
- `http://<esp_ip>/` → 配网页（重新配网入口）
- `http://<esp_ip>/data` → 实时数据页（5 秒自动刷新）
- `http://<esp_ip>/history` → 历史曲线页（Canvas 绘制 5 条曲线）
- `http://<esp_ip>/api/data` → JSON 实时数据
- `http://<esp_ip>/api/history` → JSON 历史数据
- `http://<esp_ip>/config` → 同 `/`

### AP 配网页 (`http://192.168.4.1`)
- WiFi SSID / 密码
- 服务器模式：局域网自动发现 / 手动指定 IP
- MQTT 端口 / 用户名 / 密码 / 设备编号 / 上报间隔
- WiFi 扫描按钮（弹窗选择）

### 报警
- 环境报警阈值：温度 / 湿度 / 气压
- 体征报警阈值：血氧下限 / 心率范围
- 三级：正常 / 警告 / 报警
- 无 LED 引脚（本版本未接）

### 串口命令 (115200)
| 命令 | 说明 |
|------|------|
| `page1`-`page4` | 切到对应页 |
| `page` / `n` | 下一页 |
| `auto` | 恢复自动轮播 |
| `config` | 立即进入 AP 配网 |
| `factory` | 清除配置并重启 |
| `status` | 打印状态 |
| `hist` | 打印历史 JSON |

---

## 3. 编译

```bash
cd /home/hotyuo/tio/firmware
sudo -E $(which pio) run -e esp8266-v4
```

## 4. 烧录

```bash
sudo -E $(which pio) run -e esp8266-v4 -t upload --upload-port /dev/ttyUSB0
```

或直接用固件文件：
```bash
esptool.py --before default-reset --before-delay 5 --chip esp8266 \
    --port /dev/ttyUSB0 --baud 460800 write_flash 0x0 releases/v4.0.0/firmware_v4.0.0.bin
```

## 5. 串口监控

```bash
sudo -E $(which pio) device monitor -p /dev/ttyUSB0 -b 115200
```

## 6. 目录结构

```
releases/v4.0.0/
├── README.md              # 本文件
├── firmware_v4.0.0.bin    # 烧录文件 (356KB)
├── src_esp8266_v4/        # 源码
│   ├── pins.h             # 引脚定义
│   ├── config.h/cpp       # 配置存储 (EEPROM)
│   ├── sensors.h/cpp      # 传感器采集
│   ├── aht20.h            # AHT20 驱动 (软件 I2C)
│   ├── bmp280.h           # BMP280 驱动 (软件 I2C)
│   ├── max30102.h/cpp     # MAX30102 驱动 (硬件 Wire)
│   ├── i2c_sw.h           # 软件 I2C 底层驱动
│   ├── main.cpp           # 主程序 (OLED 4 界面 + 调度)
│   ├── net_mgr.h/cpp      # 网络管理 (AP↔STA 状态机 + Web 页)
│   ├── mqtt_mgr.h/cpp     # MQTT 通信
│   ├── mqtt_client.h/cpp  # MQTT 协议客户端
│   ├── alarm.h/cpp        # 报警模块
│   └── history.h/cpp      # 历史数据缓存 (内存环形)
└── docs/                  # 详细文档
```

---

## 7. 与上一版本 (v3.0) 的差异

| 项目 | v3.0 | v4.0 |
|------|------|------|
| MAX30102 | 无 | ✅ 支持 (共用传感器总线 D7/D8) |
| OLED 界面数 | 1 (环境+SSID) | 4 (环境/体征/网络/历史曲线) |
| OLED 翻页 | 无 | 自动 5s + 串口命令 |
| Web 数据页 | 无 | ✅ 实时页 + 历史曲线页 |
| Web 数据 API | 无 | ✅ /api/data /api/history |
| 历史数据 | 无 | 内存环形 (5min × 12 点 = 1h) |
| I2C 策略 | 全硬件 Wire (OLED 与传感器共用 D5/D6) | OLED 软件 I2C + 三颗传感器共用硬件 Wire (D7/D8) |
| 默认网络模式 | STA (有配置) / AP (无配置) | 同 v3.0 |
| 报警体征 | 无 | ✅ 血氧/心率 |
| 固件大小 | ~240KB | ~356KB (+48%) |

---

## 8. 参考

- 腾讯云开发者社区 ESP8266 接入文章: https://cloud.tencent.com/developer/article/1920918
- u8g2 文档: https://github.com/olikraus/U8g2_Arduino
- MAX30102 datasheet: https://www.analog.com/en/products/max30102.html
- BMP280 datasheet: https://www.bosch-sensortec.com/media/bst/downloads/all_overview/datasheets/datasheet_bst_bmp280_ds006.pdf
- AHT20 datasheet: https://www.aosense.com/UploadFile/20190105/20190105134117_279798.pdf
