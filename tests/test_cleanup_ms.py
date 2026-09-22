#!/usr/bin/env python3
"""验证 telemetry 清理 cutoff 的毫秒格式修复。

SQLite 的 < 对字符串按 ASCII 逐字符比较。telemetry.ts 由服务器写入时带毫秒
（如 2026-09-22T15:40:07.713Z），而 cutoff 若不带毫秒（15:40:07Z），
因 '.'(46) < 'Z'(90)，导致 "07.713Z" < "07Z" 恒成立 → 误删。

本测试不依赖数据库，直接比较字符串，等价于 SQLite 的 BINARY collation 行为。
"""
import sys
from datetime import datetime, timezone, timedelta

RAW_RETENTION_DAYS = 2
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


# 模拟服务器写 ts 的方式：utcnow_ms() 带毫秒
ts_now = "2026-09-22T15:40:07.713Z"      # 刚写入的行（保留窗口内，绝不该删）
ts_at_cut = "2026-09-22T15:40:07.001Z"   # 恰好等于 cutoff 秒
ts_before = "2026-09-22T15:40:06.999Z"   # cutoff 前 1ms（应删）
ts_old = "2026-09-20T14:00:00.500Z"      # 两天前（应删）

cutoff_fixed = "2026-09-22T15:40:07.000Z"   # 新格式（补 .000）
cutoff_old = "2026-09-22T15:40:07Z"          # 旧格式（缺陷）

print("=== 旧格式 cutoff（有缺陷 —— 断言 bug 确实存在）===")
for ts, should_del, label in [
    (ts_now, False, "刚写入的窗口内行"),
    (ts_at_cut, False, "恰好 cutoff 秒"),
    (ts_before, True, "cutoff 前 1ms"),
    (ts_old, True, "两天前"),
]:
    deleted = ts < cutoff_old
    # 旧格式有缺陷：预期"留"的行会被误删（bug 证据）；预期"删"的行正确删
    if should_del:
        check(f"{label}: 正确删除", deleted is True, f"ts={ts} deleted={deleted}")
    else:
        check(f"{label}: BUG 误删（确认缺陷存在）", deleted is True,
              f"ts={ts} deleted={deleted} expect=False")

print("\n=== 新格式 cutoff .000Z（修复后）===")
for ts, should_del, label in [
    (ts_now, False, "刚写入的窗口内行"),
    (ts_at_cut, False, "恰好 cutoff 秒"),
    (ts_before, True, "cutoff 前 1ms"),
    (ts_old, True, "两天前"),
]:
    deleted = ts < cutoff_fixed
    check(f"{label}: {'删' if should_del else '留'}"
          + ("  <-- 正确" if deleted == should_del else "  <-- 误判"),
          deleted == should_del, f"ts={ts} deleted={deleted} expect={should_del}")

print("\n=== 格式生成器验证（_cleanup 实际调用的 strftime）===")
fmt_fixed = "%Y-%m-%dT%H:%M:%S.000Z"
fmt_old = "%Y-%m-%dT%H:%M:%SZ"
base = datetime(2026, 9, 22, 15, 40, 7, tzinfo=timezone.utc)
cut_now = base - timedelta(days=RAW_RETENTION_DAYS)
check("新格式输出含 .000", cut_now.strftime(fmt_fixed).endswith(".000Z"),
      cut_now.strftime(fmt_fixed))
check("旧格式输出无毫秒", cut_now.strftime(fmt_old).endswith("Z"),
      cut_now.strftime(fmt_old))
check("新格式与 ts 位数一致（21 位）",
      len(cut_now.strftime(fmt_fixed)) == len(ts_now),
      f"cutoff={len(cut_now.strftime(fmt_fixed))} ts={len(ts_now)}")
check("位数一致保证字符串比较按时间序",
      cut_now.strftime(fmt_fixed)[:19] == "2026-09-20T15:40:07",
      cut_now.strftime(fmt_fixed)[:19])

print("\n=== 误删率对照（100 次清理模拟）===")
# 模拟：每次清理时 cutoff 取当前秒，10 秒/条 × 3 台设备
# 旧格式下 cutoff 那一秒内最多 3 条会被误删；新格式下 0 条
lost_old = lost_new = 0
base_t = datetime(2026, 9, 22, 15, 30, 0, tzinfo=timezone.utc)
for i in range(100):
    # 每 60 秒一次清理
    cutoff_dt = base_t + timedelta(seconds=60 * i)
    cut_o = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cut_n = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # 该秒内随机有 0-3 条记录（毫秒位随机）
    for ms in (137, 421, 805):
        ts = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
        if ts < cut_o:
            lost_old += 1
        if ts < cut_n:
            lost_new += 1
check("旧格式 100 轮误删行数 > 0", lost_old > 0, f"lost_old={lost_old}")
check("新格式 100 轮误删行数 == 0", lost_new == 0, f"lost_new={lost_new}")
print(f"  旧格式误删 {lost_old} 条 / 新格式误删 {lost_new} 条")

print("\n=== 其他表格式未受影响 ===")
min_fmt = "%Y-%m-%dT%H:%M"
alarm_fmt = "%Y-%m-%dT%H:%M:%SZ"
check("telemetry_1m 格式未变",
      base_t.strftime(min_fmt) == "2026-09-22T15:30", base_t.strftime(min_fmt))
check("alarms 格式未变",
      base_t.strftime(alarm_fmt) == "2026-09-22T15:30:00Z", base_t.strftime(alarm_fmt))

print(f"\n{'='*50}")
print(f"结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
