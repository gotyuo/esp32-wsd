# Release v7.50 — 修复「扫描局域网」同一设备重复出现

> **发布日期**: 2026-09-20
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> **基于**: commit `a5cc543`（v7.49.1）

---

## ✨ 变更汇总

修复「设备管理 → 🔍 扫描局域网」后**同一台设备在设备列表里出现两次**的问题。

### 根因
服务器扫描按 IP 探测设备 Web 门户并提取 `device_id`；当 Web JSON 解析不出真实 id 时（典型：
`esp32-7oled` 固件的 `/json` 不带 `dev`/`device_id` 字段），扫描会**兜底注册成 `esp-<ip>`**；
而同一台设备通过 MQTT/HTTP 遥测自动注册**真实 id**（如 `envmon8266-xxxx`）。
`devices` 表主键是设备 id，于是同一物理设备出现两行 → 前端渲染成两张卡。

### 修复（三层）
1. **服务端扫描去重**（`server/app/main.py`）
   - 解析不出 `device_id` 时不再盲目造 `esp-<ip>` 新行：先按 IP 查 `devices` 表，
     若该 IP 已登记真实 id 则直接沿用 → 从源头避免产生第二行。
   - `found` 结果按 `(device_id, ip)` 去重，同一设备不会被重复返回/入库。
   - ping-only 兜底分支同样复用。
2. **存量清理脚本**（`server/scripts/merge_esp_devices.py`，新增）
   - 把历史遗留的 `esp-<ip>` 行合并进同 IP 的真实 id 行，并迁移关联表
     （telemetry / telemetry_1m / thresholds / alarms / patient_devices /
     messages / monitor_sessions / device_scan_snapshots）中的 `device_id`。
   - **默认 dry-run 只预览**；`--execute` 才执行，执行前自动备份到 `server/backups/`，
     整批事务包裹、失败全回滚。
3. **固件补字段**（`firmware/esp32-7oled/src/net_mgr.cpp`）
   - `/json` 响应补上 `"dev":"..."` 与 `"device_id":"..."`（对齐 esp8266 v2.0.1 修复），
     缓冲区 512→640 字节。需重新编译烧录后生效。

### 验证
- `server/app/main.py`、`merge_esp_devices.py`：`py_compile` 通过。
- 合并脚本在模拟测试库演练：遥测并入、唯一键冲突保留真实行（丢弃冲突行）、
  患者绑定正确迁移、同 IP 无真实行的 `esp-` 行原样保留、备份生成 ✅。
- 固件改动为人工 review（沙箱无 PlatformIO 工具链），烧录前请先编译确认。

## 🔧 改动文件

| 文件 | 说明 |
|------|------|
| `server/app/main.py` | 扫描去重：IP→真实 id 复用 + found 去重 |
| `server/scripts/merge_esp_devices.py` | 新增：存量重复行清理脚本（dry-run 默认，`--execute` 带备份） |
| `firmware/esp32-7oled/src/net_mgr.cpp` | `/json` 增加 `dev`/`device_id` 字段 |
| `.rollback/20260920-dev-dup-fix/` | 修复前 server-main.py / net_mgr 快照（保留版本，可回滚） |

## 📋 升级指引

1. 重启后端加载新扫描逻辑（此后新扫描不再产生重复行）。
2. 在部署机执行存量清理（先预览，确认后执行）：
   ```bash
   python3 server/scripts/merge_esp_devices.py            # 预览
   python3 server/scripts/merge_esp_devices.py --execute  # 执行（自动备份）
   ```
3. esp32-7oled 设备重新编译烧录固件（可选；不刷机时服务端已按 IP 兜底沿用真实 id）。