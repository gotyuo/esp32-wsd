# Release v7.49 — ICU 环境监测系统 体征趋势回退修复版

> **发布日期**: 2026-09-19
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> **基于**: commit `3037d3e`（v7.48）

---

## ✨ 变更汇总

本版 1 个提交，修复实时监护「📈 体征趋势」面板在所选窗口（默认最近24小时）内无新数据时**画不出曲线**的问题。

### 1. 窗口内无数据 → 回退显示最近记录曲线 + 提示
- **根因**：后端 `get_vitals` 在「窗口内无数据但存在更早数据」时按设计返回 `points=[] + out_of_window=true + latest_ts`，但前端初始加载（`refreshMonitor`）完全忽略 `out_of_window`，直接显示「暂无体征数据」；`drawMonitorTrend` 又在 pts 为空时提前返回，即使设置了提示也不显示——设计中的「持续显示最近记录」从未真正生效。
- **修复**：
  - 新增 `loadMonitorTrendFallback()`：检测到 `out_of_window` 后改取 `vitals?hours=8760`（含遥测回退，与页面其它全量调用一致），再按当前窗口过滤；仍无新点则走既有回退分支——`NOTE` 提示「所选窗口内无新数据，持续显示最近记录（最新 X）」+ `MONITOR_TREND_FALLBACK=true`，**用最近真实记录画出曲线**。
  - `refreshMonitor()` 初始加载与 `setTrendHours()` 按钮切换（30分/1时/3时/6时/12时/24时）统一复用该回退路径，不再各自为政。
  - `drawMonitorTrend()`：pts 为空且存在提示时，先以橙色提醒条显示原因，再显示空状态；提示为空时保持原「暂无体征数据」。
  - 抽取 `syncMonitorSparklines()` 消除两处重复的卡片迷你走势刷新逻辑。
- **验证**：3 个 `<script>` 块 `node --check` 通过、CSS 花括号配平；以真实 24h 数据 + 窗口外数据两种场景做静态推演，确认回退触发、备注显示、正常路径零影响。

## 🔧 改动文件

| 文件 | 说明 |
|------|------|
| `server/app/static/index.html` | 趋势回退逻辑（+44/-26 行，仅前端） |
| `.rollback/20260919-trend-24h-fallback/` | 修复前 index.html 快照（保留版本，可回滚） |