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
import json

_lock = threading.Lock()

# ---- 表操作辅助 ----
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DB_PATH = os.environ.get("DB_PATH", "data/envmon.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))





def _get_conn() -> sqlite3.Connection:
    """线程局部连接（FastAPI 线程池要求每线程独立连接）。"""
    import threading
    local = getattr(_get_conn, "_local", None)
    if local is None:
        local = _get_conn._local = threading.local()
    if getattr(local, "conn", None) is None:
        local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        local.conn.row_factory = sqlite3.Row
        local.conn.execute("PRAGMA journal_mode=WAL")
        local.conn.execute("PRAGMA busy_timeout=3000")
    return local.conn


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
                   phone: str = None, wechat_userid: str = None) -> int:
    now = _now()
    admit = admit_ts or now
    return run(
        "INSERT INTO patients (pid,name,gender,age,bed_no,admit_ts,diagnosis,doctor,phone,wechat_userid,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, gender, age, bed_no, admit, diagnosis, doctor, phone,
         wechat_userid, now, now),
    )


def patient_by_pid(pid: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM patients WHERE pid=?", (pid,))


def patient_by_id(pid: int) -> Optional[Dict]:
    return fetchone("SELECT * FROM patients WHERE id=?", (pid,))


def list_patients(limit: int = 200) -> List[Dict]:
    return fetchall("SELECT * FROM patients ORDER BY created_at DESC LIMIT ?", (limit,))


def patient_update(patient_id: int, **fields) -> bool:
    allowed = {"name", "gender", "age", "bed_no", "diagnosis", "doctor",
               "phone", "wechat_userid"}
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
def device_current_binding(device_id: str) -> Optional[Dict]:
    """设备当前绑定（patient_devices 现存）。返回患者信息或 None。"""
    try:
        rows = fetchall(
            "SELECT pd.patient_id, p.pid, p.name, p.bed_no, pd.role "
            "FROM patient_devices pd JOIN patients p ON p.id=pd.patient_id "
            "WHERE pd.device_id=?", (device_id,),
        )
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    return {"patient_id": r["patient_id"], "pid": r["pid"], "name": r["name"],
            "bed_no": r.get("bed_no"), "role": r.get("role")}


def link_device(patient_id: int, device_id: str, role: str = "primary"):
    """设备同时段只绑一患者。设备已被其他患者绑定时抛出 ValueError。"""
    cur = device_current_binding(device_id)
    if cur and cur["patient_id"] != patient_id:
        who = cur.get("name") or cur.get("pid") or ("#" + str(cur["patient_id"]))
        raise ValueError("设备 " + device_id + " 已被患者 " + who + " 绑定，请先解绑")
    if cur and cur["patient_id"] == patient_id:
        _archive_history(device_id, patient_id)
    _archive_history(device_id, patient_id)
    run("INSERT INTO patient_devices (patient_id,device_id,role,linked_at) "
        "VALUES (?,?,?,?)", (patient_id, device_id, role, _now()))


def unlink_device(patient_id: int, device_id: str) -> bool:
    """解绑设备。归档本次分配保留时间线；监护记录按 patient_id 留存，解绑不影响历史查询。"""
    _archive_history(device_id, patient_id)
    cur = run("DELETE FROM patient_devices WHERE patient_id=? AND device_id=?",
              (patient_id, device_id))
    return cur > 0


def _archive_history(device_id: str, patient_id: int) -> None:
    """设备分配变更时，把当前 patient_devices 记录归档到 device_patient_history。"""
    try:
        old = _get_conn().execute(
            "SELECT patient_id FROM patient_devices WHERE device_id=? AND patient_id=?",
            (device_id, patient_id)).fetchone()
    except Exception:
        return
    if old:
        run("INSERT INTO device_patient_history (device_id, patient_id, linked_at) "
            "VALUES (?,?,?)", (device_id, patient_id, _now()))


def device_patient_history(device_id: str) -> List[Dict]:
    """某设备的历次患者分配时间线。优先取 vitals(source_device) 的真实历史（确定），
    再补 device_patient_history 归档表（同设备换患者时的显式记录）。"""
    import datetime as _dt  # noqa: F401
    # 来源 1：vitals 表按 source_device 反查该设备实际服务过的患者及最早时间
    rows = fetchall(
        "SELECT patient_id, MIN(ts) AS linked_at "
        "FROM vitals WHERE source_device=? AND patient_id IS NOT NULL "
        "GROUP BY patient_id ORDER BY linked_at ASC",
        (device_id,),
    )
    seen = set()
    out = []
    for r in rows:
        seen.add(r["patient_id"])
        out.append(_enrich_patient(r["patient_id"], r["linked_at"]))
    # 来源 2：归档表里的显式分配（可能该患者仅被关联、暂无 vitals）
    try:
        extra = fetchall(
            "SELECT ph.patient_id, ph.linked_at FROM device_patient_history ph "
            "WHERE ph.device_id=? ORDER BY ph.linked_at ASC",
            (device_id,),
        )
    except Exception:
        extra = []
    for r in extra:
        if r["patient_id"] in seen:
            continue
        seen.add(r["patient_id"])
        out.append(_enrich_patient(r["patient_id"], r["linked_at"]))
    # 追加当前仍在 patient_devices 的分配
    cur = None
    try:
        cur = _get_conn().execute(
            "SELECT patient_id FROM patient_devices WHERE device_id=?", (device_id,)
        ).fetchone()
    except Exception:
        cur = None
    if cur and cur["patient_id"] not in seen:
        out.append(_enrich_patient(cur["patient_id"], _now()))
    return out


def _enrich_patient(patient_id: int, linked_at: str) -> Dict:
    p = None
    try:
        p = _get_conn().execute(
            "SELECT pid,name,bed_no FROM patients WHERE id=?", (patient_id,)
        ).fetchone()
    except Exception:
        p = None
    return {"patient_id": patient_id, "pid": p["pid"] if p else None,
            "name": p["name"] if p else None, "bed_no": p["bed_no"] if p else None,
            "linked_at": linked_at}


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


# ---------- 出入量 ----------
def add_io_log(patient_id: int, direction: str, kind: str, amount_ml: float,
               amount_g: Optional[float], sub_type: Optional[str], route: Optional[str],
               note: Optional[str], source: str, operator: Optional[str],
               ts: Optional[str], unique_id: Optional[str]) -> int:
    row = fetchone("SELECT id FROM patients WHERE id=?", (patient_id,))
    if not row:
        raise ValueError("patient not found")
    now = _now()
    q = ("INSERT INTO io_log (patient_id,direction,kind,sub_type,amount_ml,amount_g,route,"
         "note,source,operator,ts,unique_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
    return run(q, (patient_id, direction, kind, sub_type, amount_ml or 0, amount_g or 0,
                    route, note, source, operator, ts or now, unique_id, now))


def list_io_log(patient_id: int, hours: int = 72) -> List[Dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
    return fetchall(
        "SELECT * FROM io_log WHERE patient_id=? AND ts>? ORDER BY ts ASC",
        (patient_id, cutoff),
    )


def io_balance(patient_id: int, hours: int = 24) -> Dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetchall(
        "SELECT direction, COALESCE(SUM(amount_ml),0) AS ml, COALESCE(SUM(amount_g),0) AS g "
        "FROM io_log WHERE patient_id=? AND ts>? GROUP BY direction",
        (patient_id, cutoff),
    )
    inc = sum(r["ml"] for r in rows if r["direction"] == "in") + sum(r["g"] for r in rows if r["direction"] == "in")
    out = sum(r["ml"] for r in rows if r["direction"] == "out") + sum(r["g"] for r in rows if r["direction"] == "out")
    # 估算体重 (默认 70kg) 用于每小时尿量下限
    return {"in_ml": round(inc, 1), "out_ml": round(out, 1),
            "net_ml": round(inc - out, 1), "hours": hours}


# ---------- 监护记录 ----------

def start_monitor_session(patient_id: int, device_id: str = None) -> Dict:
    """为患者开启一段监护记录。若该患者已有未结束的监护，先自动结束旧的。"""
    now = _now()
    # 自动结束该患者之前的未结束监护
    open_sess = fetchone(
        "SELECT id FROM monitor_sessions WHERE patient_id=? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1",
        (patient_id,),
    )
    if open_sess:
        run("UPDATE monitor_sessions SET end_ts=? WHERE id=?", (now, open_sess["id"]))
    sid = run(
        "INSERT INTO monitor_sessions (patient_id, device_id, start_ts, created_at) VALUES (?,?,?,?)",
        (patient_id, device_id, now, now),
    )
    return {"session_id": sid, "patient_id": patient_id, "device_id": device_id, "start_ts": now}


def end_monitor_session(session_id: int, summary: str = None) -> Dict:
    """结束一段监护记录。"""
    now = _now()
    run("UPDATE monitor_sessions SET end_ts=?, summary=? WHERE id=? AND end_ts IS NULL",
        (now, summary, session_id))
    return {"session_id": session_id, "end_ts": now, "summary": summary}


def list_monitor_sessions(patient_id: int = None, device_id: str = None,
                          start: str = None, end: str = None,
                          limit: int = 200) -> List[Dict]:
    """查询监护记录列表，可按患者/设备/日期范围过滤。"""
    sql = ("SELECT ms.*, p.pid, p.name, p.bed_no "
           "FROM monitor_sessions ms "
           "LEFT JOIN patients p ON p.id=ms.patient_id WHERE 1=1")
    params: list = []
    if patient_id is not None:
        sql += " AND ms.patient_id=?"
        params.append(patient_id)
    if device_id:
        sql += " AND ms.device_id=?"
        params.append(device_id)
    if start:
        sql += " AND ms.start_ts>=?"
        params.append(start)
    if end:
        sql += " AND ms.start_ts<=?"
        params.append(end)
    sql += " ORDER BY ms.start_ts DESC LIMIT ?"
    params.append(limit)
    return fetchall(sql, tuple(params))


def get_monitor_session(session_id: int) -> Optional[Dict]:
    """获取单条监护记录详情。"""
    return fetchone(
        "SELECT ms.*, p.pid, p.name, p.bed_no, p.diagnosis "
        "FROM monitor_sessions ms "
        "LEFT JOIN patients p ON p.id=ms.patient_id WHERE ms.id=?",
        (session_id,),
    )


def active_session_for_device(device_id: str) -> Optional[Dict]:
    """查询某设备当前活跃的监护记录（用于切换设备时同步患者）。"""
    return fetchone(
        "SELECT ms.*, p.pid, p.name, p.bed_no "
        "FROM monitor_sessions ms "
        "LEFT JOIN patients p ON p.id=ms.patient_id "
        "WHERE ms.device_id=? AND ms.end_ts IS NULL "
        "ORDER BY ms.start_ts DESC LIMIT 1",
        (device_id,),
    )


# ---------- AI 评估 ----------
def _trend_arrow(vals: List[float]) -> str:
    """根据序列斜率返回 ↗ / ➡ / ↘ —— 用最近 3 点 vs 更早 3 点加权比较"""
    if len(vals) < 3:
        return "➡"
    # 用最近 3 点均值 vs 前 3 点均值（如果够长），或末点 vs 首点
    recent = vals[-1] * 0.5 + vals[-2] * 0.3 + vals[-3] * 0.2
    if len(vals) >= 6:
        earlier = vals[-4] * 0.5 + vals[-5] * 0.3 + vals[-6] * 0.2
    else:
        earlier = vals[0]
    # 用首末点的总差作为参考
    total_delta = (recent - vals[0]) / max(abs(vals[0]), 1.0)
    window_delta = (recent - earlier) / max(abs(earlier), 1.0)
    # 只要整体有 ≥5% 变化就判趋势，不只看 window
    if total_delta > 0.05 or window_delta > 0.05:
        return "↗"
    if total_delta < -0.05 or window_delta < -0.05:
        return "↘"
    return "➡"


def _in_band(v, lo, hi) -> int:
    """0 ok, 1 warn, 2 crit. NaN returns 0."""
    if v is None or v != v: return 0  # NaN check
    r = (hi - lo) / 2
    if lo <= v <= hi: return 0
    if lo - r <= v <= hi + r: return 1
    return 2


def assess_patient(patient_id: int, hours: int = 24) -> Dict:
    from .icu import _get_conn
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    patient = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not patient:
        raise ValueError("patient not found")
    pid = patient["pid"]

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 最近 24h vitals
    vitals = conn.execute(
        "SELECT * FROM vitals WHERE patient_id=? AND ts>? ORDER BY ts ASC",
        (patient_id, cutoff)
    ).fetchall()
    # 当前运行中的医嘱
    orders = conn.execute(
        "SELECT * FROM orders WHERE patient_id=? AND status='active' AND start_ts>? ORDER BY start_ts DESC",
        (patient_id, cutoff)
    ).fetchall()
    # 最近检验
    labs = conn.execute(
        "SELECT * FROM lab_results WHERE patient_id=? AND result_ts>? ORDER BY result_ts DESC",
        (patient_id, cutoff)
    ).fetchall()
    # IO 平衡
    io_bal = io_balance(patient_id, hours)

    # ---- 构建各系统状态 ----
    def _get(param: str) -> List[float]:
        return [v[param] for v in vitals if v[param] not in (None,)]

    systems = {}
    # 循环
    ecg_hr = _get("ecg_hr")
    ecg_val = ecg_hr[-1] if ecg_hr else None
    ecg_b = _in_band(ecg_val, 60, 100)
    systems["cardiac"] = {
        "label": "循环系统",
        "hr": ecg_val,
        "pr_hr": (_get("pr_hr")[-1] if _get("pr_hr") else None),
        "sbp": (_get("sbp")[-1] if _get("sbp") else None),
        "dbp": (_get("dbp")[-1] if _get("dbp") else None),
        "trend": _trend_arrow(ecg_hr),
        "risk": ecg_b,
        "note": None,
    }
    if ecg_val is not None and (ecg_val > 130 or ecg_val < 50):
        systems["cardiac"]["note"] = "心率异常，建议心电图复查并评估心律失常"
    elif ecg_val is not None and ecg_val > 100:
        systems["cardiac"]["note"] = "心动过速，关注心衰/容量/疼痛"

    # 呼吸
    rr = _get("rr_bpm")
    rr_val = rr[-1] if rr else None
    systems["respiratory"] = {
        "label": "呼吸系统",
        "rr": rr_val,
        "sp_o2": (_get("sp_o2")[-1] if _get("sp_o2") else None),
        "trend": _trend_arrow(rr),
        "risk": _in_band(rr_val, 12, 25),
        "note": None,
    }
    sp = systems["respiratory"]["sp_o2"]
    if sp is not None and sp < 90:
        systems["respiratory"]["risk"] = max(systems["respiratory"]["risk"], 2)
        systems["respiratory"]["note"] = "血氧 <90%，高流量吸氧，必要时无创/有创通气"
    elif sp is not None and sp < 94:
        systems["respiratory"]["note"] = "血氧偏低，调整吸氧流量"

    # 神经
    systems["neuro"] = {
        "label": "神经系统",
        "gcs": None, "trend": "➡", "risk": 0,
        "note": "GCS 未接入，请人工评估",
    }

    # 内分泌 / 血糖
    glu = _get("glucose")
    glu_val = glu[-1] if glu else None
    systems["endo"] = {
        "label": "内分泌",
        "glucose": glu_val,
        "trend": _trend_arrow(glu),
        "risk": _in_band(glu_val, 3.9, 6.1) if glu_val is not None else 0,
        "note": None,
    }
    if glu_val is not None:
        if glu_val < 3.0 or glu_val > 16.0:
            systems["endo"]["note"] = "血糖危急，胰岛素/葡萄糖处理"
        elif glu_val < 3.9 or glu_val > 10.0:
            systems["endo"]["note"] = "血糖偏离，关注感染/应激"

    # 肾功能
    kreatinine_rows = [l for l in labs if l["item_code"] in ("Cr","KREA","CREATININE")]
    systems["renal"] = {
        "label": "肾功能",
        "creatinine": kreatinine_rows[0]["value"] if kreatinine_rows else None,
        "urine_hr_ml": (io_bal["out_ml"] / hours) if hours > 0 else 0,
        "trend": "➡", "risk": 0, "note": None,
    }
    uhr = systems["renal"]["urine_hr_ml"]
    # 只有有出入量记录时尿量预警才有意义
    if hours >= 1 and io_bal["in_ml"] > 0 and uhr < 0.5 * 70:
        systems["renal"]["risk"] = 1
        systems["renal"]["note"] = "尿量偏少，关注容量/肾功能"
    if kreatinine_rows:
        v = kreatinine_rows[0]["value"]
        if v > 133:
            systems["renal"]["risk"] = 2
            systems["renal"]["note"] = "肌酐显著升高，评估 AKI"
        elif v > 88:
            systems["renal"]["risk"] = 1
            systems["renal"]["note"] = "肌酐偏高"

    # 凝血 / 血常规
    hgb_rows = [l for l in labs if l["item_code"] in ("HGB","Hb")]
    plt_rows = [l for l in labs if l["item_code"] in ("PLT")]
    systems["heme"] = {
        "label": "血液/凝血",
        "hgb": hgb_rows[0]["value"] if hgb_rows else None,
        "plt": plt_rows[0]["value"] if plt_rows else None,
        "trend": "➡", "risk": 0, "note": None,
    }
    if hgb_rows and hgb_rows[0]["value"] < 60:
        systems["heme"]["risk"] = 2
        systems["heme"]["note"] = "血红蛋白 <60g/L，评估输血"
    if plt_rows and plt_rows[0]["value"] < 50:
        systems["heme"]["risk"] = 2
        systems["heme"]["note"] = "血小板 <50×10⁹/L，出血风险"

    # 酸碱 / 血气
    ph_rows = [l for l in labs if l["item_code"] in ("pH",)]
    lact_rows = [l for l in labs if l["item_code"] in ("Lac","LACT")]
    systems["acid_base"] = {
        "label": "酸碱/代谢",
        "ph": ph_rows[0]["value"] if ph_rows else None,
        "lactate": lact_rows[0]["value"] if lact_rows else None,
        "trend": "➡", "risk": 0, "note": None,
    }
    if ph_rows:
        pv = ph_rows[0]["value"]
        if pv < 7.2 or pv > 7.55:
            systems["acid_base"]["risk"] = 2
            systems["acid_base"]["note"] = "pH 危急，酸碱失衡"
        elif pv < 7.35 or pv > 7.45:
            systems["acid_base"]["risk"] = 1
            systems["acid_base"]["note"] = "pH 偏离正常"
    if lact_rows and lact_rows[0]["value"] > 4.0:
        systems["acid_base"]["risk"] = 2
        systems["acid_base"]["note"] = "乳酸 >4，组织灌注不足，启动 sepsis 处理"

    # 出入量
    systems["fluid"] = {
        "label": "液体平衡",
        "in_ml": io_bal["in_ml"],
        "out_ml": io_bal["out_ml"],
        "net_ml": io_bal["net_ml"],
        "hours": hours,
        "trend": "↗" if io_bal["net_ml"] > 200 else ("↘" if io_bal["net_ml"] < -200 else "➡"),
        "risk": 0, "note": None,
    }
    if io_bal["net_ml"] > 300:
        systems["fluid"]["risk"] = 1
        systems["fluid"]["note"] = "正平衡显著，关注容量负荷/心衰/肺水肿"
    elif io_bal["net_ml"] < -300:
        systems["fluid"]["risk"] = 1
        systems["fluid"]["note"] = "负平衡，关注容量不足/休克"

    # ---- 总体风险等级 ----
    risk_map = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
    max_risk = max(s["risk"] for s in systems.values())
    crit_count = sum(1 for s in systems.values() if s["risk"] >= 2)
    warn_count = sum(1 for s in systems.values() if s["risk"] >= 1)
    if max_risk >= 2 and crit_count >= 2:
        overall = "critical"
    elif max_risk >= 2:
        overall = "high"
    elif warn_count >= 3:
        overall = "moderate"
    elif warn_count >= 1:
        overall = "moderate"
    else:
        overall = "low"

    # ---- 临床摘要（基于规则的自动生成） ----
    lines = []
    lines.append(f"{pid}（{patient['name']}，{patient['gender']}，{patient['age']}岁，床号 {patient['bed_no']}），"
                 f"诊断「{patient['diagnosis']}」，入院 {patient['admit_ts']}。")
    if systems["cardiac"]["hr"] is not None:
        bp_txt = ""
        if systems["cardiac"]["sbp"] is not None and systems["cardiac"]["dbp"] is not None:
            bp_txt = f"，血压 {systems['cardiac']['sbp']:.0f}/{systems['cardiac']['dbp']:.0f} mmHg"
        lines.append(f"循环：HR {systems['cardiac']['hr']:.0f} bpm{systems['cardiac']['trend']}{bp_txt}。")
    if systems["respiratory"]["rr"] is not None:
        spo_txt = ""
        if systems["respiratory"]["sp_o2"] is not None:
            spo_txt = f"，SpO2 {systems['respiratory']['sp_o2']:.0f}%"
        lines.append(f"呼吸：RR {systems['respiratory']['rr']:.0f} rpm{systems['respiratory']['trend']}{spo_txt}。")
    if systems["endo"]["glucose"] is not None:
        lines.append(f"血糖 {systems['endo']['glucose']:.1f} mmol/L{systems['endo']['trend']}。")
    if systems["renal"]["creatinine"] is not None:
        lines.append(f"肌酐 {systems['renal']['creatinine']:.1f} μmol/L，尿量 {systems['renal']['urine_hr_ml']:.1f} ml/h。")
    if systems["acid_base"]["ph"] is not None:
        lines.append(f"血气 pH {systems['acid_base']['ph']:.2f}"
                     + (f"，乳酸 {systems['acid_base']['lactate']:.1f}" if systems['acid_base']['lactate'] else ""))
    lines.append(f"出入量（{hours}h）：入 {io_bal['in_ml']:.0f}ml，出 {io_bal['out_ml']:.0f}ml，净 {io_bal['net_ml']:+.0f}ml。")
    active_drugs = [o["drug_name"] for o in orders if o["drug_name"]]
    if active_drugs:
        lines.append("运行中用药：" + "、".join(active_drugs) + "。")

    concerns = []
    actions = []
    for name, s in systems.items():
        if s["risk"] >= 1 and s["note"]:
            concerns.append(f"{s['label']}：{s['note']}")
    if concerns:
        actions.append("关注：" + "；".join(concerns) + "。")
    if overall in ("high","critical"):
        actions.append("建议立即查房评估，必要时多学科会诊。")
    if io_bal["net_ml"] > 500:
        actions.append("考虑调整补液策略，复查心脏超声/BNP。")
    if not actions:
        actions.append("暂无紧急处理建议，持续监护。")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "pid": pid,
        "patient_name": patient["name"],
        "assessed_at": now,
        "hours": hours,
        "overall_risk": overall,
        "risk_score": risk_map[overall],
        "crit_count": crit_count,
        "warn_count": warn_count,
        "systems": systems,
        "vitals_count": len(vitals),
        "orders_active": len(orders),
        "labs_recent": len(labs),
        "io_balance": io_bal,
        "summary": " ".join(lines),
        "actions": actions,
        "disclaimer": "AI 评估基于规则引擎，仅供参考，不构成诊疗建议。临床决策须由主治医师负责。",
    }


# ---------- AI 设置 ----------
_AI_DEFAULTS = {
    "ai.enabled": "false",
    "ai.provider": "openai",
    "ai.base_url": "https://api.deepseek.com/v1",
    "ai.model": "deepseek-v3.2",
    "ai.api_key": "",
    "ai.prompt": (
        "你是一名重症医学临床辅助 AI。请根据以下患者的结构化监护数据，"
        "给出一段简明、专业、面向临床医师的中文解读（5-8 句即可）。"
        "重点指出主要风险点、需要关注的系统、可能的病因方向和下一步监测/处理建议。"
        "请用中文短句，语气克制。"
        "重要声明：以下内容仅供参考，不构成诊疗建议，临床决策须由主治医师负责。"
    ),
}


def _ensure_settings_table():
    from .icu import _get_conn
    conn = _get_conn()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_settings "
            "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)"
        )
        conn.commit()
    except Exception:
        pass


def _store_value(raw):
    """兼容字符串/数字/布尔/JSON 对象，统一存为字符串。"""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw.is_integer():
            return str(int(raw))
        return str(raw)
    if isinstance(raw, (list, dict)):
        import json as _json
        return _json.dumps(raw, ensure_ascii=False)
    return str(raw)


def _parse_value(key, raw):
    """按 key 语义回退成 bool / 字符串。"""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip()
    if not s:
        return ""
    if key == "ai.enabled":
        return s.lower() in ("true", "1", "yes", "on")
    if key.startswith("ai."):
        try:
            import json as _json
            parsed = _json.loads(s)
            return parsed if isinstance(parsed, (str, int, float, bool, list, dict)) else s
        except Exception:
            return s
    return s


def get_setting(key: str, default=None):
    _ensure_settings_table()
    row = fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    if not row or row["value"] is None:
        return _parse_value(key, _AI_DEFAULTS.get(key, default))
    return _parse_value(key, row["value"])


def get_setting_json(key: str, default=None):
    v = get_setting(key, default)
    if v is None or v == "":
        return default
    return v


def get_setting_raw(key: str, default: str = "") -> str:
    """返回 DB 中存储的原始字符串（不做 bool/JSON 反解析），供 API 透给前端。"""
    _ensure_settings_table()
    row = fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    if not row or row["value"] is None:
        return default or (_AI_DEFAULTS.get(key) or "")
    return row["value"]


def list_settings_raw() -> Dict[str, str]:
    """原始字符串版（不解析），供 /api/settings 列表使用。"""
    _ensure_settings_table()
    cur = _get_conn().execute("SELECT key, value FROM app_settings ORDER BY key")
    got = {r["key"]: r["value"] for r in cur.fetchall()}
    out = dict(_AI_DEFAULTS)
    out.update(got)
    return out


def set_setting(key: str, value) -> bool:
    _ensure_settings_table()
    run(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, _store_value(value), _now()),
    )
    return True


# ---------- 联网 AI 解读 ----------
def assess_with_ai(patient_id: int, hours: int = 24) -> Dict:
    """在原规则评估基础上，可选追加 LLM 解读。失败不抛错，回退纯规则。"""
    try:
        base = assess_patient(patient_id, hours)
    except ValueError:
        raise

    enabled = get_setting("ai.enabled", False)
    if not enabled:
        return base

    provider = str(get_setting("ai.provider", "openai") or "openai")
    base_url = str(get_setting("ai.base_url", "https://api.deepseek.com/v1") or "https://api.deepseek.com/v1")
    model = str(get_setting("ai.model", "deepseek-v3.2") or "deepseek-v3.2")
    api_key = str(get_setting("ai.api_key", "") or "")
    prompt = str(get_setting("ai.prompt") or _AI_DEFAULTS["ai.prompt"])

    if not api_key:
        return base

    url = base_url.rstrip("/") + "/chat/completions"
    if not url.startswith("http"):
        return base

    data_for_llm = {
        "pid": base.get("pid"),
        "patient_name": base.get("patient_name"),
        "hours": hours,
        "overall_risk": base.get("overall_risk"),
        "systems": base.get("systems"),
        "summary": base.get("summary"),
        "actions": base.get("actions"),
        "io_balance": base.get("io_balance"),
    }

    try:
        import requests
        resp = requests.post(
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(data_for_llm, ensure_ascii=False)},
                ],
                "temperature": 0,
            },
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            timeout=(3, 25),
        )
        resp.raise_for_status()
        js = resp.json()
        choices = js.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        text = msg.get("content", "") if isinstance(msg, dict) else ""
        if text:
            base["ai_summary"] = text.strip()
            base["ai_source"] = "llm:" + model
            base["ai_provider"] = provider
    except Exception as e:  # noqa: BLE001
        base["ai_error"] = "AI 解读调用失败：" + str(e)
        base["ai_source"] = "rule"
    return base


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