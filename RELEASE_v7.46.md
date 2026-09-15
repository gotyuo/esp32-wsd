# Release v7.46 — ICU 环境监测系统 Bug 修复版

> **发布日期**: 2026-09-16
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> **基于**: commit `431d895` (v7.45 之后)

---

## 📋 变更汇总

本次发布修复了系统 Bug 测试报告中发现的 **19 个缺陷**（BUG-001 ~ BUG-019），涵盖 P0 级系统瘫痪、P1 级安全泄露与并发正确性、P2 级数据质量与性能、P3 级体验问题。

| Bug 编号 | 等级 | 模块 | 修复内容 |
|----------|------|------|----------|
| BUG-001 | P0 | 后端/icu | 监护页轮询关闭共享数据库连接导致全系统持续 500 |
| BUG-002 | P1 | 后端/安全 | `/api/dashboard` 补鉴权，防止 PHI 越权泄露 |
| BUG-003 | P1 | 后端/安全 | `/api/patients/{pid}/vitals` GET 补鉴权 |
| BUG-004 | P1 | 后端/安全 | `/api/tts/status` 补鉴权 |
| BUG-005 | P1 | 后端/安全 | 设备 HTTP 上报端点添加 MQTT 凭据认证 |
| BUG-006 | P1 | 后端/db | OTA 上传改为原子事务，避免 is_latest 全部丢失 |
| BUG-007 | P1 | 后端/db | `_locked_scope` 异常路径锁泄漏修复 |
| BUG-008 | P1 | 后端/db | `query_locked` 系列内部加锁 |
| BUG-009 | P2 | 后端/db | `first_seen` 统一 ISO8601 UTC 格式 |
| BUG-010 | P2 | 后端/main | `/api/vitals` 数据源动态检测 |
| BUG-011 | P2 | 后端/main | `or` 链取值改为显式判空 |
| BUG-012 | P2 | 后端/main | `ip_addr` 运算符优先级修复 |
| BUG-013 | P2 | 后端/db | `execute()` 返回 rowcount，新增 `execute_insert()` |
| BUG-014 | P2 | 后端/tts | TTS 合成加超时（连接 5s / 消息 10s） |
| BUG-015 | P2 | 后端/tts | `IncompleteReadError` 归一化为 `ConnectionError` |
| BUG-016 | P2 | 后端/db | `delete_user` 原子化，避免 TOCTOU 竞态 |
| BUG-017 | P2 | 后端/agg | 聚合器轮询改 60s + 清理节流 + 单列索引 |
| BUG-018 | P2 | 后端/db | `localnow()` 改用 `ZoneInfo` |
| BUG-019 | P3 | 前端 | 添加 `/favicon.ico` 路由 |

---

## 🔧 修复详情

### P0 严重 (1 个)

**BUG-001**: `icu._get_conn()` 返回的线程局部连接被 `latest_signs()` 和 `get_vitals()` 回退分支 `close()` 后，连接对象仍缓存在 `threading.local`，后续请求拿到已关闭连接触发 `ProgrammingError`，导致全系统 ICU 业务持续 500。
- 修复：删除两处 `conn.close()`，`_get_conn()` 增加连接健康检查（`SELECT 1`），检测到已关闭连接时自动重建。

### P1 高 (7 个)

**BUG-002/003/004**: 三个 API 端点无鉴权，患者 PHI / 体征 / TTS 配置可未认证读取。
- 修复：补加 `Depends(require_user)`。

**BUG-005**: 设备 HTTP 上报端点无认证，可伪造患者体征数据。
- 修复：新增 `require_device_auth` 依赖，复用 MQTT 凭据做 HTTP Basic Auth 校验。

**BUG-006**: OTA 上传事务拆散，竞态可致 `is_latest` 全部丢失。
- 修复：整个函数包进 `_lock`，UPDATE + INSERT 同一事务原子完成。

**BUG-007**: `_locked_scope` 异常路径锁泄漏 → 全站死锁。
- 修复：异常时先 `rollback()` 再 `release()`，`release()` 放入独立 `finally`。

**BUG-008**: `query_locked` 系列未持锁操作共享连接。
- 修复：函数内部改为 `with _lock:`。

### P2 中 (10 个)

**BUG-009**: `first_seen` 使用 `DATETIME('now')` 格式不一致。
- 修复：改用参数绑定的 `utcnow()`。

**BUG-010**: `/api/vitals` 数据源硬编码 `esp8266`。
- 修复：新增 `_detect_source()` 按 `fw`/`platform` 字段动态判定。

**BUG-011**: `or` 链取值吞掉合法 `0` 值。
- 修复：改用 `next((v for v in (...) if v is not None), None)`。

**BUG-012**: `ip_addr` 运算符优先级错误。
- 修复：加括号明确优先级。

**BUG-013**: `execute()` 返回 `lastrowid` 而非删除行数。
- 修复：`execute()` 返回 `rowcount`，新增 `execute_insert()` 供 INSERT 调用方使用。

**BUG-014**: TTS 合成全程无超时。
- 修复：连接 5s、单条消息读取 10s 超时。

**BUG-015**: `IncompleteReadError` 未归一化。
- 修复：捕获并归一化为 `ConnectionError`，使上层返回 503。

**BUG-016**: `delete_user` 检查与删除非原子（TOCTOU）。
- 修复：单事务原子 SQL，子查询计数 + 两删除同事务。

**BUG-017**: 聚合器 15s 全量轮询 + 清理全表扫描。
- 修复：轮询改 60s，清理节流至每 60s，为 `telemetry(ts)` 和 `alarms(ts)` 建单列索引。

**BUG-018**: `localnow()` 硬编码 UTC+8。
- 修复：改用 `zoneinfo.ZoneInfo` 读取 `TZ` 环境变量。

### P3 建议 (1 个)

**BUG-019**: `favicon.ico` 404。
- 修复：新增 `/favicon.ico` 路由，无图标文件时返回 204。

---

## 📦 涉及文件

| 文件 | 修改内容 |
|------|----------|
| `server/app/icu.py` | BUG-001: `_get_conn()` 健康检查 |
| `server/app/main.py` | BUG-001/002/003/004/005/010/011/012/019 |
| `server/app/db.py` | BUG-006/007/008/009/013/016/017/018 |
| `server/app/tts.py` | BUG-014/015 |
| `server/app/aggregator.py` | BUG-017 |

---

## ✅ 测试通过项

所有原有测试通过项继续有效（登录鉴权、设备/患者管理、体征上报、医嘱/检验/出入量、报警、AI 评估、备份、XSS 转义、WebSocket 推送等）。
