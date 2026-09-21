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

访问 `http://<服务器IP>:12090`，使用 `.env` 中 `ADMIN_USER`/`ADMIN_PASS` 登录。

首次登录后请立即修改默认管理员密码（右上角头像 → 修改密码）。

## 端口

| 端口 | 用途 |
|------|------|
| 12090 | Web 管理界面 + REST API |
| 18830 | MQTT（设备接入，映射到容器内 1883） |

## 数据库

SQLite 文件挂载在主机 `./data/envmon.db`（容器外持久化，重部署不丢失）。
备份：直接复制该文件即可（建议停服或使用 sqlite 在线备份）。

## 环境变量（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| MQTT_PORT | 18830 | MQTT 对外端口 |
| WEB_PORT | 12090 | Web 对外端口 |
| MQTT_USER | envmon | MQTT 账号（终端配网页同填） |
| MQTT_PASS | envmon-secret | MQTT 密码 |
| ADMIN_USER | admin | 首次启动自动创建的管理员账号 |
| ADMIN_PASS | admin123 | 管理员密码（**必须修改**） |
| SESSION_TTL_HOURS | 168 | 登录会话有效期（小时） |
| RAW_RETENTION_DAYS | 7 | 原始数据保留天数 |
| MINUTE_RETENTION_DAYS | 400 | 分钟聚合保留天数 |
| INTEGRATION_TOKEN | (空) | 集成平台接收接口鉴权 token（设置后需带 X-Integration-Token 头） |
| ICU_DEPT_KEYWORDS | ICU,重症,CCU,EICU | 集成平台自动建档的重症科室关键词（逗号分隔；也有 app_settings `integration.icu_depts` 可运行时改） |
| INTEGRATION_TZ_OFFSET | 8 | 集成平台消息时间相对 UTC 的偏移小时（默认东八区） |

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
| POST | /api/integration/receive | 集成平台 XML 接收（医嘱/检查/检验/病理） | 配置 token 后需鉴权 |
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

## 集成平台数据接收（v7.54）

HIS/医院集成平台通过 XML 消息推送临床数据（医嘱 / 检查结果 / 检验结果 / 病理结果），
本系统接收后解析入库，并应答集成平台规范 XML。

**接收地址**：`POST /api/integration/receive`
**请求体**（Content-Type: `application/xml` 或 `text/xml`）：

```xml
<Request>
  <Header>
    <SourceSystem>02</SourceSystem>
    <MessageID>1033424</MessageID>
  </Header>
  <Body>
    <AddOrdersRt>
      <PATPatientID>0000774959</PATPatientID>
      <PAADMVisitNumber>19204512</PAADMVisitNumber>
      <OEORIInfoList>
        <OEORIInfo>
          <OEORIOrderItemID>17770682||171</OEORIOrderItemID>
          <OEORIARCItmMastDesc>快速血糖</OEORIARCItmMastDesc>
          ...
        </OEORIInfo>
      </OEORIInfoList>
    </AddOrdersRt>
  </Body>
</Request>
```

**应答体**：

```xml
<Response>
  <Header>
    <SourceSystem>CDSS</SourceSystem>
    <MessageID>1033424</MessageID>
  </Header>
  <Body>
    <ResultCode>0</ResultCode>
    <ResultContent>医嘱接收成功</ResultContent>
  </Body>
</Response>
```

- `ResultCode`：`0` 成功；`1` 患者未建档（非重症科室）；`2` XML 错误；`3` 业务类型暂不支持；`4` 内部错误
- **幂等**：同一 `MessageID` 重复推送直接返回成功，不重复入库
- **患者规则**：患者档案不存在时，仅当消息科室命中重症科室关键词才自动建档；
  非重症科室患者返回 `ResultCode=1`。关键词通过 app_settings `integration.icu_depts`
  （逗号分隔，如 `ICU,重症,CCU,ESLBQ`）或环境变量 `ICU_DEPT_KEYWORDS` 配置。
- **消息日志**：每条消息原始 XML 记录在 `integration_messages` 表，便于排查/重放。
- **鉴权**：配置 `INTEGRATION_TOKEN` 后，请求需带 `X-Integration-Token: <token>` 头；
  未配置默认放行（内网对接调试用），生产请务必配置。

> 📌 当前业务节点支持情况：`AddOrdersRt`（医嘱）已完整实现；
> 检查/检验/病理结果节点的解析器已预留分派框架，需按集成平台实际 XML 样例补齐字段映射。
