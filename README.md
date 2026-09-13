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
| v1.0 | 2026-08-17 | AHT20+BMP280 环境监测 + Web 仪表盘 |
| v1.7 | 2026-08-17 | OTA 双 slot 远程升级（固件 869KB） |
| v1.8 | 2026-08-17 | AP 扫描 1.5s + STA 60s fallback |
| v2.1 | 2026-08-18 | ICU 多患者管理 + 体征/医嘱/LIS 5 张新表 |
| v2.2 | 2026-08-18 | 实时监护 Tab + 出入量 + AI 规则评估 + 端口 12090 |
| v2.3 | 2026-08-24 | ESP8266 双平台固件 + 医嘱/设备历史/聚光灯大屏 |
| v2.4 | 2026-08-24 | 患者管理并排多行展示 + Docker 固件烧录环境 |
| v2.5 | 2026-08-25 | 固件集成 MAX30102 血氧 + AD8232 心电 |
| — | 2026-08-25 | LAN 自动发现(UDP) + ESP8266 OLED(SSD1306) 4-pin I2C |
| — | 2026-08-25 | OLED 分引脚 + ESP32-S3 OLED 变体(4线I2C/6线SPI) |
| — | 2026-08-26 | ESP8266 OLED u8g2 库 + 服务器端 v2.1 优化 |
| — | 2026-08-26 | 服务器时间东八区 + ESP8266 WiFi 掉线防御 |
| — | 2026-08-27 | RESET 键恢复出厂 + ESP32-S3 TFT TTS 语音 |
| — | 2026-08-27 | ESP8266 6 线 SPI TFT 变体 + I2C 自动探测 |
| — | 2026-08-28 | 前端/后端 13 项集中修复 + LAN 扫描线程化 |
| — | 2026-08-30 | UDP 设备发现修复 + TTS 语音合成端到端可用 |
| — | 2026-08-30 | 刷新按钮/在线指示灯/静默 catch 等 UI 修复 |
| — | 2026-08-31 | 在线判定修复（保留消息污染）+ discovery 统一口径 |
| — | 2026-09-01 | 医生档案/文字消息/设备绑定互斥 + JS 转义修复 |
| — | 2026-09-01 | MAX30102 与 AHT20/BMP280 I2C 引脚拆分 |
| — | 2026-09-02 | AI 模型配置 UI + 报警触发 LLM 分析 + 企微推送 |
| — | 2026-09-03 | 登录页 JS 语法修复 + AI helper script 包裹 |
| — | 2026-09-04 | 医生表单/企微表单修复 + 患者企微绑定 + 企微独立 tab |
| — | 2026-09-05 | UI 修复 + 医生企微字段 + MQTT 断线重连 |
| — | 2026-09-06 | ESP32-S3 OLED 黑屏修复 + 固件 v1.1.0/v1.2.0 |
| — | 2026-09-07 | 固件 Web 端口/路由补全 + JS 崩溃修复 + 21 个安全修复 |
| — | 2026-09-08 | 18 个低优先级 bug + 设备软删除 + 设备接入页面 |
| — | 2026-09-09 | 10 个逻辑缺陷修复 + SSID JSON escape |
| — | 2026-09-10 | 21 个后端 bug + ESP32-S3 Web 端口/NVS 分区修复 |
| — | 2026-09-11 | 设备关联 500 + 评估风险分级死分支 + 缺失端点 |
| — | 2026-09-12 | ESP8266 Soft WDT + 4-pin screen + AP stuck + captive portal |
| v2.0.0(fw) | 2026-09-12 | ESP8266 TFT 固件从头重写 |
| v3.0.0(fw) | 2026-09-12 | ESP8266 OLED 固件从头重写 + 引脚确认 |
| v4.0.0(fw) | 2026-09-13 | 新增 ESP8266 v4.0.0 与 v5.0.0(esp8266+4oled) |
| v5.0.0(fw) | 2026-09-13 | 新增 ESP8266 最小 OLED 固件 |
| v4.0 | 2026-09-13 | 修复 5 个 MQTT 通信与固件 bug |
| v5.0 | 2026-09-13 | 修复设备接入 7 个问题 |
| v5.1 | 2026-09-13 | 删除设备扫描残留 + MQTT 凭据一致性 |
| v5.2 | 2026-09-13 | AI 设置保存后测试连接失败修复 |
| v5.3 | 2026-09-13 | 医生选择/推送地址问题修复 |
| v5.4 | 2026-09-13 | 统一 AI 配置入口，消除两套不一致的设置界面 |
| v5.5 | 2026-09-13 | view-ai 移入 .container 修复页面重叠 |
| v5.6 | 2026-09-13 | 系统设置同时保存 AI 和微信配置 |
| v5.7 | 2026-09-13 | 设备接入内网/外网分成独立页签 |
| v5.8 | 2026-09-13 | 设备管理局域网发现改为页签切换 |
| v6.0 | 2026-09-13 | 整理固件目录为 4 个清晰平台 |
| v6.1 | 2026-09-13 | 修复系统设置中 AI 与企微配置互相覆盖不生效的 bug |

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
