"""实测：造 cutoff 边界数据，跑一轮真实 Aggregator._cleanup，验证 .000Z 修复生效。

容器内运行：python3 /tmp/probe_cleanup.py
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from app import db
from app import aggregator as aggmod

aggmod.aggregator_module = aggmod  # 仅用于引用常量


def main():
    cut = datetime.now(timezone.utc) - timedelta(days=aggmod.RAW_RETENTION_DAYS)
    FMT_NEW = "%Y-%m-%dT%H:%M:%S.000Z"
    FMT_OLD = "%Y-%m-%dT%H:%M:%SZ"

    probe = "dev_probe_ms"
    db.execute("INSERT OR IGNORE INTO devices (id,name) VALUES (?,?)", (probe, "probe"))

    # 造一批恰好落在 cutoff 那一秒的数据：旧格式会全删，新格式全留
    batch = []
    for ms in (1, 250, 500, 749, 999):
        ts = cut.strftime("%Y-%m-%dT%H:%M:%S") + ".{:03d}Z".format(ms)
        db.execute(
            "INSERT OR REPLACE INTO telemetry "
            "(device_id,temp_c,hum_pct,pres_hpa,rssi,alarm_level,free_heap,ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (probe, 25.0, 50.0, 1000.0, -50, 0, 80000, ts),
        )
        batch.append(ts)

    before = db.query("SELECT ts FROM telemetry WHERE device_id=? ORDER BY ts", (probe,))
    print("造了 {} 条 cutoff 边界数据:".format(len(before)))
    for r in before:
        print("   ", r["ts"])
    print("cutoff 新格式(.000Z):", cut.strftime(FMT_NEW))
    print("cutoff 旧格式      : ", cut.strftime(FMT_OLD))

    # 关键：直接验证字符串比较（SQLite BINARY collation 行为）
    print("\n=== 字符串比较（决定删/留）===")
    cut_new, cut_old = cut.strftime(FMT_NEW), cut.strftime(FMT_OLD)
    for ts in batch:
        print("  {}  新:删={}  旧:删={}".format(ts, ts < cut_new, ts < cut_old))

    # 触发一轮真实清理（与每 60s 定时任务调用同一方法）
    a = aggmod.Aggregator()
    a._cleanup()

    after = db.query("SELECT ts FROM telemetry WHERE device_id=? ORDER BY ts", (probe,))
    kept = len(after)
    print("\n_cleanup 后剩余: {} / {}".format(kept, len(before)))
    ok = kept == len(before)
    print("  [PASS] 修复生效：cutoff 边界数据全部保留，未误删" if ok
          else "  [FAIL] 仍误删 {} 条".format(len(before) - kept))

    # 清理探针数据
    db.execute("DELETE FROM telemetry WHERE device_id=?", (probe,))
    db.execute("DELETE FROM devices WHERE id=?", (probe,))
    print("  （探针数据已清理）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
