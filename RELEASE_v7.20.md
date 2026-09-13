# Release v7.20 — ICU 环境监测系统稳定版

> **发布日期**: 2026-09-14
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台

---

## 📋 变更汇总 (v7.10 → v7.20)

| 版本 | 类型 | 说明 |
|------|------|------|
| v7.19 | revert | 去掉 daocloud 镜像源前缀，恢复原始镜像名 |
| v7.18 | fix | 监护记录 + 报警自动记录历史数据 |
| v7.17 | fix | 设备页全部/局域网/外网标签加紧凑按钮样式 |
| v7.16 | fix | 实时监护数据 + 趋势曲线图 + 环境数据修复 |
| v7.15 | perf | Docker 镜像改用 daocloud 国内源前缀 |
| v7.14 | fix | 患者卡片加解绑按钮 + 实时监护刷新不更新数据 |
| v7.13 | perf | Piper 模型下载使用国内镜像源 hf-mirror.com |
| v7.12 | fix | MQTT 改为免认证模式，解决 passwd 文件无法创建问题 |
| v7.11 | fix | passwd 文件移至 data 目录，解决 config 目录不可写问题 |
| v7.10 | fix | 加固 mosquitto 启动脚本 + 健康检查 |

---

## ✨ 功能特性

- **双平台固件**: ESP32-S3 全功能（传感器+屏幕+体征+OTA） / ESP8266 精简版（传感器+报警）
- **Docker 一键部署**: FastAPI + MQTT (Mosquitto) + SQLite
- **Web 监护台**: 实时监护屏 / 患者管理 / 医嘱 LIS / 出入量 / AI 规则评估 / 大屏 Dashboard
- **设备自动发现**: 局域网 UDP 广播，无需手填服务器 IP
- **OTA 远程升级**: 固件无线更新
- **环境监测**: 温湿度、CO₂ 等环境参数采集
- **多参数生命体征**: 心率、血氧等体征数据采集与上报

---

## 🚀 快速开始

### 后端部署

```bash
cd server && docker compose up -d
# Web 监护台:  http://<IP>:12090
# MQTT 端口:   18830
# UDP 设备发现: 12091
```

### 固件构建 & 烧录

```bash
cd firmware

# ESP32-S3
pio run -e esp32-s3
pio run -e esp32-s3 -t upload --upload-port /dev/ttyACM0

# ESP8266
pio run -e esp8266
pio run -e esp8266 -t upload --upload-port /dev/ttyUSB0
```

### Docker 一键烧录（免装 PlatformIO）

```bash
cd firmware/docker
sudo docker build -t envmon-firmware .
sudo docker run --rm --device=/dev/ttyACM0 \
  -v "$PWD/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp32-s3 /dev/ttyACM0
```

---

## 📁 项目结构

```
esp32-wsd/
├── firmware/          固件源码（PlatformIO，双平台）
├── server/            Docker 后端（FastAPI + MQTT + SQLite）
├── docs/              文档集
├── scripts/           通用脚本
└── README.md          项目说明
```

---

## ⚠️ 升级须知

- 从 v7.18 及之前升级：MQTT 已改为免认证模式，无需 passwd 配置
- Docker 镜像名已恢复原始命名（v7.19 revert 了 daocloud 前缀）
- 如需国内加速，可自行配置 Docker 镜像加速器
