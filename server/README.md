# 环境监测系统 - 服务器端

接收各 ESP32 终端采集数据的管理平台。Docker 一键部署（Mosquitto MQTT + FastAPI + SQLite）。

## 功能（v2.0）

- **MQTT 数据接入**：接收终端遥测/在线状态/报警，QoS1
- **多设备管理**：设备列表、在线状态、重命名、删除
- **实时仪表盘**：WebSocket 推送实时数据 + Canvas 历史曲线（1h/24h/7d/30d）
- **远程配置下发**：按设备设置报警阈值/上报间隔/蜂鸣开关，MQTT 实时下发
- **报警系统**：服务端阈值判定 → 报警记录 → 浏览器声音+横幅推送
- **多用户登录**：用户名密码（PBKDF2 加密），管理员/观察者角色
- **数据保留**：原始数据 7 天，分钟聚合 400 天（可配置）

## 快速开始

```bash
cp .env.example .env      # 修改 ADMIN_PASS 等重要密码
./docker-pull-cn.sh eclipse-mosquitto 2   # 国内网络拉取镜像（可选）
docker compose up -d --build
```

访问 `http://<服务器IP>:8627`，使用 `.env` 中 `ADMIN_USER`/`ADMIN_PASS` 登录。

首次登录后请立即修改默认管理员密码（右上角头像 → 修改密码）。

## 端口

| 端口 | 用途 |
|------|------|
| 8627 | Web 管理界面 + REST API |
| 18830 | MQTT（设备接入，映射到容器内 1883） |

## 数据库

SQLite 文件挂载在主机 `./data/envmon.db`（容器外持久化，重部署不丢失）。
备份：直接复制该文件即可（建议停服或使用 sqlite 在线备份）。

## 环境变量（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| MQTT_PORT | 18830 | MQTT 对外端口 |
| WEB_PORT | 8627 | Web 对外端口 |
| MQTT_USER | envmon | MQTT 账号（终端配网页同填） |
| MQTT_PASS | envmon-secret | MQTT 密码 |
| ADMIN_USER | admin | 首次启动自动创建的管理员账号 |
| ADMIN_PASS | admin123 | 管理员密码（**必须修改**） |
| SESSION_TTL_HOURS | 168 | 登录会话有效期（小时） |
| RAW_RETENTION_DAYS | 7 | 原始数据保留天数 |
| MINUTE_RETENTION_DAYS | 400 | 分钟聚合保留天数 |

## REST API 摘要

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | /api/login | 登录获取 token | - |
| GET | /api/me | 当前用户信息 | 登录 |
| PUT | /api/me/password | 修改密码 | 登录 |
| PUT | /api/me/sound | 报警声音偏好 | 登录 |
| GET | /api/devices | 设备列表 | 登录 |
| PUT | /api/devices/{id} | 重命名设备 | 管理员 |
| DELETE | /api/devices/{id} | 删除设备 | 管理员 |
| GET | /api/realtime | 实时数据 | 登录 |
| GET | /api/history | 历史曲线 | 登录 |
| GET/PUT | /api/thresholds | 读取/保存并下发阈值 | PUT=管理员 |
| GET | /api/alarms | 报警记录 | 登录 |
| GET/POST/DELETE | /api/users | 用户管理 | 管理员 |
| POST | /api/ingest | HTTP 备用数据接入 | 管理员 |
| WS | /ws?token= | 实时推送 | 登录 |

认证方式：`Authorization: Bearer <token>` 请求头。

## 终端对接

固件 MQTT 主题：
- 上报：`envmon/{device_id}/telemetry`，JSON `{"t":25.5,"h":60,"p":1013,"rssi":-50,"fw":"1.0.0"}`
- 状态：`envmon/{device_id}/status`，`online` / `offline`
- 配置请求：`envmon/{device_id}/config/req`（设备重启后请求下发）
- 接收配置：`envmon/{device_id}/config`，JSON 阈值参数

## 国内网络注意

Docker Hub 被墙时使用 `./docker-pull-cn.sh` 从中文镜像源拉取并重打标签，
或自行修改 `/etc/docker/daemon.json` 的 `registry-mirrors`。