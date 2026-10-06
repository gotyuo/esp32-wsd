# 外网设备接入说明

## 问题

ESP 设备配置为"手动指定模式"（填入服务器公网域名/端口）后，设备无法上报数据，
后端扫描也找不到设备。

## 根因

1. **MQTT 端口未暴露**：设备手动模式填的 `mqtt_port`（默认 18830）是 MQTT broker 端口。
   如果服务器的路由器/防火墙没有将 18830 端口转发到内网，设备从外网无法连接 MQTT broker。

2. **LAN 扫描范围限制**：`POST /api/devices/scan` 只扫描服务器所在子网（ARP + HTTP 探测），
   外网设备不在同一子网，无法被主动发现。

3. **固件仅支持 MQTT**：ESP32/ESP8266 固件目前只通过 MQTT 上报数据，没有 HTTP POST 回退通道。
   如果 MQTT 不可达，数据无法到达服务器。

## 解决方案

### 方案 A：暴露 MQTT 端口（推荐）

在路由器/防火墙上将 MQTT broker 端口（18830）转发到服务器内网 IP。
设备配置手动模式填入公网域名 + 18830 端口即可。

### 方案 B：使用 HTTP 接入（无需 MQTT）

外网设备可以通过 HTTP POST 推送数据到服务器：

```
POST /api/ingest
Content-Type: application/json
Authorization: Bearer <admin_token>

{"device_id":"ext-001","temp_c":25.5,"hum_pct":60,"pres_hpa":1013}
```

在服务器前端的"设备接入 → 外网设备"页面可以手动注册外网设备。

### 方案 C：VPN/内网穿透

使用 Tailscale、ZeroTier 等组网工具，让外网设备加入同一虚拟内网，
然后使用"局域网自动发现"模式。

## 相关 API

| API | 说明 |
|-----|------|
| `POST /api/devices/scan` | 扫描局域网子网（仅限内网设备） |
| `GET /api/discover/devices?network=external` | 列出数据库中的外网设备 |
| `POST /api/ingest` | HTTP 数据接入（需 admin 认证） |
| `POST /api/telemetry` | HTTP 遥测上传（设备认证，内网免认证） |
