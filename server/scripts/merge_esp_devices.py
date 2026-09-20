#!/usr/bin/env python3
"""
merge_esp_devices.py — 清理「扫描局域网」产生的重复设备行
============================================================

背景
----
服务器 /api/devices/scan 扫描局域网时，若 ESP 设备的 Web JSON 里解析不出
device_id（典型：esp32-7oled 旧固件 /json 不带 dev/device_id），会把设备
兜底注册成 `esp-<ip>`；而同一台设备通过 MQTT/HTTP 遥测自动注册真实 id
（如 envmon8266-xxxx）。于是 devices 表里同一物理设备有两行，前端设备管理
里显示成两台设备。

本脚本把这类 `esp-<ip>` 行合并进同 IP 的真实 id 行，并保留其历史数据
（telemetry / telemetry_1m / thresholds / alarms / patient_devices /
messages / monitor_sessions / device_scan_snapshots 中引用的 device_id
一并改名），最后删除 esp- 行本身。

安全设计
--------
* 默认 dry-run：只打印将要做的合并，不动数据库。
* 必须加 --execute 才真正执行；执行前自动备份数据库到 server/backups/。
* 每个 esp 行必须能在 devices 表里按 ip_addr 找到唯一的非 esp- 行，
  找不到就跳过并报告（不删除、不修改）。
* 整批操作用一个事务包裹，任一步失败全部回滚。

用法
----
    python3 server/scripts/merge_esp_devices.py                    # 预览
    python3 server/scripts/merge_esp_devices.py --db /path/envmon.db --execute

注意
----
- 只匹配 id 以 "esp-" 开头且有 ip_addr 的行；如果设备真实 id 恰好是
  esp-xxx，请在 dry-run 输出里人工核对后再执行。
- 若 esp 行与真实行在同一唯一键上有冲突（例如同 (device_id,seq) 的
  遥测已存在），冲突行会被丢弃（保留真实 id 已有的那条），属预期行为。
"""
import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys

# 需迁移的关联表。脚本会动态检查表是否存在 / 是否有 device_id 列。
# mode 说明：
#   update_or_ignore : UPDATE OR IGNORE（命中唯一键冲突时跳过该行），残留行随 DELETE 丢弃
#   insert_or_ignore : INSERT OR IGNORE SELECT * ...（主键冲突时保留已存在行），再 DELETE esp 行
#   update           : 无唯一键约束，直接 UPDATE 再 DELETE
PLAN = [
    ("telemetry",             "update_or_ignore"),
    ("telemetry_1m",          "update_or_ignore"),
    ("thresholds",            "insert_or_ignore"),
    ("alarms",                "update"),
    ("patient_devices",       "insert_or_ignore"),
    ("messages",              "update"),
    ("monitor_sessions",      "update"),
    ("device_scan_snapshots", "insert_or_ignore"),
]


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return col in cols


def row_count(conn: sqlite3.Connection, table: str, device_id: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE device_id=?", (device_id,)
    ).fetchone()[0]


def find_pairs(conn: sqlite3.Connection):
    """返回 (pairs, unmatched)。pairs: (esp_id, ip, real_id)。"""
    # 老库可能没有 deleted 列（v2.9 迁移才加）——没有该列时全部视为未删除
    not_deleted = "COALESCE(deleted,0)=0" if has_col(conn, "devices", "deleted") else "1=1"
    esp_rows = conn.execute(
        f"SELECT id, ip_addr FROM devices "
        f"WHERE id LIKE 'esp-%' AND {not_deleted} "
        f"AND ip_addr IS NOT NULL AND TRIM(ip_addr)<>'' ORDER BY id"
    ).fetchall()

    pairs, unmatched = [], []
    for esp_id, ip in esp_rows:
        real = conn.execute(
            f"SELECT id FROM devices "
            f"WHERE ip_addr=? AND id NOT LIKE 'esp-%' AND {not_deleted} "
            f"ORDER BY COALESCE(last_seen,'') DESC, COALESCE(first_seen,'') ASC "
            f"LIMIT 1",
            (ip,),
        ).fetchone()
        if real:
            pairs.append((esp_id, ip, real[0]))
        else:
            unmatched.append((esp_id, ip))
    return pairs, unmatched


def migrate_table(conn: sqlite3.Connection, table: str, mode: str,
                  esp_id: str, real_id: str):
    """把表中 device_id=esp_id 的行迁移到 real_id。返回 (moved, dropped)。"""
    if mode == "update":
        cur = conn.execute(
            f"UPDATE {table} SET device_id=? WHERE device_id=?", (real_id, esp_id)
        )
        moved = cur.rowcount
        cur = conn.execute(f"DELETE FROM {table} WHERE device_id=?", (esp_id,))
        dropped = cur.rowcount
        return moved, dropped

    if mode == "update_or_ignore":
        before = row_count(conn, table, esp_id)
        conn.execute(
            f"UPDATE OR IGNORE {table} SET device_id=? WHERE device_id=?",
            (real_id, esp_id),
        )
        # OR IGNORE 跳过的行（唯一键冲突）仍留在 esp_id 名下，下一步 DELETE 时丢弃
        remaining = row_count(conn, table, esp_id)
        conn.execute(f"DELETE FROM {table} WHERE device_id=?", (esp_id,))
        return before - remaining, remaining

    if mode == "insert_or_ignore":
        before = row_count(conn, table, esp_id)
        real_before = row_count(conn, table, real_id)
        # 复制时把 device_id 列改写成 real_id——否则复制出的行仍叫 esp_id，
        # 与原行主键自冲突被 IGNORE，绑定数据就丢了。
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        col_list = ", ".join(f'\"{c}\"' for c in cols)
        sel_exprs, params = [], []
        for c in cols:
            if c == "device_id":
                sel_exprs.append("?")
                params.append(real_id)
            else:
                sel_exprs.append(f'\"{c}\"')
        params.append(esp_id)
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({col_list}) "
            f"SELECT {', '.join(sel_exprs)} FROM {table} WHERE device_id=?",
            params,
        )
        real_after = row_count(conn, table, real_id)
        moved = real_after - real_before
        conn.execute(f"DELETE FROM {table} WHERE device_id=?", (esp_id,))
        return moved, before - moved

    raise ValueError(f"unknown mode: {mode}")


def run(db_path: str, execute: bool) -> int:
    if not os.path.isfile(db_path):
        print(f"[ERR] database not found: {db_path}", file=sys.stderr)
        return 2

    if execute:
        backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"esp-merge-{now_stamp()}.db")
        shutil.copy2(db_path, backup_path)
        print(f"[BACKUP] {db_path} -> {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # 手动控制事务
    try:
        if not table_exists(conn, "devices"):
            print("[ERR] no devices table in database", file=sys.stderr)
            return 2

        plans = [(t, m) for t, m in PLAN
                 if table_exists(conn, t) and has_col(conn, t, "device_id")]
        pairs, unmatched = find_pairs(conn)

        print(f"== {db_path} ==  mode: "
              + ("DRY-RUN (no changes)" if not execute else "EXECUTE"))
        if not pairs:
            print("[OK] no esp-<ip> duplicate rows found — nothing to merge.")
            return 0
        print(f"found {len(pairs)} esp-<ip> row(s) to merge into real-id rows\n")

        if execute:
            conn.execute("BEGIN IMMEDIATE")

        for esp_id, ip, real_id in pairs:
            print(f"[{esp_id}] (ip={ip})  ->  {real_id}")
            for table, mode in plans:
                if execute:
                    moved, dropped = migrate_table(conn, table, mode, esp_id, real_id)
                    tag = f"moved={moved} dropped={dropped}"
                else:
                    n = row_count(conn, table, esp_id)
                    tag = f"rows={n}" if n else "rows=0"
                if tag != "rows=0":
                    print(f"    {table:<24} {tag}")
            if execute:
                cur = conn.execute("DELETE FROM devices WHERE id=?", (esp_id,))
                print(f"    {'devices':<24} deleted esp row (rowcount={cur.rowcount})")

        if unmatched:
            print("\n[SKIP] esp rows without any real-id row on same IP (kept as-is):")
            for esp_id, ip in unmatched:
                print(f"    {esp_id} @ {ip}")

        if execute:
            conn.execute("COMMIT")
            print("\n[OK] merge committed.")
        else:
            print("\n[DONE] dry-run preview only. Re-run with --execute to apply.")
            print("       --execute will back up the DB first, then merge in one transaction.")
        return 0

    except Exception as e:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        print(f"\n[ERR] merge failed, rolled back: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="merge esp-<ip> duplicate device rows")
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "envmon.db"),
        help="sqlite database path (default: server/envmon.db)")
    ap.add_argument("--execute", action="store_true",
                    help="actually perform the merge (default: dry-run preview)")
    args = ap.parse_args()
    return run(args.db, args.execute)


if __name__ == "__main__":
    sys.exit(main())