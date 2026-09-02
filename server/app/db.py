"""SQLite 数据访问层。

使用标准库 sqlite3 + 全局锁，跨线程安全（API 请求线程 + MQTT 后台线程）。
时间统一以 ISO8601 UTC 字符串存储，便于直接排序与展示。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional

DB_PATH = os.environ.get("DB_PATH", "data/envmon.db")
SCHEMA_FILE = os.environ.get("SCHEMA_FILE", "schema.sql")

_log = logging.getLogger("envmon.db")

_lock = threading.Lock()


@contextmanager
def _locked_scope() -> Generator[sqlite3.Connection, None, None]:
    """临界区上下文：acquire _lock 并给出 db 的共享连接。
    调用方在此区间内完成"检查 + 写入"，保证原子可见，杜绝并发去重漏判。"""
    _lock.acquire()
    try:
        yield get_conn()
    finally:
        get_conn().commit()
        _lock.release()
_conn: Optional[sqlite3.Connection] = None


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def localnow() -> str:
    """本地时间戳,默认东八区(系统 TZ 若已设置则以其为准)。用于 UI 展示。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def utcnow_ms() -> str:
    """带毫秒的 UTC 时间戳。ESP 无法同步时钟，服务器用它补 ts 时能天然去重
    （旧 telemetry 表带 UNIQUE(device_id,ts) 约束，同秒多行会被丢弃；
    带毫秒后每行唯一，避免 INSERT OR IGNORE 静默丢行）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def minute_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db() -> None:
    """执行 schema.sql 并补齐全局默认阈值。"""
    with _lock:
        conn = get_conn()
        # ① 预迁移：在 schema.sql 之前调整旧表结构，让后续 CREATE TABLE 走新版定义。
        _pre_migrate(conn)
        if os.path.exists(SCHEMA_FILE):
            with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        # ② 后迁移：创建表后的列/索引修补。
        _post_migrate(conn)
        # 全局默认阈值
        conn.execute(
            """
            INSERT OR IGNORE INTO thresholds (
                device_id, temp_min, temp_max, hum_min, hum_max,
                pres_min, pres_max, report_interval, alarm_enabled, alarm_sound, updated_at
            ) VALUES ('*', 5, 40, 20, 90, 950, 1050, 10, 1, 1, ?)
            """,
            (utcnow(),),
        )
        conn.commit()


# ================================================================ 模式迁移
# schema.sql 使用 CREATE TABLE IF NOT EXISTS，旧表不会自动获得新增列。
# 每个 migration 用"列是否存在"做幂等判断，可重复执行。
def _has_col(conn, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _pre_migrate(conn: sqlite3.Connection) -> None:
    # 在 schema.sql 之前执行：若旧 telemetry 表缺 seq 列（且带旧 UNIQUE(device_id,ts) 约束），
    # 删除旧表，让 schema.sql 的 CREATE TABLE IF NOT EXISTS 按新版定义重建。
    # schema.sql 是幂等的（IF NOT EXISTS），所以首次运行会创建新表，迁移后再运行直接跳过。
    # 旧数据 telemetry 本就只有秒级时间戳、无法可靠重建 seq，丢弃是安全的（线上历史可弃）。
    if not _has_col(conn, "telemetry", "seq"):
        conn.execute("DROP TABLE IF EXISTS telemetry_old")
        try:
            conn.execute("DROP TABLE IF EXISTS telemetry")
            _log.info("migration v2.2: old telemetry dropped (will rebuild with seq)")
        except Exception as e:  # noqa: BLE001
            _log.warning("migration v2.2: could not drop telemetry: %s", e)


def _post_migrate(conn: sqlite3.Connection) -> None:
    # v2.3: 一次性清理 devices.last_seen 被污染的历史值。
    # 污染签名很明确：【多台设备共享同一个 last_seen 时间戳】。
    # 正常情况每台设备各自上报，last_seen 互不相同；只有被【同一次写入】污染过
    # 才会出现多行同值 —— 实测一次 MQTT 重连把 7 台设备的 last_seen 全刷成
    # 2026-08-30T22:33:46Z，其中 5 台从没有过任何遥测。
    # 这类值对在线判定毫无意义，只会制造假象：
    #   假时间偏早 → 在线设备显示离线；假时间偏新 → 死设备显示在线。
    # 处理方式：把这些行的 last_seen 清空为 NULL、online 归零，回退到
    # 「从未收到数据」这个如实状态；下一帧真实遥测到达时会自动恢复。
    # 只清「被多行共享」的时间戳，各设备自己的真实上报时间不受影响。
    # 统一截到【秒】再分组：历史数据里有带毫秒的时间戳（utcnow_ms），
    # 有不带毫秒的（设备上报 ts），归一化后同一时刻才能被识别出来。
    polluted = [r[0] for r in conn.execute(
        "SELECT SUBSTR(last_seen, 1, 19) FROM devices "
        "WHERE last_seen IS NOT NULL "
        "GROUP BY SUBSTR(last_seen, 1, 19) HAVING COUNT(*) > 1").fetchall()]
    if polluted:
        q = ",".join("?" for _ in polluted)
        n = conn.execute(
            f"UPDATE devices SET last_seen=NULL, online=0 "
            f"WHERE SUBSTR(last_seen, 1, 19) IN ({q})",
            tuple(polluted),
        ).rowcount
        _log.info("migration v2.3: cleared %d polluted device last_seen values (%s)",
                  n, ", ".join(polluted))

    # Issue 5: 监护记录表 — 患者在某设备上的监护时间段。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS monitor_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE, "
        "device_id TEXT, "
        "start_ts TEXT NOT NULL, "
        "end_ts TEXT, "
        "summary TEXT, "
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_monitor_sessions_patient ON monitor_sessions(patient_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_monitor_sessions_device ON monitor_sessions(device_id)"
    )
    # 设备-患者历史表：记录同设备历次分配给不同患者的时间线。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_patient_history ("
        "device_id TEXT NOT NULL, patient_id INTEGER NOT NULL, linked_at TEXT NOT NULL, "
        "PRIMARY KEY(device_id, patient_id, linked_at))"
    )
    # 首次建表时，把现有 patient_devices 与 vitals 历史回填进去。
    try:
        has_data = conn.execute(
            "SELECT COUNT(*) FROM device_patient_history"
        ).fetchone()[0]
    except Exception:
        has_data = 0
    if has_data == 0:
        try:
            for row in conn.execute(
                "SELECT pd.device_id, pd.patient_id, MIN(v.ts) AS ts "
                "FROM patient_devices pd LEFT JOIN vitals v ON v.patient_id=pd.patient_id "
                "GROUP BY pd.device_id, pd.patient_id"
            ).fetchall():
                tdev = conn.execute(
                    "SELECT last_seen FROM devices WHERE id=?", (row["device_id"],)
                ).fetchone()
                ts = row["ts"] or (tdev["last_seen"] if tdev else None)
                if ts is None:
                    continue
                conn.execute(
                    "INSERT INTO device_patient_history (device_id, patient_id, linked_at) VALUES (?,?,?)",
                    (row["device_id"], row["patient_id"], ts),
                )
        except Exception as e:
            _log.warning("device_patient_history backfill skipped: %s", e)

    # v2.4: 医生档案 + 文字消息记录。定义见 schema.sql；旧库重启时在此
    # 补建，IF NOT EXISTS 保证幂等，已存在则跳过。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS doctors ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "title TEXT DEFAULT NULL, "
        "department TEXT DEFAULT NULL, "
        "department_id TEXT DEFAULT NULL, "
        "phone TEXT DEFAULT NULL, "
        "note TEXT DEFAULT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doctors_name ON doctors(name)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "device_id TEXT NOT NULL, "
        "sender TEXT DEFAULT NULL, "
        "text TEXT NOT NULL, "
        "delivered INTEGER NOT NULL DEFAULT 0, "
        "delivered_at TEXT DEFAULT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_device ON messages(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")

    # v2.6: 提醒表。医生发起的语音/文字提醒，可同时走企微 webhook、推送患者设备屏幕 + 语音。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reminders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "patient_id TEXT NOT NULL, "
        "device_id TEXT, "
        "doctor_id INTEGER, "
        "doctor_name TEXT, "
        "text TEXT NOT NULL, "
        "type TEXT NOT NULL DEFAULT 'reminder', "
        "sent_to_device INTEGER NOT NULL DEFAULT 0, "
        "sent_to_wechat INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_patient ON reminders(patient_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_created ON reminders(created_at)")

    # v2.5: 设备接入快照表。记录每次 /api/devices/probe 主动扫描的结果，分内网/外网两栏。
    # 保留窗口内多次快照，窗口外自动清空——避免"一直有数据送到端口"造成历史积压。
    # access_type = 'lan' | 'wan' | 'unknown' 由 IP 是否私有地址推断。
    # 前端据此生成"新增/在线/离线"分组，和 devices 表对比，新增设备可批量保存入库。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_scan_snapshots ("
        "device_id TEXT NOT NULL, "
        "name TEXT, "
        "ip_addr TEXT, "
        "access_type TEXT NOT NULL DEFAULT 'unknown', "
        "online INTEGER NOT NULL DEFAULT 0, "
        "fw_version TEXT, "
        "first_seen TEXT, "
        "scanned_at TEXT NOT NULL, "
        "PRIMARY KEY (device_id, scanned_at)"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_scanned_at ON device_scan_snapshots(scanned_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_device ON device_scan_snapshots(device_id)")

    # 旧版主键是 (device_id, scanned_at)——每次 probe 都新增一行，导致前端重复显示。
    # 新版改为 device_id 唯一（每次覆盖），这里一次性重建表把旧结构换掉。
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(device_scan_snapshots)")]
        pk = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='device_scan_snapshots'").fetchone()
        old_def = "PRIMARY KEY (device_id, scanned_at)"
        new_def = "PRIMARY KEY (device_id)"
        def_str = conn.execute("SELECT sql FROM sqlite_master WHERE name='device_scan_snapshots'").fetchone()[0]
        if old_def in def_str:
            conn.execute("DROP TABLE IF EXISTS device_scan_snapshots_tmp")
            conn.execute("ALTER TABLE device_scan_snapshots RENAME TO device_scan_snapshots_tmp")
            conn.execute(
                "CREATE TABLE device_scan_snapshots ("
                "device_id TEXT NOT NULL, name TEXT, ip_addr TEXT, access_type TEXT NOT NULL DEFAULT 'unknown', "
                "online INTEGER NOT NULL DEFAULT 0, fw_version TEXT, first_seen TEXT, scanned_at TEXT NOT NULL, "
                "PRIMARY KEY (device_id))"
            )
            conn.execute("INSERT INTO device_scan_snapshots "
                         "SELECT device_id, name, ip_addr, access_type, online, fw_version, first_seen, MAX(scanned_at) "
                         "FROM device_scan_snapshots_tmp GROUP BY device_id")
            conn.execute("DROP TABLE IF EXISTS device_scan_snapshots_tmp")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_scanned_at ON device_scan_snapshots(scanned_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_device ON device_scan_snapshots(device_id)")
            conn.commit()
            _log.info("migration v2.5: rebuilt device_scan_snapshots pk (device_id)")
    except Exception as e:
        _log.warning("device_scan_snapshots pk migration skipped: %s", e)

    # v2.4.1: 设备补齐。devices 表应包含所有曾被引用过的设备 id（内网 wifi 接入的
    # ESP32、外网域名接入的设备、曾使用过但已脱网的设备）。若 device 被删或
    # 从未通过 register_device 显式创建，patient_devices / vitals.source_device /
    # device_patient_history 仍会引用它 → 页面上设备"消失"、监护记录找不到对应设备。
    # 此步骤把其它表出现但 devices 表没有的设备 id 自动 INSERT 补齐，避免设备凭空消失。
    try:
        missing = conn.execute("""
            SELECT DISTINCT d.device_id
            FROM (
              SELECT device_id FROM patient_devices
              UNION SELECT source_device AS device_id FROM vitals WHERE source_device IS NOT NULL
              UNION SELECT device_id FROM device_patient_history
            ) d
            LEFT JOIN devices dv ON dv.id = d.device_id
            WHERE dv.id IS NULL
        """).fetchall()
        for row in missing:
            did = row[0]
            earliest = conn.execute("""
                SELECT MIN(ts) FROM (
                    SELECT linked_at AS ts FROM patient_devices WHERE device_id=?
                    UNION SELECT ts FROM vitals WHERE source_device=?
                    UNION SELECT linked_at AS ts FROM device_patient_history WHERE device_id=?
                )""", (did, did, did)).fetchone()[0]
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO devices(id, first_seen, online) VALUES(?, ?, 0)",
                    (did, earliest),
                )
            except Exception as e:
                _log.warning("device backfill skipped %s: %s", did, e)
        _log.info("migration v2.4.1: backfilled %d device rows", len(missing))
    except Exception as e:
        _log.warning("device backfill skipped: %s", e)


def query(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    with _lock:
        cur = get_conn().execute(sql, params)
        return cur.fetchall()


def query_locked(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    """在已持有 _lock 的上下文中调用，直接执行（不重复加锁）。"""
    return get_conn().execute(sql, params).fetchall()


def query_one_locked(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchone()


def query_locked_bool(sql: str, params: tuple = ()) -> bool:
    return bool(get_conn().execute(sql, params).fetchone())


# ================================================================ ICU vitals 原子写入
# MQTT 消息快速连续到达时，跨线程必须"查询去重 + 插入"在同一临界区内完成，
# 否则两个线程会同时看到"无记录"而各插入一行。本函数在 _lock 下用 db 共享连接执行，
# 保证先入者提交后后入者才能读到，从而实现严格去重。
def vital_insert_v2(patient_id: int, ts: str, source: str, device_id: str,
                    extra: Optional[str], values: Dict[str, float], alarm_flag: int) -> None:
    # 用占位符动态拼接，避免重复列
    fields = ["patient_id", "ts", "source", "source_device", "extra", "created_at"]
    ph = ["?", "?", "?", "?", "?", "?"]
    bind = [patient_id, ts, source, device_id, extra, utcnow()]
    for k in ("sp_o2", "pr_hr", "ecg_hr", "ecg_st", "rr_bpm", "etco2",
              "sbp", "dbp", "map_bp", "ibp", "temp_c", "glucose"):
        if k in values:
            fields.append(k)
            ph.append("?")
            bind.append(float(values[k]))
    if alarm_flag:
        fields.append("alarm_flag")
        ph.append("?")
        bind.append(alarm_flag)
    with _locked_scope() as conn:
        if conn.execute(
            "SELECT 1 FROM vitals WHERE source_device=? AND extra=?",
            (device_id, extra),
        ).fetchone():
            return
        conn.execute(
            "INSERT INTO vitals (%s) VALUES (%s)" % (", ".join(fields), ", ".join(ph)),
            bind,
        )
        conn.commit()


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------- devices
def upsert_device(device_id: str, fw_version: Optional[str] = None, ip_addr: Optional[str] = None) -> None:
    """登记/更新设备的固件版本与 IP（仅元数据）。

    【重要】不触碰 last_seen，也不改变 online。
    last_seen 的唯一写入者是 handle_telemetry 里的 set_device_seen —— 它代表
    「最近一次真实遥测」时刻。若本函数刷新 last_seen，因 envmon/{id}/status 是
    保留消息，bridge 每次重连都会重新投递而反复调用本函数，把所有设备的
    last_seen 刷成重连时刻，新鲜度判断即被彻底污染（实测误判 5 台设备在线）。
    """
    with _lock:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO devices (id, fw_version, ip_addr, first_seen, online)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(id) DO UPDATE SET
                fw_version = COALESCE(excluded.fw_version, devices.fw_version),
                ip_addr    = COALESCE(excluded.ip_addr, devices.ip_addr)
            """,
            (device_id, fw_version, ip_addr, utcnow()),
        )
        conn.commit()


def ensure_device(device_id: str) -> None:
    """确保设备记录存在（设备先上线、数据后到的场景）。

    只 INSERT，冲突时【什么都不更新】——不碰 last_seen，也不改 online。
    这是 status 消息的正确做法：它只说明设备曾连接过，不代表它此刻有数据。
    """
    with _lock:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO devices (id, first_seen, online) VALUES (?, ?, 0)
            ON CONFLICT(id) DO NOTHING
            """,
            (device_id, utcnow()),
        )
        conn.commit()


def set_device_seen(device_id: str, ts: Optional[str] = None) -> None:
    """记录设备「最近一次真实上报」时刻 —— 一律以【服务器接收时刻】为准。

    忽略设备自带的 ts：ESP 时钟无法可靠同步（实测 8266-v3 设备时钟比服务器
    慢约 2 小时），若采信设备时间戳，在线判定会出现两类相反的错误——

      设备时钟偏慢 → last_seen 永久落后 → 活设备被误判离线；
      设备时钟偏快 → last_seen 领先当前时间 → 死设备被误判在线（永不超时）。

    ts 参数保留仅为向后兼容，不再使用。
    """
    execute("UPDATE devices SET last_seen=? WHERE id=?", (utcnow(), device_id))


def set_device_online(device_id: str, online: bool) -> None:
    execute("UPDATE devices SET online=? WHERE id=?", (1 if online else 0, device_id))


def list_devices() -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM devices ORDER BY id")
    return [dict(r) for r in rows]


def _is_lan_ip(ip: Optional[str]) -> bool:
    """判断 IP 是否属于内网/私有地址。None 或空字符串直接返回 False。"""
    if not ip or not str(ip).strip():
        return False
    ip = str(ip).strip()
    try:
        parts = [int(x) for x in ip.split(".")]
    except (ValueError, AttributeError):
        return False
    if len(parts) != 4:
        return False
    a, b = parts[0], parts[1]
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 127 or (a == 169 and b == 254):
        return True
    return False


def infer_access_type(ip: Optional[str]) -> str:
    """先按 ip_addr 判断；为空时用 device_id 兜底（很多 ESP32 会用内网 IP 当 device_id）。"""
    if _is_lan_ip(ip):
        return "lan"
    return "unknown"


def infer_access_type_with_id(ip: Optional[str], device_id: Optional[str]) -> str:
    """先按 ip_addr 判断；为空时用 device_id 兜底。返回 lan / wan / unknown。"""
    if _is_lan_ip(ip):
        return "lan"
    if ip and ip.strip():
        return "wan"
    if _is_lan_ip(device_id):
        return "lan"
    return "unknown"


def upsert_scan_snapshot(device_id: str, name: Optional[str] = None,
                        ip_addr: Optional[str] = None,
                        online: bool = False, fw_version: Optional[str] = None,
                        first_seen: Optional[str] = None) -> None:
    """写入一次设备接入快照。access_type 由 IP 推断（device_id 兜底）；
    每个 device_id 保留最新一条，旧记录自动被覆盖——避免同一设备多次快照造成前端重复。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    access = infer_access_type_with_id(ip_addr, device_id)
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO device_scan_snapshots "
            "(device_id, name, ip_addr, access_type, online, fw_version, first_seen, scanned_at) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "name=excluded.name, ip_addr=excluded.ip_addr, access_type=excluded.access_type, "
            "online=excluded.online, fw_version=excluded.fw_version, "
            "first_seen=COALESCE(excluded.first_seen, device_scan_snapshots.first_seen), "
            "scanned_at=excluded.scanned_at",
            (device_id, name, ip_addr, access, 1 if online else 0, fw_version, first_seen, now),
        )
        conn.commit()


def list_scan_snapshots(window_minutes: int = 5) -> List[Dict[str, Any]]:
    """读取窗口内的设备接入快照（每设备最新一条）。

    窗口语义：若某设备最近一次扫描距今超过 window_minutes，则视为"已过期"，
    直接从表中删除，从而让"超过时长后自动清空，开启下一阶段获取"生效。
    后续再次 probe 时该设备会重新写入。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 清理窗口外过期快照
    execute("DELETE FROM device_scan_snapshots WHERE scanned_at < ?", (cutoff,))
    rows = query(
        "SELECT * FROM device_scan_snapshots ORDER BY access_type, online DESC, device_id",
    )
    return [dict(r) for r in rows]


def clear_scan_snapshots() -> int:
    return execute("DELETE FROM device_scan_snapshots")


def register_device(device_id: str, name: Optional[str] = None, ip_addr: Optional[str] = None) -> None:
    """手动注册设备（首次未上线时可预先创建）。

    ip_addr 用于人工登记外网设备的接入地址（内网设备的 IP 由遥测上报自动填充）。
    """
    execute(
        "INSERT OR IGNORE INTO devices (id, name, ip_addr, first_seen, online) "
        "VALUES (?,?,?,DATETIME('now'),0)",
        (device_id, name, ip_addr),
    )


def update_device_fields(device_id: str, fields: dict) -> bool:
    """按「实际提供的字段」更新设备，字段值为 None 表示清空。

    fields 由 API 层传入（通常是 UpdateDeviceIn.model_dump(exclude_unset=True)），
    因此 {\"name\": None} 表示「把名称清空」，{} 表示「什么都没提供」。
    """
    cols, params = [], []
    if "name" in fields:
        cols.append("name = ?")
        params.append(fields["name"] or None)
    if "ip_addr" in fields:
        cols.append("ip_addr = ?")
        params.append(fields["ip_addr"] or None)
    if not cols:
        return False
    params.append(device_id)
    affected = execute(
        f"UPDATE devices SET {', '.join(cols)} WHERE id = ?", tuple(params)
    )
    return bool(affected)


def device_detail(device_id: str) -> Optional[Dict[str, Any]]:
    """返回单设备详情：设备信息 + 最新遥测 + 阈值。"""
    rows = query("SELECT * FROM devices WHERE id=?", (device_id,))
    if not rows:
        return None
    d = rows[0]
    latest = latest_telemetry(device_id)
    th = get_thresholds(device_id) or {}
    return {"device": dict(d), "latest": latest, "thresholds": th}


def rename_device(device_id: str, new_name: str) -> None:
    execute("UPDATE devices SET name=? WHERE id=?", (new_name, device_id))


def delete_device(device_id: str) -> None:
    """软删设备：仅从 devices 表移除，不删除 vitals / telemetry_1m / alarms /
    patient_devices / device_patient_history。

    监护记录按患者（vitals.patient_id）+ 来源设备（vitals.source_device）独立
    留存，即便设备删除也必须可查——这是核心诉求，硬删 telemetry/vitals 会违反
    此约束。删除患者-设备绑定行由调用方通过 unlink_device 显式处理，本函数
    不级联删 vitals。
    """
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        conn.commit()


# ---------------------------------------------------------------- telemetry
def insert_telemetry(device_id: str, temp: float, hum: float, pres: float,
                     rssi: int, alarm_level: int, free_heap: int,
                     ts: str = None, seq: int = None) -> None:
    # ts 由服务器补时带毫秒，保证与 UNIQUE(device_id,ts) 兼容、不丢行。
    effective_ts = ts or utcnow_ms()
    with _lock:
        conn = get_conn()
        if seq is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO telemetry
                    (device_id, ts, seq, temp_c, hum_pct, pres_hpa, rssi, alarm_level, free_heap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, effective_ts, seq, temp, hum, pres, rssi, alarm_level, free_heap),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO telemetry
                    (device_id, ts, temp_c, hum_pct, pres_hpa, rssi, alarm_level, free_heap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, effective_ts, temp, hum, pres, rssi, alarm_level, free_heap),
            )
        conn.commit()


def latest_telemetry(device_id: str) -> Optional[Dict[str, Any]]:
    rows = query(
        "SELECT * FROM telemetry WHERE device_id=? ORDER BY ts DESC LIMIT 1",
        (device_id,),
    )
    return dict(rows[0]) if rows else None


def history_range(device_id: str, start: str, end: str, limit: int = 5000) -> List[Dict[str, Any]]:
    rows = query(
        """
        SELECT * FROM telemetry
        WHERE device_id=? AND ts>=? AND ts<=?
        ORDER BY ts ASC LIMIT ?
        """,
        (device_id, start, end, limit),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- thresholds
def get_thresholds(device_id: str) -> Optional[Dict[str, Any]]:
    """设备级优先，回退全局 '*'。"""
    rows = query("SELECT * FROM thresholds WHERE device_id=?", (device_id,))
    if rows:
        return dict(rows[0])
    rows = query("SELECT * FROM thresholds WHERE device_id='*'")
    return dict(rows[0]) if rows else None


def save_thresholds(device_id: str, data: Dict[str, Any]) -> None:
    fields = ["temp_min", "temp_max", "hum_min", "hum_max", "pres_min", "pres_max",
              "report_interval", "alarm_enabled", "alarm_sound"]
    cur = query("SELECT * FROM thresholds WHERE device_id=?", (device_id,))
    if cur:
        sets = ", ".join(f"{f}=?" for f in fields)
        vals = [data[f] for f in fields]
        execute(f"UPDATE thresholds SET {sets}, updated_at=? WHERE device_id=?",
                tuple(vals) + (utcnow(), device_id))
    else:
        cols = ", ".join(["device_id"] + fields + ["updated_at"])
        ph = ", ".join(["?"] * (len(fields) + 2))
        execute(f"INSERT INTO thresholds ({cols}) VALUES ({ph})",
                tuple([device_id] + [data[f] for f in fields] + [utcnow()]))


# ---------------------------------------------------------------- alarms
def insert_alarm(device_id: str, level: int, reason: str,
                 temp: float, hum: float, pres: float) -> int:
    return execute(
        """
        INSERT INTO alarms (device_id, ts, level, reason, temp_c, hum_pct, pres_hpa, cleared_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (device_id, utcnow(), level, reason, temp, hum, pres),
    )


def clear_open_alarms(device_id: str) -> None:
    execute(
        "UPDATE alarms SET cleared_at=? WHERE device_id=? AND cleared_at IS NULL",
        (utcnow(), device_id),
    )


def list_alarms(device_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    if device_id:
        rows = query("SELECT * FROM alarms WHERE device_id=? ORDER BY ts DESC LIMIT ?",
                     (device_id, limit))
    else:
        rows = query("SELECT * FROM alarms ORDER BY ts DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def open_alarm_for(device_id: str) -> Optional[Dict[str, Any]]:
    rows = query(
        "SELECT * FROM alarms WHERE device_id=? AND cleared_at IS NULL ORDER BY ts DESC LIMIT 1",
        (device_id,),
    )
    return dict(rows[0]) if rows else None


# ================================================================ 用户与会话
def create_user(username: str, display_name: str, password_hash: str,
                salt: str, role: str = "admin") -> bool:
    """创建用户，返回是否成功（用户名冲突返回 False）。"""
    try:
        execute(
            "INSERT INTO users (username, display_name, password_hash, salt, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, display_name, password_hash, salt, role, utcnow()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user_by_name(username: str) -> Optional[Dict[str, Any]]:
    rows = query("SELECT * FROM users WHERE username=?", (username,))
    return dict(rows[0]) if rows else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    rows = query("SELECT * FROM users WHERE id=?", (user_id,))
    return dict(rows[0]) if rows else None


def touch_login(user_id: int) -> None:
    execute("UPDATE users SET last_login=? WHERE id=?", (utcnow(), user_id))


def set_user_sound(user_id: int, on: bool) -> None:
    execute("UPDATE users SET sound_alarm=? WHERE id=?", (1 if on else 0, user_id))


def update_password(user_id: int, password_hash: str, salt: str) -> None:
    execute("UPDATE users SET password_hash=?, salt=? WHERE id=?",
            (password_hash, salt, user_id))


def list_users() -> List[Dict[str, Any]]:
    rows = query(
        "SELECT id, username, display_name, role, sound_alarm, created_at, last_login "
        "FROM users ORDER BY id"
    )
    return [dict(r) for r in rows]


def delete_user(user_id: int) -> None:
    # 禁止删除最后一个管理员
    admins = query("SELECT id FROM users WHERE role='admin'")
    if len(admins) <= 1:
        target = get_user_by_id(user_id)
        if target and target["role"] == "admin":
            raise ValueError("cannot delete last admin")
    execute("DELETE FROM users WHERE id=?", (user_id,))
    execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ---------------------------------------------------------------- 会话
def create_session(token: str, user_id: int, ip_addr: str = None,
                   user_agent: str = None, ttl_hours: int = 24 * 7) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at, ip_addr, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, user_id, now.strftime("%Y-%m-%dT%H:%M:%SZ"), expires, ip_addr, user_agent),
    )


def get_session_user(token: str) -> Optional[Dict[str, Any]]:
    """校验 token 且未过期，返回用户信息（含 token 对应会话）."""
    rows = query(
        "SELECT s.token, s.expires_at, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,),
    )
    if not rows:
        return None
    sess = dict(rows[0])
    if sess["expires_at"] < utcnow():
        execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    return sess


def delete_session(token: str) -> None:
    execute("DELETE FROM sessions WHERE token=?", (token,))


def cleanup_expired_sessions() -> None:
    execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))


# ============================================================
# OTA 固件版本管理
# ============================================================

def ota_list() -> List[Dict[str, Any]]:
    """列出所有已上传固件版本（不含二进制，只含元数据）。"""
    rows = query(
        "SELECT id, version, size, sha256, uploaded, is_latest FROM ota_images ORDER BY uploaded DESC",
    )
    return [dict(r) for r in rows]


def ota_get_latest() -> Optional[Dict[str, Any]]:
    """获取当前标记为 latest 的固件版本元数据。"""
    rows = query("SELECT id, version, size, sha256, uploaded FROM ota_images WHERE is_latest=1 LIMIT 1")
    return dict(rows[0]) if rows else None


def ota_get_binary(image_id: int) -> Optional[bytes]:
    """按 id 取固件二进制。"""
    rows = query("SELECT binary FROM ota_images WHERE id=?", (image_id,))
    return rows[0]["binary"] if rows else None


def ota_upload(
    version: str,
    sha256: str,
    binary: bytes,
) -> int:
    """上传固件：写入表 + 切换 is_latest。返回新 image id。"""
    # 先取消其它 is_latest
    execute("UPDATE ota_images SET is_latest=0")
    now = utcnow()
    cur = get_conn().execute(
        "INSERT INTO ota_images (version, size, sha256, uploaded, is_latest, binary) VALUES (?,?,?,?,1,?)",
        (version, len(binary), sha256, now, binary),
    )
    oid = cur.lastrowid
    get_conn().commit()
    return oid


def ota_delete(image_id: int) -> bool:
    """删除指定固件版本。"""
    execute("DELETE FROM ota_images WHERE id=?", (image_id,))
    return True


# ---------------------------------------------------------------- doctors
def list_doctors() -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM doctors ORDER BY id")
    return [dict(r) for r in rows]


def add_doctor(name: str, title: Optional[str] = None, department: Optional[str] = None,
               department_id: Optional[str] = None, phone: Optional[str] = None,
               contact: Optional[str] = None, note: Optional[str] = None) -> int:
    """登记一名医生。phone 与 contact 是同一字段（联系电话/联系方式）的别名。"""
    now = utcnow()
    if contact is not None:
        phone = contact  # 兼容 contact 别名
    cur = execute(
        "INSERT INTO doctors(name,title,department,department_id,phone,note,created_at) VALUES(?,?,?,?,?,?,?)",
        (name, title, department, department_id, phone, note, now),
    )
    return int(cur)


def update_doctor(doctor_id: int, fields: dict) -> bool:
    """按字段更新医生档案。允许 contact 作为 phone 的别名。"""
    if not fields:
        return False
    if "contact" in fields:
        fields["phone"] = fields.pop("contact")
    allowed = ("title", "department", "department_id", "phone", "note", "name")
    cols, params = [], []
    for k, v in fields.items():
        if k in allowed:
            cols.append(f"{k} = ?")
            params.append(v or None)
    if not cols:
        return False
    params.append(doctor_id)
    return bool(execute(f"UPDATE doctors SET {', '.join(cols)} WHERE id = ?", tuple(params)))


def delete_doctor(doctor_id: int) -> bool:
    return bool(execute("DELETE FROM doctors WHERE id=?", (doctor_id,)))


def remind_patient(patient_id: str, device_id: Optional[str],
                   doctor_id: Optional[int], doctor_name: Optional[str],
                   text: str, sent_to_device: int = 0,
                   sent_to_wechat: int = 0, rtype: str = "reminder") -> int:
    now = utcnow()
    cur = execute(
        "INSERT INTO reminders(patient_id,device_id,doctor_id,doctor_name,text,type,sent_to_device,sent_to_wechat,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (patient_id, device_id, doctor_id, doctor_name, text, rtype, sent_to_device, sent_to_wechat, now),
    )
    return cur or 0


def remind_list(patient_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    if patient_id:
        rows = query("SELECT * FROM reminders WHERE patient_id=? ORDER BY id DESC LIMIT ?",
                     (patient_id, limit))
    else:
        rows = query("SELECT * FROM reminders ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- messages
def list_messages(device_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
    if device_id:
        rows = query("SELECT * FROM messages WHERE device_id=? ORDER BY id DESC LIMIT ?",
                     (device_id, limit))
    else:
        rows = query("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def add_message(device_id: str, text: str, sender: Optional[str] = None,
                delivered: int = 0, delivered_at: Optional[str] = None) -> int:
    now = utcnow()
    cur = execute(
        "INSERT INTO messages(device_id,sender,text,delivered,delivered_at,created_at) VALUES(?,?,?,?,?,?)",
        (device_id, sender, text, delivered, delivered_at, now),
    )
    return int(cur)


def mark_message_delivered(message_id: int) -> None:
    execute("UPDATE messages SET delivered=1, delivered_at=? WHERE id=?", (utcnow(), message_id))


def message_stat() -> dict:
    """消息统计：总数 / 已送达 / 未送达 / 按设备分布。"""
    rows = query(
        "SELECT "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN delivered=1 THEN 1 ELSE 0 END) AS delivered, "
        "SUM(CASE WHEN delivered=0 THEN 1 ELSE 0 END) AS pending "
        "FROM messages"
    )
    s = dict(rows[0])
    s["by_device"] = [
        {"device_id": r["device_id"], "n": r["n"]}
        for r in query(
            "SELECT device_id, COUNT(*) AS n FROM messages "
            "GROUP BY device_id ORDER BY n DESC, device_id"
        )
    ]
    return s


def message_clear() -> int:
    """清空历史消息记录，返回删除行数。"""
    cur = execute("DELETE FROM messages")
    return int(cur)


# ---------------------------------------------------------------- 路由别名
# main.py 的 doctors/messages 路由使用的函数名（doctor_list / doctor_by_id /
# message_list 等），在此统一入口；下面各自调用本文件的实际实现，避免两套命名漂移。
def doctor_list(limit: int = 200) -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM doctors ORDER BY id LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def doctor_create(name: str, title: Optional[str] = None,
                  department: Optional[str] = None, contact: Optional[str] = None,
                  note: Optional[str] = None) -> int:
    """登记医生。contact = 联系电话（别名）；department_id 未提供。"""
    now = utcnow()
    cur = execute(
        "INSERT INTO doctors(name,title,department,department_id,phone,note,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (name, title, department, None, contact, note, now),
    )
    return int(cur)


def doctor_by_id(doctor_id: int) -> Optional[Dict[str, Any]]:
    rows = query("SELECT * FROM doctors WHERE id=?", (doctor_id,))
    return dict(rows[0]) if rows else None


def doctor_update(doctor_id: int, **fields: Any) -> bool:
    return update_doctor(doctor_id, fields)


def doctor_delete(doctor_id: int) -> bool:
    return delete_doctor(doctor_id)


def message_list(device_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """按设备过滤取消息。device_id 为空字符串视为不筛选（路由默认传 ''）。"""
    return list_messages(device_id=device_id or None, limit=limit)


# ================================================================ AI 报警分析历史
_AI_ANALYSES_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS alarm_ai_analyses ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "device_id TEXT NOT NULL, "
    "patient_id TEXT, "
    "patient_name TEXT, "
    "doctor_id INTEGER, "
    "doctor_name TEXT, "
    "level INTEGER NOT NULL DEFAULT 1, "
    "reason TEXT, "
    "model TEXT, "
    "provider TEXT, "
    "prompt_len INTEGER, "
    "analysis TEXT, "
    "usage_text TEXT, "
    "weixin_sent INTEGER NOT NULL DEFAULT 0, "
    "weixin_err TEXT, "
    "created_at TEXT NOT NULL)"
)


def _ensure_alarm_ai_analyses() -> None:
    with _lock:
        conn = get_conn()
        has = any(r[0] == "alarm_ai_analyses"
                  for r in conn.execute(
                      "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        if not has:
            conn.execute(_AI_ANALYSES_TABLE_DDL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_analyses_device "
                "ON alarm_ai_analyses(device_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_analyses_patient "
                "ON alarm_ai_analyses(patient_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_analyses_created "
                "ON alarm_ai_analyses(created_at)")
            conn.commit()


def insert_ai_analysis(*, device_id: str, patient_id: Optional[str],
                       patient_name: Optional[str], doctor_id: Optional[int],
                       doctor_name: Optional[str], level: int,
                       reason: str, model: str, provider: str,
                       prompt_len: int, analysis: str, usage_text: Optional[str],
                       weixin_sent: int, weixin_err: Optional[str]) -> int:
    _ensure_alarm_ai_analyses()
    return int(execute(
        "INSERT INTO alarm_ai_analyses("
        "device_id, patient_id, patient_name, doctor_id, doctor_name, level, "
        "reason, model, provider, prompt_len, analysis, usage_text, "
        "weixin_sent, weixin_err, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (device_id, patient_id, patient_name, doctor_id, doctor_name, level,
         reason, model, provider, prompt_len, analysis, usage_text,
         weixin_sent, weixin_err, utcnow()),
    ))


def list_ai_analyses(device_id: Optional[str] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
    _ensure_alarm_ai_analyses()
    if device_id:
        rows = query(
            "SELECT * FROM alarm_ai_analyses WHERE device_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (device_id, limit))
    else:
        rows = query(
            "SELECT * FROM alarm_ai_analyses ORDER BY created_at DESC LIMIT ?",
            (limit,))
    return [dict(r) for r in rows]
