"""ICU 重症监护数据模型——患者 / 体征 / 医嘱 / 检验 / 备份。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple
import hashlib

_lock = threading.Lock()

# ---- 表操作辅助 ----
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DB_PATH = os.environ.get("DB_PATH", "data/envmon.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))
_conn_global: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn_global
    if _conn_global is None:
        _conn_global = sqlite3.connect(DB_PATH)
        _conn_global.row_factory = sqlite3.Row
        _conn_global.execute("PRAGMA journal_mode=WAL")
    return _conn_global


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    with _lock:
        c = _get_conn()
        try:
            yield c
        finally:
            c.commit()


def run(sql: str, params: Optional[Tuple] = None):
    with _conn() as c:
        cur = c.execute(sql, params or ())
        c.commit()
        return cur.lastrowid


def fetchone(sql: str, params: Optional[Tuple] = None):
    with _conn() as c:
        r = c.execute(sql, params or ()).fetchone()
        return dict(r) if r else None


def fetchall(sql: str, params: Optional[Tuple] = None):
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params or ())]


# ---------- 患者 ----------
def patient_create(pid: str, name: str = None, gender: str = None,
                   age: int = None, bed_no: str = None, admit_ts: str = None,
                   diagnosis: str = None, doctor: str = None,
                   phone: str = None) -> int:
    now = _now()
    admit = admit_ts or now
    return run(
        "INSERT INTO patients (pid,name,gender,age,bed_no,admit_ts,diagnosis,doctor,phone,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, gender, age, bed_no, admit, diagnosis, doctor, phone, now, now),
    )


def patient_by_pid(pid: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM patients WHERE pid=?", (pid,))


def patient_by_id(pid: int) -> Optional[Dict]:
    return fetchone("SELECT * FROM patients WHERE id=?", (pid,))


def list_patients(limit: int = 200) -> List[Dict]:
    return fetchall("SELECT * FROM patients ORDER BY created_at DESC LIMIT ?", (limit,))


def patient_update(patient_id: int, **fields) -> bool:
    allowed = {"name", "gender", "age", "bed_no", "diagnosis", "doctor", "phone"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    set_clauses = ", ".join(f"{k}=?" for k in fields.keys()) + ", updated_at=?"
    vals = [v for v in fields.values()] + [_now(), patient_id]
    run(f"UPDATE patients SET {set_clauses} WHERE id=?", tuple(vals))
    return True


def patient_delete(patient_id: int):
    # 级联删除关联设备/体征/医嘱/检验
    run("DELETE FROM patient_devices WHERE patient_id=?", (patient_id,))
    run("DELETE FROM vitals WHERE patient_id=?", (patient_id,))
    run("DELETE FROM orders WHERE patient_id=?", (patient_id,))
    run("DELETE FROM lab_results WHERE patient_id=?", (patient_id,))
    run("DELETE FROM patients WHERE id=?", (patient_id,))


# ---------- 患者-设备关联 ----------
def link_device(patient_id: int, device_id: str, role: str = "primary"):
    run("INSERT OR REPLACE INTO patient_devices (patient_id,device_id,role,linked_at) "
        "VALUES (?,?,?,?)", (patient_id, device_id, role, _now()))


def unlink_device(patient_id: int, device_id: str) -> bool:
    cur = run("DELETE FROM patient_devices WHERE patient_id=? AND device_id=?", (patient_id, device_id))
    return cur > 0


def devices_for_patient(patient_id: int) -> List[Dict]:
    return fetchall(
        "SELECT pd.*, d.name AS device_name, d.fw_version, d.ip_addr, d.online, d.last_seen "
        "FROM patient_devices pd LEFT JOIN devices d ON d.id=pd.device_id "
        "WHERE pd.patient_id=? ORDER BY pd.role DESC",
        (patient_id,),
    )


# ---------- 生命体征 ----------
VITAL_FIELDS = [
    "sp_o2", "pr_hr", "ecg_hr", "ecg_st", "rr_bpm", "etco2",
    "sbp", "dbp", "map_bp", "ibp", "temp_c", "glucose",
    "hum_pct", "pres_hpa",
    "k_mmol", "na_mmol", "cl_mmol", "ca_mmol", "glucose_lab", "lactate",
    "ph", "pco2", "po2", "hco3", "be",
]

def insert_vital(patient_id: int, ts: str, source: str, source_device: str = None,
                 alarm_flag: int = 0, alarm_reason: str = None,
                 extra: str = None, **values):
    cols = ["patient_id", "ts", "source", "created_at"]
    placeholders = ["?", "?", "?", "?"]
    bind = [patient_id, ts, source, _now()]
    if source_device:
        cols.append("source_device")
        placeholders.append("?")
        bind.append(source_device)
    for k in VITAL_FIELDS:
        if k in values and values[k] is not None:
            cols.append(k)
            placeholders.append("?")
            bind.append(values[k])
    if alarm_flag:
        cols.append("alarm_flag")
        placeholders.append("?")
        bind.append(alarm_flag)
    if alarm_reason:
        cols.append("alarm_reason")
        placeholders.append("?")
        bind.append(alarm_reason)
    if extra:
        cols.append("extra")
        placeholders.append("?")
        bind.append(extra)
    col_str = ", ".join(cols)
    qmarks = ", ".join(placeholders)
    run(f"INSERT INTO vitals ({col_str}) VALUES ({qmarks})", tuple(bind))


def patient_vitals(patient_id: int, start: str, end: str,
                   fields: Optional[List[str]] = None) -> List[Dict]:
    if not fields:
        fields = VITAL_FIELDS
    cols = ", ".join(["ts"] + [f for f in fields if f in VITAL_FIELDS])
    sql = (f"SELECT ts, {cols}, source, alarm_flag "
           f"FROM vitals WHERE patient_id=? AND ts>=? AND ts<=? ORDER BY ts ASC")
    return fetchall(sql, (patient_id, start, end))


def vitals_recent(patient_id: int, hours: int = 24,
                  fields: Optional[List[str]] = None) -> List[Dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    return patient_vitals(
        patient_id,
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fields,
    )


# ---------- 医嘱 ----------
def order_insert(patient_id: int, source: str = "his",
                 order_no: str = None, drug_name: str = None,
                 dosage: str = None, route: str = None,
                 start_ts: str = None, end_ts: str = None,
                 rate_mlph: float = None, status: str = "active",
                 operator: str = None) -> int:
    start = start_ts or _now()
    return run(
        "INSERT INTO orders (patient_id,source,order_no,drug_name,dosage,route,start_ts,end_ts,rate_mlph,status,operator,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (patient_id, source, order_no, drug_name, dosage, route,
         start, end_ts, rate_mlph, status, operator, _now()),
    )


def orders_for_patient(patient_id: int, start: str = None, end: str = None) -> List[Dict]:
    if start and end:
        return fetchall(
            "SELECT * FROM orders WHERE patient_id=? AND start_ts>=? AND (end_ts IS NULL OR end_ts<=?) "
            "ORDER BY start_ts DESC",
            (patient_id, start, end),
        )
    return fetchall("SELECT * FROM orders WHERE patient_id=? ORDER BY start_ts DESC", (patient_id,))


def order_stop(order_id: int) -> bool:
    r = run("UPDATE orders SET status='stopped', end_ts=? WHERE id=? AND status='active'",
            (_now(), order_id))
    return r > 0


# ---------- LIS 检验 ----------
def lab_result_insert(patient_id: int, source: str = "lis",
                      item_code: str = None, item_name: str = None,
                      value: float = None, unit: str = None,
                      ref_min: float = None, ref_max: float = None,
                      result_ts: str = None, critical: int = 0) -> int:
    ts = result_ts or _now()
    # 自动标 critical
    if ref_min is not None and ref_max is not None and value is not None:
        if value < ref_min or value > ref_max:
            critical = 1
    return run(
        "INSERT INTO lab_results (patient_id,source,item_code,item_name,value,unit,ref_min,ref_max,result_ts,critical,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (patient_id, source, item_code, item_name, value, unit,
         ref_min, ref_max, ts, critical, _now()),
    )


def lab_results_for_patient(patient_id: int, result_ts_start: str = None,
                            result_ts_end: str = None) -> List[Dict]:
    if result_ts_start and result_ts_end:
        return fetchall(
            "SELECT * FROM lab_results WHERE patient_id=? AND result_ts>=? AND result_ts<=? "
            "ORDER BY result_ts DESC",
            (patient_id, result_ts_start, result_ts_end),
        )
    return fetchall(
        "SELECT * FROM lab_results WHERE patient_id=? ORDER BY result_ts DESC",
        (patient_id,),
    )


# ---------- 备份 ----------
BACKUP_DIR = os.environ.get("BACKUP_DIR", "data/backups")

def do_backup() -> Dict:
    now = _now()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(BACKUP_DIR, f"envmon-{timestamp}.db")
    shutil.copy2(DB_PATH, path)
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    run("INSERT INTO backup_log (path,size_bytes,sha256,created_at) VALUES (?,?,?,?)",
        (path, size, sha, now))
    # 清理 3 天前的旧备份
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    kept = 0
    for fn in os.listdir(BACKUP_DIR):
        fp = os.path.join(BACKUP_DIR, fn)
        if not os.path.isfile(fp):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc)
        if mtime < cutoff:
            os.remove(fp)
        else:
            kept += 1
    return {"path": path, "size": size, "sha256": sha, "ts": now, "kept": kept}


def list_backups(limit: int = 20) -> List[Dict]:
    return fetchall(
        "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )