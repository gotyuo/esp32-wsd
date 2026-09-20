# Release v7.51 — 修复「设备扫描不到 / 在线一段时间就离线」

> **发布日期**: 2026-09-20
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> **基于**: commit `0521c6f`（v7.50）

---

## ✨ 变更汇总

排查「设备经常扫描不到」「在线一段时间就离线」两类现象，定位 4 个根因并修复：

### 1. 扫描子网被硬编码（服务器配置）
- **根因**：`docker-compose.yml` 中 backend 的 `DISC_IP=${DISC_IP:-172.22.22.83}` 把扫描
  出口 IP 默认钉死在 `172.22.22.83`——只要部署机内网 IP 不是它（家庭/办公网通常
  192.168.x.x），后端扫描就会去扫 `172.22.22.0/24`，永远发现不了设备。
- **修复**：默认值改空（走自动探测）；多网卡/容器误判时才在 `.env` 显式配
  `DISC_IP=宿主机实际内网IP`，`.env.example` 注释同步强化。

### 2. esp8266 WiFi 掉线 60s 永久切 AP 配网（固件）
- **根因**：`src_esp8266_4oled/net_mgr.cpp` 在「曾经连上过、之后掉线」超过 60s 时
  `startAP()` 切配网 AP，且切过去后 loop 不再回 STA —— 路由抖动/信号波动会把设备
  永久“卡”在配网模式：MQTT 断、扫描不到、页面离线。
- **修复**：曾经连上过的设备掉线后**无限重试回 STA，绝不切 AP**；仅「首次配网从未
  连上」才在 15s 超时后进 AP（保留配网兜底）。另将 `WiFi.disconnect(true,false)`
  改为 `(false,false)`，避免重连时关闭 radio 增加抖动。

### 3. 自研 MQTT 栈“假在线”不重连（固件，全平台）
- **根因**：`mqtt_client.cpp` 只发 PINGREQ、不校验 PINGRESP/读超时；TCP 半开时
  `_connected` 恒为 true，设备自认为在线、永不重连；broker 侧 45s 收不到包已断开并
  发 LWT offline。
- **修复**：
  - `loop()` 增加活性检测：连续 `keepalive×2.5` 秒收不到任何数据（PINGRESP/下发消息）
    即 `_connected=false` + `_net->stop()`，交由 `ensureConn()` 退避重连。
  - `mqtt_mgr.loop()`：WiFi 断开时**立即 `disconnect()` 复位 MQTT 连接**，WiFi 恢复后
    `ensureConn()` 自动重连 —— 解决“WiFi 短暂断开又恢复、MQTT 仍是假连接”的场景。
  - 应用到全部活跃固件变体（esp32-7oled / esp8266_4oled / esp32_4oled /
    esp32_6oled / esp8266_6oled / src 默认）；`legacy/`、`releases/` 历史存档未动。

### 4. 离线判定窗口 90s 与上报间隔的配置陷阱（服务器）
- **根因**：`OFFLINE_TIMEOUT_S=90`，若设备上报间隔被调到 ≥90s（阈值页可下发），
  必然被频繁判离线。
- **修复**：默认提到 **120s**（`main.py`/`aggregator.py` 默认值 + compose 环境变量统一），
  并在 `.env.example` 注明「必须大于设备上报间隔，调大上报间隔时同步调大」。

## 🔧 改动文件

| 文件 | 说明 |
|------|------|
| `server/docker-compose.yml` | DISC_IP 默认空（自动探测）+ OFFLINE_TIMEOUT_S=120 环境变量 |
| `server/.env.example` | DISC_IP / OFFLINE_TIMEOUT_S 配置说明强化 |
| `server/app/main.py` | OFFLINE_TIMEOUT_S 默认 90→120 |
| `server/app/aggregator.py` | OFFLINE_TIMEOUT_S 默认 90→120 |
| `firmware/src_esp8266_4oled/net_mgr.cpp` | 掉线永不切 AP；disconnect 不关 radio |
| `firmware/*/mqtt_client.cpp`（6 份活跃） | MQTT 活性检测：keepalive×2.5s 无数据强制断链 |
| `firmware/*/mqtt_mgr.cpp`（esp8266_4oled / esp32-7oled） | WiFi 断开立即断开 MQTT，恢复后自动重连 |
| `.rollback/20260920-wifi-mqtt-resilience/` | 修复前快照（保留版本，可回滚） |

## 📋 升级指引

1. **服务器**：重启后端 + discovery 容器。若部署机是多网卡/容器网络、扫描仍找不到设备，
   在 `.env` 按宿主机实际内网 IP 配置 `DISC_IP=192.168.1.100`（见 `.env.example`）。
2. **固件**：esp8266（src_esp8266_4oled）与 esp32-7oled 需重新编译烧录；
   其余变体（4oled/6oled 等）可选同步升级（已带同一修复）。
3. 固件改动为人工 review（沙箱无 PlatformIO 工具链），**烧录前请先编译确认**。