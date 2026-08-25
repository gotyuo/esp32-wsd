# 重症监护环境监测系统 (ICU EnvMon)

> ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> 覆盖：环境监测 · 多参数生命体征 · 出入量 · 医嘱 LIS · AI 评估 · 双平台固件

## 一句话定位

面向 ICU/CCU/急诊的重症监护数据平台：ESP32-S3 或 ESP8266 采集环境与体征数据，通过 MQTT 上报到 FastAPI 后端，医师通过 Web 监护台查看实时监护屏、患者列表、检验 LIS、出入量与 AI 规则评估结果。两台设备共用同一 MQTT 服务器与后端，数据格式一致。

## 目录

```
tio/
├── firmware/                 固件源码（PlatformIO，双平台）
│   ├── platformio.ini        [env:esp32-s3] 全功能：传感器+屏幕+体征+OTA
│   ├── platformio_esp8266.ini / [env:esp8266] 精简：传感器+报警，无屏幕无体征
│   ├── src/                  ESP32-S3 源码（main/sensors/ui/alarm/mqtt/ota/net_mgr/config）
│   ├── src_esp8266/          ESP8266 源码（EEPROM+WiFi.scanNetworks+WebServer 配网）
│   ├── docs/esp8266-wiring.md ESP8266 接线/烧录指南
│   └── docker/               Docker 构建/烧录环境（一键：docker build + docker run 烧录）
├── server/                   Docker 后端（FastAPI + MQTT + SQLite）
│   ├── app/                  icu.py / main.py / models.py / mqtt_bridge.py
│   ├── static/               前端 UI（实时监护/患者管理/医嘱/历史/大屏 dashboard）
│   ├── schema.sql            17 张数据表
│   ├── Dockerfile            EXPOSE 12090 + uvicorn
│   └── docker-compose.yml
├── mosquitto/                MQTT 证书与配置
├── docs/                     本文档集
│   ├── 01-硬件接线.md ~ 08-AI-评估规则.md
└── scripts/                  通用脚本
```

## 快速开始（后端）

```bash
cd server && docker compose up -d          # 端口 12090:Web / 18830:MQTT / 12091:UDP 设备发现
# 首次 admin 账号由 bootstrap 生成；登录 http://<IP>:12090
# docker compose up -d 会自动拉起 discovery 服务，设备 LAN 模式下开机即自动发现服务器
```

> **设备首次接入（v2.3）**：设备开 AP 配网 → 手机连 `ENVMON-XXXXXX` → 打开配网页 → 保持默认「局域网自动发现」模式，填好 WiFi SSID/密码后保存。设备连上同一无线网后自动广播 UDP 探测，收到 discovery 服务应答后自动保存 MQTT 配置并重启上线，**无需手填服务器 IP**。外网/固定 IP 场景：切换为「手动指定」模式并填写地址。

## 固件构建 & 烧录（本机 PlatformIO）

```bash
cd firmware
# 本机需装 python3 + platformio：sudo pip3 install platformio
# ESP32-S3（串口 /dev/ttyACM0，CH340）
sudo -E $(which pio) run -e esp32-s3
sudo -E $(which pio) run -e esp32-s3 -t upload --upload-port /dev/ttyACM0
# ESP8266（串口 /dev/ttyUSB0，CP210x/CP2102）
sudo -E $(which pio) run -e esp8266
sudo -E $(which pio) run -e esp8266 -t upload --upload-port /dev/ttyUSB0
```

## 固件构建 & 烧录（Docker 一键，免装 PlatformIO）

```bash
cd firmware/docker
# 1) 构建镜像（内含 PlatformIO + esp32-s3/esp8266 工具链，首次较慢）
sudo docker build -t envmon-firmware .

# 2) 编译 + 烧录 ESP32-S3（端口 /dev/ttyACM0）
sudo docker run --rm --device=/dev/ttyACM0 \
  -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp32-s3 /dev/ttyACM0

# 3) 编译 + 烧录 ESP8266（端口 /dev/ttyUSB0）
sudo docker run --rm --device=/dev/ttyUSB0 \
  -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp8266 /dev/ttyUSB0

# 4) 仅编译不烧录（产出 firmware/.pio/build/<env>/firmware.bin）
sudo docker run --rm -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware build esp32-s3
```

## 硬件接线

- ESP32-S3：见 `docs/01-硬件接线.md`
- ESP8266：见 `firmware/docs/esp8266-wiring.md`（AHT20+BMP280 并联 I2C D1/D2；LED R/G D6/D7；蜂鸣 D5；串口 TXD→RXD / RXD→TXD，3.3V 电平，烧录拉低 GPIO0）

## 版本

| 版本 | 日期 | 关键变化 |
|------|------|---------|
| v1.0 | 2026-08-15 | AHT20+BMP280 环境监测 + Web 仪表盘 |
| v1.7 | 2026-08-17 | OTA 双 slot 远程升级（固件 869KB） |
| v1.8 | 2026-08-17 | AP 扫描 1.5s + STA 60s fallback |
| v2.1 | 2026-08-18 | ICU 多患者管理 + 体征/医嘱/LIS 5 张新表 |
| v2.2 | 2026-08-18 | 实时监护 Tab + 出入量 + AI 规则评估 + 端口 12090 |
| v2.3 | 2026-08-23 | ESP8266 双平台固件 + 医嘱/设备历史/聚光灯大屏 |
| v2.4 | 2026-08-24 | 患者管理并排多行展示 + Docker 固件烧录环境 |
| v2.5 | 2026-08-25 | 固件集成 MAX30102 血氧 + AD8232 心电 |

## 引脚速查

### ESP32-S3

| 功能 | GPIO |
|------|------|
| I2C SDA/SCL (AHT20+BMP280) | 8 / 9 |
| SPI TFT SCK/MOSI/CS/DC/RST/BL | 12/11/10/7/6/5 |
| 麦克风 ADC | 4 |
| 喇叭 PWM | 21 |
| RGB LED R/G/B | 15/16/17 |
| 蜂鸣器 | 18 |
| ECG(AD8232) / Pulse(PPG) / Breath ADC | 1 / 2 / 3 |
| 血氧/脉率(MAX30102, I2C) | 8(SDA) / 9(SCL) |

### ESP8266 (ESP-12F)

| 功能 | GPIO |
|------|------|
| I2C SDA/SCL (AHT20+BMP280) | D2(4) / D1(5) |
| 麦克风 ADC | A0（需分压 0-1V） |
| LED R / G | D6(12) / D7(13) |
| 蜂鸣器 | D5(14) |
| 串口 | GPIO1(TX) / GPIO3(RX) |

> ESP8266 无屏幕、无体征 ADC、无 OTA；配网走 WiFi AP + Web 页面，状态用 LED 颜色指示。

> 血氧/脉率由 **MAX30102**（I2C，与 AHT20/BMP280 并联，地址 0x57）、
> 心电心率由 **AD8232**（模拟输出→GPIO1，5V 供电）负责，固件已集成并上报 `sp_o2/pr_hr/ecg_hr`。

## 技术栈

- 固件：ESP32-S3 / ESP8266，PlatformIO，Arduino 原生 API，可通过 `firmware/docker` 一键构建烧录
- 后端：Python 3.12 + FastAPI + Uvicorn + SQLite（docker compose 一键部署）
- MQTT Broker：Mosquitto 18830
- 前端：纯 HTML + Canvas sparkline + 原生 JS（无框架）
- AI 评估：规则引擎（8 系统风险分级 + 趋势 + 摘要），不依赖外部 LLM

## 许可

本代码为内部 ICU 科研/学习项目。
