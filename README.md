# 重症监护环境监测系统 (ICU EnvMon)

> ESP32-S3 边缘节点 + Docker 后端 + Web 监护台
> 覆盖：环境监测 · 多参数生命体征 · 出入量 · 医嘱 LIS · AI 评估

## 一句话定位

面向 ICU/CCU/急诊的重症监护数据平台：一台 ESP32-S3 采集环境与体征数据，通过 MQTT 上报到 FastAPI 后端，医师通过 Web 监护台查看实时监护屏、患者列表、检验 LIS、出入量与 AI 规则评估结果。

## 目录

```
tio/
├── firmware/            ESP32-S3 固件源码（PlatformIO，零第三方库）
│   ├── envmon-v1.0/     固件 v1.0 发布包
│   └── src/             main / sensors / ui / alarm / mqtt / ota / net_mgr / config
├── server/              Docker 后端（FastAPI + MQTT + SQLite）
│   ├── app/             icu.py(重症) / main.py / models.py / mqtt_bridge.py
│   ├── static/          前端 UI（含 实时监护/患者管理 双 Tab）
│   ├── schema.sql       15 张数据表
│   ├── Dockerfile       EXPOSE 12090 + uvicorn --port 12090
│   └── docker-compose.yml
├── mosquitto/           MQTT 证书与配置
├── docs/                本文档集
│   ├── 01-硬件接线.md
│   ├── 02-服务器部署.md
│   ├── 03-设备配网.md
│   ├── 04-系统操作.md
│   ├── 05-故障排查.md
│   ├── 06-API-接口文档.md
│   ├── 07-临床-数据字典.md
│   └── 08-AI-评估规则.md
└── scripts/             通用脚本
```

## 快速开始

```bash
cd server && docker compose up -d          # 端口 12090:Web / 18830:MQTT
# 首次 admin 账号在容器启动时由 bootstrap 生成
# 登录 http://<IP>:12090
```

## 版本

| 版本 | 日期 | 关键变化 |
|------|------|---------|
| v1.0 | 2026-08-15 | AHT20+BMP280 环境监测 + Web 仪表盘 |
| v1.7 | 2026-08-17 | OTA 双 slot 远程升级（固件 869KB） |
| v1.8 | 2026-08-17 | AP 扫描 1.5s + STA 60s fallback |
| v2.1 | 2026-08-18 | ICU 多患者管理 + 体征/医嘱/LIS 5 张新表 |
| v2.2 | 2026-08-18 | 实时监护 Tab + 出入量 + AI 规则评估 + 端口 12090 |

## 引脚速查

| 功能 | GPIO |
|------|------|
| I2C SDA/SCL (AHT20+BMP280) | 8 / 9 |
| SPI TFT SCK/MOSI/CS/DC/RST/BL | 12/11/10/7/6/5 |
| 麦克风 ADC | 4 |
| 喇叭 PWM | 21 |
| RGB LED R/G/B | 15/16/17 |
| 蜂鸣器 | 18 |
| **ECG 心电 ADC** | **25** |
| **Pulse 脉搏 ADC** | **27** |
| **Breath 呼吸 ADC** | **14** |

## 技术栈

- 固件：ESP32-S3，PlatformIO，Arduino 原生 API（零第三方库），1MB 程序 + 1MB NVS
- 后端：Python 3.12 + FastAPI + Uvicorn + SQLite
- MQTT Broker：Mosquitto 18830
- 前端：纯 HTML + Canvas sparkline + 原生 JS（无框架）
- AI 评估：规则引擎（8 系统风险分级 + 趋势 + 摘要），不依赖外部 LLM

## 许可

本代码为内部 ICU 科研/学习项目。