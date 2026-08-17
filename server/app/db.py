"""SQLite 数据访问层。

使用标准库 sqlite3 + 全局锁，跨线程安全（API 请求线程 + MQTT 后台线程）。
时间统一以 ISO8601 UTC 字符串存储，便于直接排序与展示。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("DB_PATH", "data/envmon.db")
SCHEMA_FILE = os.environ.get("SCHEMA_FILE", "schema.sql")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if os.path.exists(SCHEMA_FILE):
            with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
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


def query(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    with _lock:
        cur = get_conn().execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------- devices
def upsert_device(device_id: str, fw_version: str = None, ip_addr: str = None) -> None:
    now = utcnow()
    with _lock:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO devices (id, fw_version, ip_addr, first_seen, last_seen, online)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                last_seen = excluded.last_seen,
                online = 1,
                fw_version = COALESCE(excluded.fw_version, devices.fw_version),
                ip_addr = COALESCE(excluded.ip_addr, devices.ip_addr)
            """,
            (device_id, fw_version, ip_addr, now, now),
        )
        conn.commit()


def set_device_online(device_id: str, online: bool) -> None:
    execute("UPDATE devices SET online=? WHERE id=?", (1 if online else 0, device_id))


def list_devices() -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM devices ORDER BY id")
    return [dict(r) for r in rows]


def register_device(device_id: str, name: str = None) -> None:
    """手动注册设备（首次未上线时可预先创建）。"""
    execute("INSERT OR IGNORE INTO devices (id, name, first_seen, online) VALUES (?,?,DATETIME('now'),0)",
            (device_id, name))


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
    """删除设备及其全部关联数据。"""
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        conn.execute("DELETE FROM telemetry WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM telemetry_1m WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM alarms WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM thresholds WHERE device_id=?", (device_id,))
        conn.commit()


# ---------------------------------------------------------------- telemetry
def insert_telemetry(device_id: str, temp: float, hum: float, pres: float,
                     rssi: int, alarm_level: int, free_heap: int,
                     ts: str = None) -> None:
    execute(
        """
        INSERT OR IGNORE INTO telemetry
            (device_id, ts, temp_c, hum_pct, pres_hpa, rssi, alarm_level, free_heap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (device_id, ts or utcnow(), temp, hum, pres, rssi, alarm_level, free_heap),
    )


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
