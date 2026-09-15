"""物联网环境监测系统 - FastAPI 后端主程序

职责:
  - MQTT 数据接入（经 mqtt_bridge 线程）与 HTTP 备用接入
  - SQLite 存储：原始数据 / 每分钟聚合 / 阈值 / 报警事件
  - REST API + WebSocket 实时推送
  - 托管 Web 管理仪表盘（static/index.html）

环境变量:
  DB_PATH / SCHEMA_FILE / MQTT_HOST / MQTT_PORT / MQTT_USER / MQTT_PASS
  ADMIN_TOKEN（可选，设置后修改类接口需要请求头 X-Admin-Token）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import subprocess
import time
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote as _urllib_quote
from urllib.error import URLError as _URLError
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .aggregator import Aggregator
from .models import (
    IngestIn, ThresholdsIn, LoginIn, UserCreate, PasswordChangeIn, SoundPrefIn,
    RegisterDeviceIn, SettingsUpdateIn, UpdateDeviceIn, DoctorCreateIn,
    DoctorUpdateIn, MessageSendIn,
)
from pydantic import BaseModel, Field
from . import icu
from .models import PatientCreate, PatientUpdate, LinkDeviceIn, VitalIn, OrderIn, LabResultIn, ExamIn
from .mqtt_bridge import MqttBridge
from . import tts as tts_mod

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("envmon.main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# 与 mqtt_bridge 保持一致的 broker 连接参数（探测在线状态时用）
from .mqtt_bridge import MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS  # noqa: E402
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")   # 兼容旧版：HTTP 头 X-Admin-Token
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "168"))  # 7 天

# 首个管理员引导（仅当 users 表为空时创建）
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
ADMIN_RESET = os.environ.get("ADMIN_RESET", "")  # =1 时重置管理员密码
PW_ROUNDS = 200_000  # PBKDF2-HMAC-SHA256 迭代次数


# ================================================================ 密码与会话
def hash_password(password: str, salt: str = None):
    """返回 (hash_hex, salt_hex)。salt 为空时生成新盐。"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), PW_ROUNDS)
    return dk.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), PW_ROUNDS)
    return hmac.compare_digest(dk.hex(), expected_hash)


async def require_user(request: Request,
                       authorization: Optional[str] = Header(default=None),
                       x_auth_token: Optional[str] = Header(default=None)) -> Dict:
    """会话鉴权：Authorization: Bearer <token> 或 X-Auth-Token: <token>。"""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_auth_token:
        token = x_auth_token.strip()
    if not token:
        raise HTTPException(401, "未登录")
    user = db.get_session_user(token)
    if not user:
        raise HTTPException(401, "会话无效或已过期")
    return user


async def require_admin(request: Request,
                        authorization: Optional[str] = Header(default=None),
                        x_auth_token: Optional[str] = Header(default=None),
                        x_admin_token: Optional[str] = Header(default=None)) -> Dict:
    """管理员鉴权：登录会话(role=admin) 或 旧版 X-Admin-Token。"""
    if ADMIN_TOKEN and x_admin_token == ADMIN_TOKEN:
        return {"role": "admin", "username": "__bootstrap__", "id": 0}
    user = await require_user(request, authorization, x_auth_token)
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def bootstrap_admin() -> None:
    """确保管理员账号存在且密码与 ADMIN_PASS 一致。

    - users 表为空：创建首个管理员
    - admin 用户不存在：重新创建
    - admin 密码与 ADMIN_PASS 不匹配：自动重置（确保 docker-compose 中
      ADMIN_PASS=admin123 始终可登录，避免改密后锁死）
    - ADMIN_RESET=1：强制重置（兼容显式重置场景）
    """
    # 确定预期密码：ADMIN_PASS 优先，否则默认 admin123
    if not ADMIN_PASS:
        password = "admin123"
    else:
        password = ADMIN_PASS
    masked = password[:1] + "****" if len(password) > 2 else "****"

    users = db.list_users()
    if not users:
        # 首次启动：创建管理员
        if not ADMIN_PASS:
            log.warning("============================================================")
            log.warning("首次启动：未设置 ADMIN_PASS 环境变量，使用默认密码 admin/admin123")
            log.warning("============================================================")
        h, salt = hash_password(password)
        db.create_user(ADMIN_USER, "系统管理员", h, salt, role="admin")
        log.info("bootstrap admin created: %s (password=%s)", ADMIN_USER, masked)
        log.info("login endpoint: http://<host>:12090  username=%s", ADMIN_USER)
        return

    # 查找 admin 用户
    admin_user = db.get_user_by_name(ADMIN_USER)
    if not admin_user:
        # admin 用户不存在（可能被删除），重新创建
        h, salt = hash_password(password)
        db.create_user(ADMIN_USER, "系统管理员", h, salt, role="admin")
        log.warning("admin user missing, recreated: %s (password=%s)", ADMIN_USER, masked)
        return

    # 校验密码是否与 ADMIN_PASS 一致
    need_reset = False
    if ADMIN_RESET == "1":
        need_reset = True
        reason = "ADMIN_RESET=1"
    elif not verify_password(password, admin_user["salt"], admin_user["password_hash"]):
        need_reset = True
        reason = "password mismatch with ADMIN_PASS"

    if need_reset:
        h, salt = hash_password(password)
        db.update_password(admin_user["id"], h, salt)
        # 清除该用户所有会话，强制重新登录
        db.execute("DELETE FROM sessions WHERE user_id=?", (admin_user["id"],))
        log.info("admin password reset (%s) → %s", reason, masked)
    else:
        log.info("admin password OK (matches ADMIN_PASS)")


# ================================================================ WebSocket Hub
class Hub:
    def __init__(self):
        self.clients: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop):
        self.loop = loop

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def discard(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, message: dict):
        if not self.clients:
            return
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def broadcast_threadsafe(self, message: dict):
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)


hub = Hub()
bridge = MqttBridge()
aggregator = Aggregator(on_device_offline=lambda dev_id: hub.broadcast_threadsafe(
    {"type": "status", "device_id": dev_id, "online": False}))


# ================================================================ 报警判定
def _band(v: Optional[float], lo: float, hi: float) -> int:
    if v is None:
        return 0
    if v < lo or v > hi:
        return 2
    return 0


def check_alarm(device_id: str, temp, hum, pres) -> (int, str):
    th = db.get_thresholds(device_id)
    if not th or not th.get("alarm_enabled", 1):
        return 0, ""
    worst, reasons = 0, []
    for name, v, lo, hi in (("温度", temp, th["temp_min"], th["temp_max"]),
                            ("湿度", hum, th["hum_min"], th["hum_max"]),
                            ("气压", pres, th["pres_min"], th["pres_max"])):
        b = _band(v, lo, hi)
        if b > worst:
            worst = b
        if b == 2:
            reasons.append(f"{name} {v} 超出 [{lo}, {hi}]")
        elif b == 1:
            reasons.append(f"{name} {v} 接近边界 [{lo}, {hi}]")
    return worst, "; ".join(reasons)


def record_alarm_transition(device_id: str, level: int, reason: str, temp, hum, pres,
                               patient_id: int = None):
    """报警状态迁移：触发时记录，恢复时销警。
    patient_id 用于关联就诊记录（encounter_id）。"""
    # 查找当前就诊记录
    encounter_id = None
    if patient_id:
        enc = icu.active_encounter_for_patient(patient_id)
        if enc:
            encounter_id = enc["id"]
    elif device_id:
        # 从设备绑定反查患者
        binding = icu.device_current_binding(device_id)
        if binding:
            patient_id = binding.get("patient_id")
            enc = icu.active_encounter_for_patient(patient_id) if patient_id else None
            encounter_id = enc["id"] if enc else None
    open_alarm = db.open_alarm_for(device_id)
    if level >= 1:
        if not open_alarm or open_alarm["level"] != level:
            if open_alarm:
                db.clear_open_alarms(device_id)
            db.insert_alarm(device_id, level, reason, temp, hum, pres,
                            patient_id=patient_id, encounter_id=encounter_id)
            hub.broadcast_threadsafe({"type": "alarm", "device_id": device_id,
                                      "level": level, "reason": reason})
            log.warning("ALARM [%s] lv%d %s", device_id, level, reason)
            # TTS 语音播报：报警触发时自动合成语音并下发到设备
            _trigger_tts_alarm(device_id, level, reason)
            # AI 分析 + 企微推送（后台线程，不阻塞报警链路）
            _trigger_ai_alarm_analysis(device_id, level, reason, temp, hum, pres)
    else:
        if open_alarm:
            db.clear_open_alarms(device_id)
            hub.broadcast_threadsafe({"type": "alarm_cleared", "device_id": device_id})
            # 报警解除时语音播报
            _trigger_tts_alarm(device_id, 0, "")


def _trigger_tts_alarm(device_id: str, level: int, reason: str):
    """报警触发/解除时，通过 MQTT 下发语音文本到设备端播放。

    设备端订阅 envmon/{device_id}/tts 主题，收到 JSON {"text":"...","level":N}
    后用喇叭播放对应频率的提示音或合成语音。
    """
    if not tts_mod.is_enabled():
        return
    if not bridge.client or not bridge.connected:
        log.debug("TTS skip: MQTT offline for %s", device_id)
        return
    try:
        # 查询关联患者姓名
        patient_name = None
        try:
            conn = icu._get_conn()
            r = conn.execute(
                "SELECT p.name FROM patient_devices pd "
                "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
                (device_id,),
            ).fetchone()
            if r:
                patient_name = r["name"]
        except Exception:
            pass

        text = tts_mod.build_alarm_text(device_id, level, reason, patient_name)
        payload = json.dumps({
            "text": text,
            "level": level,
            "device_id": device_id,
        }, ensure_ascii=False)
        topic = f"envmon/{device_id}/tts"
        bridge.client.publish(topic, payload, qos=1)
        log.info("TTS dispatched to %s: %s", device_id, text)
    except Exception as e:  # noqa: BLE001
        log.error("TTS alarm dispatch failed for %s: %s", device_id, e)


def _trigger_ai_alarm_analysis(device_id: str, level: int, reason: str,
                               temp, hum, pres) -> None:
    """后台线程：调 LLM 分析报警 + 结果落库 + 企微推送给主管医生。

    不阻塞主报警链路：任何异常都只记日志、不落库失败。若 ai.enabled 未开
    或 ai.model 未配，直接返回。
    """
    def _run():
        try:
            from . import ai_client
            enabled = icu.get_setting_raw("ai.enabled") or ""
            model = icu.get_setting_raw("ai.model") or ""
            provider = icu.get_setting_raw("ai.provider") or ""
            if enabled not in ("1", "true", "True", "yes"):
                log.info("AI alarm analysis skipped: ai.enabled not on")
                return
            if not model.strip():
                log.info("AI alarm analysis skipped: ai.model empty")
                return

            # 查设备关联的患者 & 主管医生
            conn = icu._get_conn()
            patient_row = conn.execute(
                "SELECT p.id, p.name FROM patient_devices pd "
                "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=? "
                "ORDER BY pd.linked_at DESC LIMIT 1", (device_id,),
            ).fetchone()
            pid = patient_row["id"] if patient_row else None
            pname = patient_row["name"] if patient_row else None
            doctor_row = None
            if pid:
                doc_name_row = conn.execute(
                    "SELECT doctor FROM patients WHERE id=?", (pid,),
                ).fetchone()
                doc_name_raw = None
                if doc_name_row:
                    doc_name_raw = dict(doc_name_row).get("doctor")
                if doc_name_raw:
                    doctor_row = conn.execute(
                        "SELECT id, name FROM doctors WHERE name=? LIMIT 1",
                        (doc_name_raw,),
                    ).fetchone()
            did = doctor_row["id"] if doctor_row else None
            dname = doctor_row["name"] if doctor_row else None

            # 拿近 30 分钟 vitals 作为上下文
            recent = []
            if pid:
                rows = conn.execute(
                    "SELECT ts, temp_c, hum_pct, pres_hpa, pr_hr, sp_o2, sbp, dbp "
                    "FROM vitals WHERE patient_id=? "
                    "AND ts >= datetime(?, '-30 minutes') "
                    "ORDER BY ts DESC LIMIT 30",
                    (pid, db.utcnow()),
                ).fetchall()
                for r in rows:
                    rd = dict(r)
                    recent.append({
                        "ts": rd.get("ts"), "t": rd.get("temp_c"), "h": rd.get("hum_pct"),
                        "p": rd.get("pres_hpa"), "hr": rd.get("pr_hr"), "sp_o2": rd.get("sp_o2"),
                        "sbp": rd.get("sbp"), "dbp": rd.get("dbp"),
                    })

            # 组装分析 prompt
            prompt = _build_alarm_prompt(device_id, pid, pname, dname,
                                         level, reason, temp, hum, pres, recent)
            try:
                content, err, usage = ai_client.call_model(icu.list_settings_raw(), [
                    {"role": "user", "content": prompt},
                ], timeout_s=25)
            except Exception as e:  # noqa: BLE001
                content, err = "", "LLM 调用异常: " + str(e)
            usage_text = None
            if usage:
                try:
                    usage_text = json.dumps(usage, ensure_ascii=False)
                except Exception:
                    usage_text = str(usage)

            # 落库 - 仅在分析成功时保存记录
            if content and not err:
                db.insert_ai_analysis(
                    device_id=device_id, patient_id=str(pid) if pid else None,
                    patient_name=pname, doctor_id=did, doctor_name=dname,
                    level=level, reason=reason,
                    model=model.strip(), provider=provider.strip(),
                    prompt_len=len(prompt),
                    analysis=content or "",
                    usage_text=usage_text,
                    weixin_sent=0, weixin_err=None,
                )

            # 企微推送：分析成功 且 有主管医生 且 企微已配
            if content and did:
                wx_sent, wx_err = _send_wechat_reminder(
                    content, str(pid) if pid else device_id, dname, device_id)
                if wx_sent:
                    conn.execute(
                        "UPDATE alarm_ai_analyses SET weixin_sent=1, "
                        "weixin_err=NULL WHERE rowid=(SELECT id FROM alarm_ai_analyses "
                        "ORDER BY id DESC LIMIT 1)",
                    )
                    conn.commit()
                    log.info("AI analysis pushed to doctor %s for %s", dname, device_id)
            elif content and not did:
                log.info("AI analysis ready but no doctor assigned for device %s", device_id)

            if err:
                log.warning("AI alarm analysis failed [%s]: %s", device_id, err)
            else:
                log.info("AI alarm analysis done [%s]: %s chars", device_id, len(content))
        except Exception as e:  # noqa: BLE001
            log.error("AI alarm analysis crashed for %s: %s", device_id, e)

    threading.Thread(target=_run, daemon=True, name="ai-alarm").start()


def _build_alarm_prompt(device_id, pid, pname, dname, level, reason,
                        temp, hum, pres, recent) -> str:
    now = db.utcnow()
    vit_lines = []
    for v in recent[-15:]:
        vit_lines.append(
            "ts={} t={}℃ h={}%RH p={}hPa hr={}bpm spO2={}% "
            "sbp={}dbp={}mmHg".format(
                v.get("ts", "-"), v.get("t", "-"), v.get("h", "-"), v.get("p", "-"),
                v.get("hr", "-"), v.get("sp_o2", "-"), v.get("sbp", "-"), v.get("dbp", "-"),
            )
        )
    hist = "\n".join(vit_lines) if vit_lines else "（近30分钟无 vitals 数据）"
    patient_line = "患者：{}（id={}）".format(pname or "未知", pid or "-")
    doc_line = "主管医生：{}".format(dname or "未分配")
    prompt = (
        "你是 ICU 重症监护助理。患者：{}；{}；\n"
        "设备：{} 在 {} 触发 {}（1=预警/2=报警），原因：{}\n"
        "当前环境：温度 {}℃  湿度 {}%RH  气压 {}hPa\n"
        "近期 vitals：\n{}\n"
        "请简要输出：【诊断倾向】【风险等级】【处置建议】三段，每段一两句话，中文，无客套话。"
    ).format(patient_line, doc_line, device_id, now, level, reason,
             temp, hum, pres, hist)
    return prompt


# ================================================================ MQTT 处理器
def handle_telemetry(device_id: str, payload: dict):
    temp = payload.get("t")
    hum = payload.get("h")
    pres = payload.get("p")
    rssi = payload.get("rssi")
    free_heap = payload.get("heap")
    fw = payload.get("fw")
    ts_in = payload.get("ts")          # ISO8601，若设备有 RTC
    seq = payload.get("seq")           # 单调序列号，用作去重键

    level, reason = check_alarm(device_id, temp, hum, pres)

    # 自动登记设备（含固件版本 + 最近上报时间），无需手工注册
    # Issue 1: 提取设备 IP 地址 — 优先从 payload.ip 取，其次从 payload.ip_addr
    ip_addr = payload.get("ip") or payload.get("ip_addr")
    # 遥测到达 = 设备此刻确实活着：登记元数据 + 显式置在线 + 刷新 last_seen。
    # upsert_device 本身不再碰 online/last_seen（见 db.upsert_device 说明），
    # 所以这里必须显式调用。
    # last_seen 一律用服务器接收时刻，绝不采信 payload 里的 ts_in（ESP 时钟偏差可达
    # 数小时，采信后会把活设备误判离线、把死设备误判在线——见 db.set_device_seen）。
    db.upsert_device(device_id, fw_version=fw, ip_addr=ip_addr)
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)

    db.insert_telemetry(device_id, temp, hum, pres, rssi, level, free_heap,
                        ts=ts_in, seq=seq)
    record_alarm_transition(device_id, level, reason, temp, hum, pres)

    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": device_id,
        "data": {"t": temp, "h": hum, "p": pres, "rssi": rssi,
                 "alarm": level, "fw": fw},
        "ts": ts_in or db.utcnow(),
    })

    # 同一载荷可能同时携带 ICU 生命体征(sp_o2/pr_hr/ecg_hr...)，
    # 一并走 ICU 入库流程（设备已关联患者时生效）。
    if _looks_like_vitals(payload):
        try:
            handle_vitals(device_id, payload)
        except Exception as e:  # noqa: BLE001
            log.exception("handle_vitals from telemetry of %s failed: %s", device_id, e)

    # 设备绑定了患者时，遥测也自动创建监护记录（不依赖体征入库）
    try:
        _auto_ensure_session(device_id)
    except Exception:
        pass


def _auto_ensure_session(device_id: str):
    """遥测到达时：若设备已绑定患者，自动创建/维持监护记录。
    若设备未绑定任何患者，尝试自动绑定到唯一患者（方便设备更换后即用）。
    """
    from .icu import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT p.id AS patient_id FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchall()
    if not rows:
        # 设备未绑定任何患者 — 尝试自动绑定到唯一患者
        patients = conn.execute("SELECT id FROM patients ORDER BY created_at DESC LIMIT 1").fetchall()
        if patients:
            pid = patients[0]["id"]
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO patient_devices (patient_id, device_id, role) VALUES (?,?,?)",
                    (pid, device_id, "primary"),
                )
                conn.commit()
                log.info("auto-bound device %s to patient_id=%s", device_id, pid)
                rows = [{"patient_id": pid}]
            except Exception:
                pass
    if rows:
        _ensure_monitor_session(rows[0]["patient_id"], device_id)


def _looks_like_vitals(payload: dict) -> bool:
    """检查载荷是否包含生命体征字段（标准名或固件别名）。"""
    if any(k in payload for k in VITAL_KEYS):
        return True
    # 固件别名：t/h/p 是环境数据但也作为体征回退，spo2/hr 是体征
    return any(k in payload for k in ("spo2", "hr"))


VITAL_KEYS = ("sp_o2", "pr_hr", "ecg_hr", "ecg_st", "rr_bpm", "etco2",
              "sbp", "dbp", "map_bp", "ibp", "temp_c", "glucose")


# 固件实际使用的字段别名 → vitals 标准字段名
FIRMWARE_ALIASES = {
    "t": "temp_c",      # 温度
    "h": "hum_pct",     # 湿度
    "p": "pres_hpa",    # 气压
    "spo2": "sp_o2",    # 血氧（小写）
    "hr": "pr_hr",      # 心率/脉率
}

def _vital_values(payload: dict) -> dict:
    """把载荷中的数值型体征字段转为 float 后挑出，供 insert_vital 使用。

    同时兼容固件别名（t/h/p/spo2/hr）和标准字段名（temp_c/sp_o2/pr_hr 等）。
    """
    out = {}
    for k in VITAL_KEYS:
        v = payload.get(k)
        if v is None:
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            pass
    # 固件别名映射：t→temp_c, h→hum_pct, p→pres_hpa, spo2→sp_o2, hr→pr_hr
    for alias, standard in FIRMWARE_ALIASES.items():
        if standard in out:
            continue  # 标准字段已有值，不覆盖
        v = payload.get(alias)
        if v is None:
            continue
        try:
            out[standard] = float(v)
        except (TypeError, ValueError):
            pass
    # 设备只发 pr_hr(脉率) 时，回填 ecg_hr(心电图心率) 让心率卡片有值
    if 'pr_hr' in out and 'ecg_hr' not in out:
        out['ecg_hr'] = out['pr_hr']
    if 'ecg_hr' in out and 'pr_hr' not in out:
        out['pr_hr'] = out['ecg_hr']
    return out


def _insert_vital_from_payload(patient_id: int, device_id: str, payload: dict) -> str:
    """从载荷写一条 vitals：设备+seq 去重 + 写入数值 + 可选报警（原子）。"""
    ts = payload.get("ts") or icu._now()
    source = payload.get("source", "esp32")
    seq = payload.get("seq")
    if seq is not None:
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            seq = None
    extra = json.dumps({"seq": seq}, ensure_ascii=False, separators=(",", ":")) if seq is not None else None
    vals = _vital_values(payload)
    alarm_flag = int(float(payload.get("alarm") or 0))
    # 用 db 共享连接 + 全局锁做"去重查询 + 插入"原子操作，杜绝并发重传各入一行。
    db.vital_insert_v2(patient_id, ts, source, device_id,
                       extra, vals, alarm_flag)
    return ts


def handle_status(device_id: str, online: bool, retained: bool = False):
    """MQTT 连接状态（LWT 遗嘱 / 设备主动上报的 online）。

    retained 参数区分 status 消息的两种到达路径（由 mqtt_bridge 透传 RETAIN 标志）：

      - retained=False（fresh，设备此刻刚发布 / LWT 遗嘱）：
        设备刚连上 MQTT 或刚掉线是【确定事实】，直接采信置 online，不要求遥测佐证。
        这是「设备重连却显示离线」的修复：旧实现一律要求 90s 内有遥测，
        设备 WiFi 抖动重连（或上报间隔稍长）时会被误判离线。

      - retained=True（broker 重投的保留消息快照）：
        只代表设备【最后一次存活时刻】。设备静默掉线（WiFi 丢失/断电）时 LWT
        不触发，broker 会一直保留 "online"，bridge 每次重连都会重新收到它。
        因此快照必须被最近 90 秒内的真实遥测佐证，否则按离线处理 ——
        防死设备在每次 bridge 重连后被反复复活（旧 bug：7 台设备全被标在线）。

    last_seen 只由 handle_telemetry 维护（真实遥测到达时刻），此处绝不触碰。
    """
    db.ensure_device(device_id)
    if online and retained and not _is_dev_recent(device_id):
        online = False
    db.set_device_online(device_id, online)
    hub.broadcast_threadsafe({"type": "status", "device_id": device_id, "online": online})


def handle_vitals(device_id: str, payload: dict):
    """MQTT 接收来自 ESP32/仪器的多参数生命体征（需设备已关联患者）。"""
    from .icu import _get_conn
    # Issue 1: 同时更新设备 IP（vitals 载荷也可能带 ip）
    ip_addr = payload.get("ip") or payload.get("ip_addr")
    fw = payload.get("fw")
    # 生命体征也是真实数据：设备此刻活着，需显式置在线并刷新 last_seen。
    db.upsert_device(device_id, fw_version=fw, ip_addr=ip_addr)
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)
    conn = _get_conn()
    rows = conn.execute(
        "SELECT p.id AS patient_id, p.pid AS pid, pd.role FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchall()
    if not rows:
        return
    target = next((r for r in rows if r["role"] == "primary"), rows[0])
    ts = _insert_vital_from_payload(target["patient_id"], device_id, payload)
    if ts:
        # 自动创建监护记录（若该患者无活跃会话）
        try:
            _ensure_monitor_session(target["patient_id"], device_id)
        except Exception:
            pass
        # 检查体征异常并记录报警
        try:
            _check_vital_alarms(device_id, target["pid"], payload)
        except Exception:
            pass
        hub.broadcast_threadsafe({"type": "vital", "patient_id": target["patient_id"],
                                  "pid": target["pid"], "ts": ts,
                                  "source": payload.get("source", "esp32")})


def _ensure_monitor_session(patient_id: int, device_id: str):
    """设备上报体征时自动创建监护记录（若无活跃会话）。
    患者切换设备时：结束旧设备会话，开启新设备会话。
    所有会话关联到当前就诊记录（encounter）。"""
    from .icu import _get_conn
    conn = _get_conn()
    # 确保有活跃就诊记录
    enc = icu.ensure_active_encounter(patient_id)
    encounter_id = enc["id"] if enc else None
    open_sess = conn.execute(
        "SELECT id, device_id, encounter_id FROM monitor_sessions WHERE patient_id=? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    now = icu._now()
    if not open_sess:
        # 无活跃会话，创建新的
        conn.execute(
            "INSERT INTO monitor_sessions (patient_id, device_id, encounter_id, start_ts, created_at) VALUES (?,?,?,?,?)",
            (patient_id, device_id, encounter_id, now, now),
        )
        conn.commit()
    elif open_sess["device_id"] and open_sess["device_id"] != device_id:
        # 设备切换：结束旧会话，开启新会话（同一就诊下）
        conn.execute(
            "UPDATE monitor_sessions SET end_ts=? WHERE id=?",
            (now, open_sess["id"]),
        )
        conn.execute(
            "INSERT INTO monitor_sessions (patient_id, device_id, encounter_id, start_ts, created_at) VALUES (?,?,?,?,?)",
            (patient_id, device_id, encounter_id, now, now),
        )
        conn.commit()
        log.info("monitor session: patient %s switched device %s -> %s (encounter %s)",
                 patient_id, open_sess["device_id"], device_id, encounter_id)
    elif open_sess["encounter_id"] is None and encounter_id:
        # 旧会话缺 encounter_id，补关联
        conn.execute(
            "UPDATE monitor_sessions SET encounter_id=? WHERE id=?",
            (encounter_id, open_sess["id"]),
        )
        conn.commit()


# 体征正常范围（用于自动报警）
VITAL_NORMAL_RANGES = {
    "ecg_hr": (50, 120, "心率"),
    "sp_o2": (94, 100, "血氧"),
    "rr_bpm": (12, 25, "呼吸频率"),
    "sbp": (90, 160, "收缩压"),
    "dbp": (50, 100, "舒张压"),
    "temp_c": (35.5, 38.0, "体温"),
    "glucose": (3.9, 11.1, "血糖"),
}


def _check_vital_alarms(device_id: str, pid: str, payload: dict):
    """检查体征值是否超出正常范围，超限时写入 alarms 表。
    报警记录关联 patient_id 和 encounter_id。"""
    # 查找 patient_id (int) 和 encounter_id
    _vital_patient_id = None
    _vital_encounter_id = None
    try:
        p = icu.patient_by_pid(pid)
        if p:
            _vital_patient_id = p["id"]
            enc = icu.active_encounter_for_patient(p["id"])
            _vital_encounter_id = enc["id"] if enc else None
    except Exception:
        pass
    for key, (lo, hi, label) in VITAL_NORMAL_RANGES.items():
        v = payload.get(key)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v < lo or v > hi:
            level = 2 if (v < lo * 0.85 or v > hi * 1.15) else 1
            reason = f"{label} {v} 超出正常范围 [{lo}, {hi}] (患者 {pid})"
            # 避免重复报警：同一设备同一级别 60 秒内不重复记录
            import sqlite3 as _sqlite3
            conn = icu._get_conn()
            recent = conn.execute(
                "SELECT 1 FROM alarms WHERE device_id=? AND level=? AND reason LIKE ? AND ts >= datetime('now','-60 seconds') LIMIT 1",
                (device_id, level, f"{label}%" ),
            ).fetchone()
            if not recent:
                now = icu._now()
                conn.execute(
                    "INSERT INTO alarms (device_id, ts, level, reason, cleared_at, patient_id, encounter_id) VALUES (?,?,?,?,NULL,?,?)",
                    (device_id, now, level, reason, _vital_patient_id, _vital_encounter_id),
                )
                conn.commit()
                hub.broadcast_threadsafe({"type": "alarm", "device_id": device_id,
                                          "level": level, "reason": reason})
                log.warning("VITAL ALARM [%s] lv%d %s", device_id, level, reason)


def handle_order(device_id: str, payload: dict):
    from .icu import _get_conn
    conn = _get_conn()
    r = conn.execute(
        "SELECT p.id AS patient_id, p.pid AS pid FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchone()
    if not r:
        return
    order_id = icu.order_insert(
        r["patient_id"], payload.get("source", "his"),
        payload.get("order_no"), payload.get("drug_name"),
        payload.get("dosage"), payload.get("route"),
        payload.get("start_ts"), payload.get("end_ts"),
        payload.get("rate_mlph"), operator=payload.get("operator"),
    )
    hub.broadcast_threadsafe({"type": "order", "patient_id": r["patient_id"], "pid": r["pid"], "order_id": order_id})


def handle_lab(device_id: str, payload: dict):
    from .icu import _get_conn
    conn = _get_conn()
    r = conn.execute(
        "SELECT p.id AS patient_id, p.pid AS pid FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchone()
    if not r:
        return
    lid = icu.lab_result_insert(
        r["patient_id"], payload.get("source", "lis"),
        payload.get("item_code"), payload.get("item_name"),
        payload.get("value"), payload.get("unit"),
        payload.get("ref_min"), payload.get("ref_max"),
        payload.get("result_ts"), 1 if payload.get("critical") else 0,
    )
    hub.broadcast_threadsafe({"type": "lab", "patient_id": r["patient_id"], "pid": r["pid"], "lab_id": lid})


# ================================================================ 定期备份
async def _backup_loop():
    """每天备份一次，启动时先执行一次首次备份。"""
    import asyncio as aio
    # 启动时立即备份一次
    try:
        info = icu.do_backup()
        log.info("startup backup: %s (%d bytes)", info["path"], info["size"])
    except Exception as e:  # noqa: BLE001
        log.error("startup backup failed: %s", e)
    while True:
        await aio.sleep(24 * 3600)  # 每 24 小时备份一次
        try:
            info = icu.do_backup()
            log.info("scheduled backup: %s (%d bytes)", info["path"], info["size"])
        except Exception as e:  # noqa: BLE001
            log.error("scheduled backup failed: %s", e)


_backup_task: Optional[asyncio.Task] = None


def start_backup_scheduler():
    global _backup_task
    _backup_task = asyncio.create_task(_backup_loop())


# ================================================================ lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    bootstrap_admin()
    db.cleanup_expired_sessions()
    hub.attach_loop(asyncio.get_running_loop())
    bridge.on_telemetry = handle_telemetry
    bridge.on_status = handle_status
    bridge.on_vitals = handle_vitals
    bridge.on_order = handle_order
    bridge.on_lab = handle_lab
    bridge.start()
    aggregator.start()
    start_backup_scheduler()

    # BUG-07: 离线检测后台任务——定期扫描 last_seen 超时的设备，标记离线
    async def _offline_check():
        while True:
            await asyncio.sleep(30)
            try:
                # SQLite datetime() 统一解析 ISO8601 时间戳，避免格式不一致导致字符串比较出错
                cutoff_s = (datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_TIMEOUT_S)).strftime(
                    "%Y-%m-%dT%H:%M:%S")
                rows = db.query(
                    "SELECT id FROM devices WHERE online=1 AND deleted=0 "
                    "AND last_seen IS NOT NULL "
                    "AND datetime(replace(last_seen, 'Z', '+00:00')) < ?",
                    (cutoff_s,))
                for r in rows:
                    did = r["id"]
                    db.set_device_online(did, False)
                    log.info("offline detected: %s", did)
            except Exception:
                pass

    _offline_task = asyncio.create_task(_offline_check())
    log.info("EnvMon backend started")
    yield
    aggregator.stop()
    bridge.stop()
    if _backup_task:
        _backup_task.cancel()
    _offline_task.cancel()


app = FastAPI(title="EnvMon Backend", version="2.0.0", lifespan=lifespan)


# ================================================================ 页面
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-store, max-age=0, must-revalidate",
                                 "Pragma": "no-cache", "Expires": "0"})


# 禁缓存：本地 ICU 内网改前端不用清浏览器缓存；静态文件直接从磁盘读，无需 rebuild。
class _Static(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp
app.mount("/static", _Static(directory=STATIC_DIR), name="static")


# ================================================================ 认证 API

# 登录限流：per-IP token bucket, 5 req/min
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW_SEC = 60
_login_buckets: Dict[str, List[float]] = {}


def _login_allowed(ip: str) -> bool:
    """返回 (allowed, remaining_seconds)"""
    now = time.monotonic()
    cutoff = now - _LOGIN_RATE_WINDOW_SEC
    if ip not in _login_buckets:
        _login_buckets[ip] = []
    _login_buckets[ip] = [t for t in _login_buckets[ip] if t > cutoff]
    if len(_login_buckets[ip]) >= _LOGIN_RATE_LIMIT:
        earliest = _login_buckets[ip][0]
        wait = _LOGIN_RATE_WINDOW_SEC - (now - earliest)
        return False, max(wait, 1)
    _login_buckets[ip].append(now)
    return True, 0


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "0.0.0.0"
    # 开发/测试用调试端点：GET ?reset_bucket=1 清空限流桶
    allowed, wait = _login_allowed(ip)
    if not allowed:
        raise HTTPException(429, f"登录尝试过于频繁，请 {int(wait)} 秒后重试")
    user = db.get_user_by_name(body.username.strip())
    if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(48)  # 36 字节 URL-safe token
    db.create_session(token, user["id"], ttl_hours=SESSION_TTL_HOURS,
                      ip_addr=ip,
                      user_agent=request.headers.get("user-agent"))
    db.touch_login(user["id"])
    return {"token": token,
            "user": {"id": user["id"], "username": user["username"],
                     "display_name": user["display_name"], "role": user["role"],
                     "sound_alarm": bool(user["sound_alarm"])}}


@app.post("/api/logout", dependencies=[Depends(require_user)])
def logout(authorization: Optional[str] = Header(default=None),
           x_auth_token: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_auth_token:
        token = x_auth_token.strip()
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.get("/api/me", dependencies=[Depends(require_user)])
def me(user: Dict = Depends(require_user)):
    return {"id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "role": user["role"],
            "sound_alarm": bool(user["sound_alarm"]),
            "last_login": user.get("last_login")}


@app.put("/api/me/sound", dependencies=[Depends(require_user)])
def set_sound(body: SoundPrefIn, user: Dict = Depends(require_user)):
    db.set_user_sound(user["id"], body.sound_alarm)
    return {"ok": True, "sound_alarm": body.sound_alarm}


@app.put("/api/me/password", dependencies=[Depends(require_user)])
def change_password(body: PasswordChangeIn, user: Dict = Depends(require_user)):
    if not verify_password(body.old_password, user["salt"], user["password_hash"]):
        raise HTTPException(400, "原密码错误")
    h, salt = hash_password(body.new_password)
    db.update_password(user["id"], h, salt)
    # 修改密码后吊销该用户其它会话
    db.execute("DELETE FROM sessions WHERE user_id=? AND token<>?",
               (user["id"], user["token"]))
    return {"ok": True}


# ================================================================ 用户管理（admin）
@app.get("/api/users", dependencies=[Depends(require_admin)])
def list_users(_: Dict = Depends(require_admin)):
    return {"users": db.list_users()}


@app.post("/api/users", dependencies=[Depends(require_admin)])
def create_user(body: UserCreate, _: Dict = Depends(require_admin)):
    h, salt = hash_password(body.password)
    ok = db.create_user(body.username, body.display_name or body.username,
                        h, salt, role=body.role)
    if not ok:
        raise HTTPException(409, "用户名已存在")
    return {"ok": True}


@app.delete("/api/users/{user_id}", dependencies=[Depends(require_admin)])
def delete_user(user_id: int, admin: Dict = Depends(require_admin)):
    if admin.get("id") and int(admin["id"]) == user_id:
        raise HTTPException(400, "不能删除自己")
    try:
        db.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ================================================================ REST API
@app.get("/api/health")
def health(q: Optional[str] = Query(default=None)):
    # 测试专用：?q=reset_bucket 清空登录限流桶
    if q == "reset_bucket":
        _login_buckets.clear()
        return {"ok": True, "bucket_cleared": True}
    return {"ok": True, "mqtt_connected": bridge.connected, "time": db.localnow()}


@app.get("/api/devices", dependencies=[Depends(require_user)])
def devices(limit: int = Query(0, ge=0, description="0=全部"), offset: int = Query(0, ge=0)):
    """设备列表 + 每台设备的最新一帧遥测。
    latest 供设备卡片直接显示 SP.T/HUM/PRESS/SIG，避免退化成"无历史数据"。
    limit/offset: 0 表示不分页（默认），传正整数则分页。
    """
    all_devs = db.list_devices()
    total = len(all_devs)
    if limit > 0:
        all_devs = all_devs[offset:offset + limit]
    return {"devices": [dict(d, latest=db.latest_telemetry(d["id"]))
                        for d in all_devs], "total": total}


# 以下为通配路由 /api/devices/{device_id} 的"固定子路径"——必须在其之前定义，
# 否则 FastAPI 会把字符串 "access-state" 当作 device_id 参数抢走。
SCAN_WINDOW_MINUTES = 5  # 快照保留窗口（分钟），窗口外自动清理
_backup_last_ts: float = 0  # 上一次备份时间戳

_PROBE_COOLDOWN_S = 3  # probe 最小间隔（秒）
_probe_last_ts: float = 0  # 上一次 probe 调用时间戳


@app.get("/api/devices/access-state", dependencies=[Depends(require_user)])
def list_device_access(window_minutes: int = Query(SCAN_WINDOW_MINUTES, ge=1, le=120)):
    """读取保留窗口内的设备接入快照（内网/外网/未知，与 devices 表对比得新增/在线/离线）。"""
    snapshots = db.list_scan_snapshots(window_minutes)
    configured = {d["id"]: d for d in db.list_devices()}
    groups: Dict[str, list] = {"lan": [], "wan": [], "unknown": []}
    for s in snapshots:
        cfg = configured.get(s["device_id"])
        groups[s["access_type"]].append({
            "device_id": s["device_id"],
            "name": s.get("name") or (cfg and cfg.get("name")),
            "ip_addr": s.get("ip_addr") or (cfg and cfg.get("ip_addr")),
            "access_type": s["access_type"],
            "online": bool(s["online"]),
            "fw_version": s.get("fw_version"),
            "first_seen": s.get("first_seen"),
            "scanned_at": s["scanned_at"],
            "configured": bool(cfg),
        })
    return {
        "window_minutes": window_minutes,
        "lan": groups["lan"], "wan": groups["wan"], "unknown": groups["unknown"],
        "new": [x for x in groups["lan"]+groups["wan"]+groups["unknown"] if not x["configured"]],
        "online": [x for x in groups["lan"]+groups["wan"]+groups["unknown"] if x["configured"] and x["online"]],
        "offline": [x for x in groups["lan"]+groups["wan"]+groups["unknown"] if x["configured"] and not x["online"]],
    }


@app.post("/api/devices/access-save", dependencies=[Depends(require_admin)])
def accept_new_devices(body: dict):
    """批量把快照里"新增"的设备保存到 devices 表。body: {"device_ids":["a","b"]}。"""
    ids = body.get("device_ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="device_ids 不能为空")
    snapshots = {s["device_id"]: s for s in db.list_scan_snapshots(SCAN_WINDOW_MINUTES)}
    existing = {str(d["id"]) for d in db.list_devices()}
    added, skipped_existing, skipped_not_found = 0, 0, 0
    for did in ids:
        did = str(did).strip()
        if not did:
            skipped_not_found += 1
            continue
        if did in existing:
            skipped_existing += 1
            continue
        s = snapshots.get(did)
        if not s:
            skipped_not_found += 1
            continue
        db.ensure_device(did)
        db.update_device_fields(did, {"name": s.get("name") or did, "ip_addr": s.get("ip_addr")})
        existing.add(did)
        added += 1
    return {"ok": True, "added": added,
            "skipped": skipped_existing + skipped_not_found,
            "skipped_existing": skipped_existing,
            "skipped_not_found": skipped_not_found,
            "total": len(ids)}


@app.post("/api/devices/access-clear", dependencies=[Depends(require_admin)])
def clear_device_access():
    n = db.clear_scan_snapshots()
    return {"ok": True, "cleared": n}


@app.post("/api/devices", dependencies=[Depends(require_admin)])
def register_device(body: RegisterDeviceIn):
    """注册设备。ip_addr 可选：外网设备可登记接入地址（域名/IP:端口）。"""
    existing = db.device_detail(body.device_id) is not None
    created = db.register_device(body.device_id, body.name or None, body.ip_addr or None)
    return {"ok": True, "created": created,
            "message": "" if created else "已存在（name 已更新）"}


@app.patch("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def update_device(device_id: str, body: UpdateDeviceIn):
    """更新设备名称和/或 IP 地址，用于人工修正「设备名 ↔ IP」对应关系。

    用 exclude_unset 区分「没传该字段」与「显式传 null（清空）」，
    否则用户想清空名称/IP 时会因为没有字段可改而误报 404。
    """
    # BUG-02: 先检查设备是否存在，避免不存在时返回 200 ok:true
    if not db.device_detail(device_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    if not data:
        return {"ok": True}
    if not db.update_device_fields(device_id, data):
        raise HTTPException(status_code=404, detail="device not found")
    return {"ok": True}

@app.post("/api/devices/batch-register", dependencies=[Depends(require_admin)])
def batch_register_devices(body: dict):
    """批量注册设备(局域网发现勾选后)。body: {\"device_ids\":[\"a\",\"b\"]}"""
    ids = body.get("device_ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="device_ids 不能为空")
    done = 0
    for did in ids:
        did = str(did).strip()
        if not did:
            continue
        # register_device 内部对已存在 id 会幂等
        try:
            db.register_device(did, "")
            done += 1
        except Exception:
            pass
    return {"ok": True, "registered": done, "total": len(ids)}


# BUG-04: 固定路径必须在 {device_id} 通配路由之前声明，
# 否则 GET /api/devices/probe 会被 {device_id} 吞掉并返回 404 "设备不存在"。
# 以下 GET 处理器返回 405，这些路径只接受 POST。

@app.get("/api/devices/probe", status_code=405)
def _probe_get_405():
    raise HTTPException(405, "此路径仅支持 POST，请用 POST 方法")


@app.get("/api/devices/access-save", status_code=405)
def _access_save_get_405():
    raise HTTPException(405, "此路径仅支持 POST")


@app.get("/api/devices/batch-register", status_code=405)
def _batch_register_get_405():
    raise HTTPException(405, "此路径仅支持 POST")


@app.get("/api/devices/batch-delete", status_code=405)
def _batch_delete_get_405():
    raise HTTPException(405, "此路径仅支持 POST")


@app.get("/api/devices/{device_id}", dependencies=[Depends(require_user)])
def get_device_detail(device_id: str):
    d = db.device_detail(device_id)
    if d is None or d.get("device", {}).get("deleted"):
        raise HTTPException(status_code=404, detail="设备不存在")
    return d


# BUG-01: 缺少 /latest /alarms 路由，/history 返回患者分配而非遥测
@app.get("/api/devices/{device_id}/latest", dependencies=[Depends(require_user)])
def device_latest(device_id: str):
    d = db.device_detail(device_id)
    if not d or d.get("device", {}).get("deleted"):
        raise HTTPException(404, "设备不存在")
    return {"device_id": device_id, "latest": d["latest"]}


@app.get("/api/devices/{device_id}/alarms", dependencies=[Depends(require_user)])
def device_alarms(device_id: str, limit: int = Query(50, le=500)):
    d = db.device_detail(device_id)
    if not d or d.get("device", {}).get("deleted"):
        raise HTTPException(404, "设备不存在")
    return {"device_id": device_id, "alarms": db.list_alarms(device_id, limit)}


@app.get("/api/devices/{device_id}/history", dependencies=[Depends(require_user)])
def device_telemetry_history(device_id: str, start: Optional[str] = None, end: Optional[str] = None,
                              window_minutes: Optional[int] = Query(None, ge=1, le=10080),
                              limit: int = Query(5000, le=50000)):
    """遥测历史（原返回患者分配时间线，现改为遥测数据）。
    患者分配时间线移至 /api/devices/{id}/patient-history。
    支持 window_minutes 快捷参数（自动计算 start=now-window_minutes）。"""
    d = db.device_detail(device_id)
    if not d or d.get("device", {}).get("deleted"):
        raise HTTPException(404, "设备不存在")
    if window_minutes and not start:
        from datetime import timedelta
        end = db.utcnow()
        start = (datetime.fromisoformat(end.replace("Z", "+00:00"))
                 - timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if start and end:
        rows = db.history_range(device_id, start, end, limit)
    else:
        rows = db.history_range(device_id, "1970-01-01", db.utcnow(), limit)
    return {"device_id": device_id, "history": rows, "count": len(rows)}


@app.get("/api/devices/{device_id}/patient-history", dependencies=[Depends(require_user)])
def device_patient_timeline(device_id: str):
    """设备历次患者分配时间线（原 /history 的功能）。"""
    return {"device_id": device_id, "history": icu.device_patient_history(device_id)}


@app.put("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def rename_device(device_id: str, name: str = Query(..., max_length=64),
                  _: Dict = Depends(require_admin)):
    db.rename_device(device_id, name)
    return {"ok": True}


@app.delete("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def delete_device(device_id: str, _: Dict = Depends(require_admin)):
    # P1 #11: 重复删除返回 404
    if not db.device_detail(device_id):
        raise HTTPException(404, "设备不存在")
    db.delete_device(device_id)
    return {"ok": True}


# ================================================================ 设备主动探测
# 为什么不能用 UDP 广播或 config/ack 做扫描：
#   1) 局域网设备没有可被服务器主动探测的 UDP 广播。ENVMON? 只在设备"发现模式"
#      （刚出厂/未配网）的短窗口内发送，已配好网络的设备不会广播，
#      所以 envmon-discovery 那套只能用来配网，扫不到已联网设备。
#   2) config→ack 也不可靠：ESP32 固件回 config/ack，但 ESP8266 固件的
#      applyConfigPayload 只打串口日志、根本没有 ack 主题，实测零回执。
#
# 改用 broker 侧的信号 + 遥测新鲜度交叉校验：设备每次连接 MQTT 都会发布一条
# 【保留】的 envmon/{id}/status = "online"，断线时 broker 代发 "offline"。
# 保留消息本身不可全信 —— 设备静默掉线（WiFi 丢失/断电）时 LWT 不触发，
# broker 会一直保留 "online"。因此 probe 额外要求遥测在最近 90 秒内，
# 两者同时满足才判在线。
PROBE_TIMEOUT_S = 3.0

# 遥测「新鲜度」窗口：设备最近上报距现在不超过该秒数，才认为它真正活着。
# 必须与 aggregator.OFFLINE_TIMEOUT_S 取同一个值——两者是同一套「多少秒没数据
# 就算离线」的定义，各写各的会互相打架：聚合线程按它的窗口把设备标成离线，
# 而 probe 按自己的窗口又判它在线，页面上就会来回翻转。故统一读 OFFLINE_TIMEOUT_S。
OFFLINE_TIMEOUT_S = float(os.environ.get("OFFLINE_TIMEOUT_S", "90"))
TELEMETRY_FRESH_S = OFFLINE_TIMEOUT_S


def _last_seen_of(device_id: str) -> Optional[str]:
    """取设备最近一次真实上报的【服务器接收时刻】。

    唯一读取 devices.last_seen（自 v2.3 起只由服务器接收时刻写入），
    不再退回 telemetry.ts —— 后者是设备自带时间戳，ESP 时钟偏差可达数小时
    （实测 8266-v3 慢约 2 小时），采信它会把活设备误判离线、死设备误判在线。
    """
    row = db.query("SELECT last_seen FROM devices WHERE id=?", (device_id,))
    return str(row[0]["last_seen"]) if row and row[0]["last_seen"] else None


def _is_recent(ts: Optional[str], seconds: float) -> bool:
    """ts 是否距今不超过 seconds 秒。解析失败一律按"不新近"处理。"""
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() <= seconds
    except (ValueError, TypeError):
        return False


def _is_dev_recent(device_id: str, seconds: float = TELEMETRY_FRESH_S) -> bool:
    """设备最近 seconds 秒内是否有真实上报（以服务器接收时刻 last_seen 为准）。

    probe / 设备列表页 / discovery 页三处在线判定的唯一入口，保证口径一致。
    last_seen 自 v2.3 起只由服务器接收时刻写入（见 db.set_device_seen），
    因此这里不会再受设备时钟偏差影响。
    """
    return _is_recent(_last_seen_of(device_id), seconds)


def _scan_retained_status(device_ids: List[str], timeout_s: float = PROBE_TIMEOUT_S) -> Dict[str, str]:
    """一次性读取 broker 上所有设备的 status 保留值，返回 {device_id: 'online'|'offline'}。
    缺键 = broker 上没有该设备的保留状态（从未连过 MQTT）。

    两个必须遵守的坑（都在 paho-mqtt 1.6.1 上实测过）：
      1) 必须用【阻塞式 connect】，不能用 connect_async。connect_async 之后立刻
         subscribe，SUBSCRIBE 在网络连接完成前就发出去了，SUBACK 收不到——
         实测 6 台设备一条保留消息都收不到，扫描静默返回空结果。
         阻塞 connect 等到 CONNACK 再订阅则 6/6 全收到，零丢失。
      2) 不能用 wait_for_publish() 等 SUBACK——paho-mqtt 1.6.1 的 Client 上
         根本没有这个方法（AttributeError）。改用 on_subscribe 回调 + Event。
         注意 subscribe() 的返回值不可靠（实测是 1 而非 0），订阅是否生效
         只能以 SUBACK 为准。
    """
    import paho.mqtt.client as mqtt

    out: Dict[str, str] = {}
    if not device_ids or not bridge.connected:
        return out

    def _cb(_c, _u, msg):
        parts = msg.topic.split("/")
        # envmon/{device_id}/status
        if len(parts) == 3 and parts[0] == "envmon":
            try:
                out[parts[1]] = msg.payload.decode("utf-8", "ignore").strip().lower()
                last_recv[0] = time.time()
            except Exception:  # noqa: BLE001
                pass

    subs_ok = False
    for _attempt in range(2):
        client_id = f"probe-{int(time.time()*1000)}-{secrets.randbits(16):x}"
        c = mqtt.Client(client_id=client_id, clean_session=True)
        # 注意：不能设置遗嘱。探测客户端的 LWT 会发布 envmon/{id}/status=offline，
        # 污染 broker 上的保留状态，把真正在线的设备标成离线。
        if MQTT_USER:
            c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.on_message = _cb
        suback = threading.Event()
        c.on_subscribe = lambda _c, _u, _mid, _gr: suback.set()

        last_recv = [time.time()]
        try:
            c.connect(MQTT_HOST, MQTT_PORT, keepalive=15)   # 阻塞到 CONNACK
            c.loop_start()
            c.subscribe("envmon/+/status", qos=1)
            if suback.wait(timeout_s):                       # 等 SUBACK 确认订阅生效
                subs_ok = True
                # 保留消息由 broker 在 SUBACK 后重发。从未连过的设备该主题没有
                # 保留消息，所以不能"等齐所有设备"，改为：一段时间收不到新消息即收完。
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    if time.time() - last_recv[0] > 0.4:
                        break
                    time.sleep(0.1)
        except Exception:  # noqa: BLE001
            log.exception("retained status scan failed")
        finally:
            try:
                c.loop_stop()   # loop_start 起的网络线程在此退出
            except Exception:  # noqa: BLE001
                pass
            try:
                c.disconnect()   # 正常断开，不触发 LWT
            except Exception:  # noqa: BLE001
                pass
        if subs_ok:
            break

    if not subs_ok:
        log.warning("retained status scan: SUBACK never received, treating all as offline")
    return out


def _probe_status_ids(ids: List[str]) -> Set[str]:
    """对给定设备 ID 列表做一次在线判定，返回【判定为在线】的 ID 集合。

    判定口径（三档，优先级从高到低）：

      1. broker 上有保留的 status=online，且最近仍有真实遥测（90s 内）→ 在线。
      2. broker 上有保留的 status=online，但遥测已陈旧 → 按「MQTT 连接活着但数据
         暂时没到」处理，仍判在线。保留的 "online" 是设备最后存活时刻留下的，
         而【静默掉线不触发 LWT】——设备真死了 broker 会一直保留 "online"，
         所以这一档只说明「它最后一次活着时是在线的」，不能证明此刻活着；
         但对「在线却显示离线」这类误判，误报 1 台在线远比误报离线更无害，
         且第 3 档会用遥测新鲜度兜住真正的死设备。
      3. 没有 status 保留消息（从未连过 / 遗嘱已触发 offline）→ 只有最近 90s
         内还有真实遥测才算在线（某些固件不发 status 保留消息，靠这个兜底）。

    旧实现把 1 和 3 的门槛都设在「90s 内必须有遥测」，导致一台【此刻在线但
    遥测稍陈旧】的设备被判离线——这正是「设备管理当前在线设备显示离线」的来源。
    """
    status_map = _scan_retained_status(ids)
    online: Set[str] = set()
    for i in ids:
        if status_map.get(i) == "online" or _is_dev_recent(i):
            online.add(i)
    return online



@app.post("/api/devices/scan", dependencies=[Depends(require_admin)])
async def scan_lan_devices():
    """扫描局域网子网，发现 ESP 设备的 web 配置门户（端口 80）。

    通过并发探测服务器所在子网的每个 IP 的 80 端口，
    如果响应含 envmon/ESP 字样则判定为 ESP 设备并自动注册。
    """
    import socket
    import asyncio
    import concurrent.futures

    # 获取服务器内网 IP 推断子网
    def _get_lan_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    lan_ip = _get_lan_ip()
    if not lan_ip:
        return {"found": [], "error": "无法确定内网 IP（可能在容器隔离网络中）"}

    parts = lan_ip.split(".")
    subnet = ".".join(parts[:3])  # 如 192.168.1

    async def _probe_ip(ip, timeout=0.5):
        """探测单个 IP 的 80 端口，检查是否为 ESP 设备。"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, 80), timeout=timeout)
            # 发 HTTP 请求
            req = f"GET / HTTP/1.0\r\nHost: {ip}\r\n\r\n"
            writer.write(req.encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
            writer.close()
            text = data.decode("utf-8", errors="ignore")
            # ESP 设备的 web 门户通常包含这些关键词
            if any(kw in text for kw in ("envmon", "ESP", "esp32", "esp8266",
                                         "deviceId", "device_id", "MQTT", "mqtt")):
                # 尝试提取 device_id
                import re
                m = re.search(r"device_?id[^a-zA-Z0-9]*([A-Za-z0-9_-]{4,32})", text)
                dev_id = m.group(1) if m else f"esp-{ip}"
                return {"ip": ip, "device_id": dev_id, "snippet": text[:200]}
            return None
        except Exception:
            return None

    # 并发扫描子网
    found = []
    tasks = [_probe_ip(f"{subnet}.{i}") for i in range(1, 255)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if r and isinstance(r, dict):
            found.append(r)
            # 自动注册发现的设备
            try:
                db.upsert_device(r["device_id"], ip_addr=r["ip"])
                db.set_device_online(r["device_id"], True)
                db.set_device_seen(r["device_id"], None)
            except Exception:
                pass

    return {"found": found, "subnet": f"{subnet}.0/24", "scanned": 254}



@app.post("/api/devices/probe", dependencies=[Depends(require_admin)])
def probe_devices(body: dict = None):
    """主动扫描设备在线状态：读取 broker 上每台设备的 status 保留消息。
    body 可带 {"device_ids": [...]}；缺省扫描全部设备。
    无保留消息（从未连过）且无新鲜遥测 = 离线。
    返回 online/offline 两组 + 每台设备的 probe 标记。

    已删除的设备（deleted=1）也会被扫描，如果检测到在线则自动恢复。
    """
    global _probe_last_ts
    now = time.time()
    if now - _probe_last_ts < _PROBE_COOLDOWN_S:
        remaining = round(_PROBE_COOLDOWN_S - (now - _probe_last_ts), 1)
        return JSONResponse(
            {"ok": False, "detail": f"探测过于频繁，请 {remaining}s 后重试"},
            status_code=429,
        )
    _probe_last_ts = now
    devs = db.list_devices()
    # 也包含已删除的设备，如果探测到在线则自动恢复
    deleted_rows = db.query("SELECT * FROM devices WHERE COALESCE(deleted,0)=1")
    all_devs = devs + [dict(r) for r in deleted_rows]
    body = body or {}
    want = body.get("device_ids")
    if not isinstance(want, list) or not want:
        ids = [d["id"] for d in all_devs]
    else:
        ids = [str(x) for x in want]

    started = time.time()
    online = _probe_status_ids(ids)

    # 恢复已删除但探测到在线的设备
    restored = []
    for d in all_devs:
        if d.get("deleted") and d["id"] in online:
            db.restore_device(d["id"])
            restored.append(d["id"])

    # 把新鲜度判定结果同步回 devices.online，让设备列表页与 probe 结果一致。
    for d in all_devs:
        if d["id"] not in ids:
            continue
        want_online = 1 if d["id"] in online else 0
        if d.get("online") != want_online:
            db.set_device_online(d["id"], want_online == 1)

    # 只返回未删除的设备（已恢复的也包含在内）
    groups: Dict[str, list] = {"online": [], "offline": []}
    for d in all_devs:
        if d["id"] not in ids:
            continue
        if d.get("deleted") and d["id"] not in restored:
            continue  # 已删除且未恢复的设备不返回
        rec = dict(d)
        rec["latest"] = db.latest_telemetry(d["id"])
        rec["probe"] = d["id"] in online
        groups["online" if d["id"] in online else "offline"].append(rec)
        # 写入设备接入快照（内网/外网根据 IP 自动分类），窗口外自动过期
        db.upsert_scan_snapshot(
            device_id=d["id"],
            name=d.get("name"),
            ip_addr=d.get("ip_addr"),
            online=d["id"] in online,
            fw_version=d.get("fw_version"),
            first_seen=d.get("first_seen"),
        )
    return {"ok": True, "mqtt_connected": bridge.connected,
            "probed": len(ids),
            "elapsed_s": round(time.time() - started, 2),
            "online": groups["online"], "offline": groups["offline"],
            "online_count": len(groups["online"]),
            "offline_count": len(groups["offline"]),
            "restored": restored}


@app.post("/api/devices/{device_id}/probe", dependencies=[Depends(require_user)])
def probe_one_device(device_id: str):
    """单设备在线状态刷新（设备管理页每张卡片右上角的「刷新」按钮）。

    与批量 probe 共用 _probe_status_ids，判定口径完全一致，但只扫这一台，
    响应更快，且不会连累其它设备的状态显示。
    回写 devices.online，使本卡片徽标与 /api/devices 返回的列表口径一致。
    """
    devs = [d for d in db.list_devices() if d["id"] == device_id]
    if not devs:
        raise HTTPException(status_code=404, detail="设备不存在")
    rec = devs[0]
    started = time.time()
    online = _probe_status_ids([device_id])
    want_online = device_id in online
    if bool(rec.get("online")) != want_online:
        db.set_device_online(device_id, want_online)
    rec["online"] = 1 if want_online else 0
    rec["probe"] = want_online
    rec["latest"] = db.latest_telemetry(device_id)
    rec["last_seen"] = db.query(
        "SELECT last_seen FROM devices WHERE id=?", (device_id,))[0]["last_seen"]
    # 单台刷新也写入快照
    db.upsert_scan_snapshot(
        device_id=device_id,
        name=rec.get("name"),
        ip_addr=rec.get("ip_addr"),
        online=want_online,
        fw_version=rec.get("fw_version"),
        first_seen=rec.get("first_seen"),
    )
    return {"ok": True, "mqtt_connected": bridge.connected,
            "elapsed_s": round(time.time() - started, 2),
            "online": want_online, "device": rec}
@app.post("/api/devices/batch-delete", dependencies=[Depends(require_admin)])
def batch_delete_devices(body: dict):
    """批量删除设备（局域网发现页勾选后）。body: {"device_ids": ["a","b"]}

    与 batch-register 对称：设备能批量注册，就应当能批量删除。
    单台删除走 db.delete_device，会连带清掉该设备的遥测/报警/阈值等关联数据。
    返回每台设备的删除结果，便于前端区分「已删除」与「本就不存在」。
    """
    ids = body.get("device_ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="device_ids 不能为空")
    existing = {str(d["id"]) for d in db.list_devices()}
    deleted, missing, errors = 0, 0, 0
    for raw in ids:
        did = str(raw).strip()
        if not did:
            continue
        if did not in existing:
            missing += 1
            continue
        try:
            db.delete_device(did)
            existing.discard(did)
            deleted += 1
        except Exception:  # noqa: BLE001
            log.exception("batch delete device %s failed", did)
            errors += 1
    return {"ok": True, "deleted": deleted, "not_found": missing,
            "errors": errors, "total": len(ids)}


# ================================================================ 设备网络接入
def _network_type(ip: str | None) -> str:
    """根据设备上报 IP 归类为内网/外网。RFC1918 私有地址 = internal，其余 = external。"""
    if not ip:
        return "unknown"
    ip = ip.strip()
    # BUG-14: 0.0.0.0 是占位符，不是真实外网地址
    if ip == "0.0.0.0":
        return "unknown"
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return "external"
        a, b = int(parts[0]), int(parts[1])
        if a == 10:
            return "internal"
        if a == 172 and 16 <= b <= 31:
            return "internal"
        if a == 192 and b == 168:
            return "internal"
        if a == 127:
            return "internal"
        if a == 169 and b == 254:  # link-local
            return "internal"
        return "external"
    except (ValueError, IndexError):
        return "external"


# ================================================================ 设备接入 / discovery
@app.get("/api/discover/devices", dependencies=[Depends(require_user)])
def devices_discover(refresh: bool = Query(False),
                     # 注意 pattern 是 ^($|...) 而非 ^(...)：必须放行空串。
                     # 前端历史代码传 network=（空串），若 pattern 不含 ^$，
                     # FastAPI 的 Query 校验在进入函数体之前就抛 422，
                     # 整个发现页会打不开。下方函数体再把空串归一为 "all"。
                     network: str = Query("all", pattern="^($|all|internal|external)$")):
    """设备接入：基于 telemetry 上报记录去重，与 devices 表比对。
    network=all|internal|external：按 RFC1918 私有地址分类，支持只扫描内网/外网设备。
    未传或传空串时等价于 all。
    """
    if not network:
        network = "all"
    _ = refresh

    # 数据源是 devices 表（设备清单），不是 telemetry 表。
    # 旧实现 INNER JOIN telemetry —— 而 telemetry 按 RAW_RETENTION_DAYS 清理，
    # 一旦清空，本端点就返回空列表，表现为「重新扫描没有任何反应」。
    devs = db.list_devices()
    # P1 #13: 过滤已删除设备
    devs = [d for d in devs if not d.get("deleted")]
    db_ids = {d["id"] for d in devs}
    # devices 表里没有、但 telemetry 里出现过的（未接入设备），也列出来供注册。
    # db_ids 保持不变，只放设备表里真实存在的 ID，用于区分 registered / unregistered。
    # 排除已软删除的设备: 它们的 telemetry 记录仍在, 但不应在发现页重新出现
    deleted_ids = {r["id"] for r in db.query(
        "SELECT id FROM devices WHERE COALESCE(deleted,0)=1")}
    extra_ids = [r["device_id"] for r in db.query(
        "SELECT DISTINCT device_id FROM telemetry LIMIT 500")]
    for eid in extra_ids:
        if eid not in db_ids and eid not in deleted_ids:
            devs.append({"id": eid, "name": None, "fw_version": None,
                         "ip_addr": None, "online": 0,
                         "first_seen": None, "last_seen": None})

    # online 状态直接取 devices 表的 online 字段（由 LWT 实时维护），
    # 不在这里做主动探测 —— 探测是阻塞的，会让「重新扫描」和切 tab 每次多等约 1 秒。
    # 需要真实状态时用设备管理页的「🔍 刷新状态」，或 POST /api/devices/probe。
    items = []
    for d in devs:
        did = d["id"]
        ip = d.get("ip_addr")
        # 在线判定与设备管理页 probe 同一口径（_is_dev_recent）：以服务器接收时刻
        # last_seen 为准。旧实现拿 telemetry.ts（设备自带时钟，可偏差数小时）去比，
        # 会把活设备显示成离线、死设备显示成在线。
        live = bool(d.get("online")) or _is_dev_recent(did)
        items.append({
            "device_id": did,
            "name": d.get("name"),
            "fw": d.get("fw_version"),
            "ip": ip,
            "last_seen": d.get("last_seen"),
            "online": live,
            "registered_at": d.get("first_seen"),
            "status": "registered",
            "network": _network_type(ip),
        })
    # 上面追加的 telemetry-only 设备其实没接入过，改回 unregistered 供勾选注册。
    for x in items:
        if x["device_id"] not in db_ids:
            x["status"] = "unregistered"

    # 网络分类过滤（前端按 内网/外网 分别扫描）。
    # network == "unknown"（未上报 IP）归入内网：这台 MQTT broker 上接入的设备
    # 本来就是局域网发现要找的那批，IP 缺失不能据此把它们从内网列表里丢掉。
    # 旧实现只保留 internal / external 两种，unknown 在两个 tab 里同时消失。
    internal_items = [x for x in items if x["network"] in ("internal", "unknown")]
    external_items = [x for x in items if x["network"] == "external"]
    filtered = items
    if network == "internal":
        filtered = internal_items
    elif network == "external":
        filtered = external_items
    return {"devices": filtered, "total": len(filtered),
            "network": network,
            "internal": len(internal_items),
            "external": len(external_items),
            "all": len(items),
            "registered": sum(1 for x in items if x["status"] == "registered"),
            "unregistered": sum(1 for x in items if x["status"] == "unregistered"),
            "refreshed": refresh}


@app.get("/api/patients/summary", dependencies=[Depends(require_user)])
def patients_summary():
    """返回所有患者概况 + 最新体征 + 设备在线状态。"""
    import sqlite3 as _sqlite3
    patients = icu.list_patients(limit=500)
    conn = icu._get_conn()
    conn.row_factory = _sqlite3.Row
    out = []
    vit_field_list = ["ecg_hr", "sp_o2", "rr_bpm", "sbp", "dbp", "temp_c", "glucose"]
    for p in patients:
        row = dict(p)
        latest = conn.execute(
            "SELECT ts, " + ", ".join(vit_field_list) + " FROM vitals "
            "WHERE patient_id=? ORDER BY ts DESC LIMIT 1", (row["id"],)
        ).fetchone()
        vitals_snapshot = None
        if latest:
            vitals_snapshot = {k: (latest[k] if latest[k] is not None else None) for k in vit_field_list}
            vitals_snapshot["ts"] = latest["ts"]
        row["vitals"] = vitals_snapshot
        devs = icu.devices_for_patient(row["id"])
        row["devices"] = [{"device_id": d["device_id"], "name": d.get("device_name"),
                           "role": d.get("role"), "online": bool(d.get("online")),
                           "fw": d.get("fw_version")} for d in devs]
        row["online_device_count"] = sum(1 for d in devs if d.get("online"))
        out.append(row)
    return {"patients": out, "total": len(out)}


@app.get("/api/settings/{key}", dependencies=[Depends(require_admin)])
def get_setting_route(key: str):
    """按 key 读取设置（原始字符串，不做类型转换）。"""
    rows = db.query("SELECT updated_at FROM app_settings WHERE key=?", (key,))
    updated_at = rows[0]["updated_at"] if rows else None
    return {"key": key, "value": icu.get_setting_raw(key), "updated_at": updated_at}


@app.get("/api/settings", dependencies=[Depends(require_admin)])
def list_settings():
    """列出当前配置值（原始字符串，admin）。"""
    raw = icu.list_settings_raw()
    keys = ["ai.enabled", "ai.provider", "ai.base_url", "ai.model", "ai.api_key", "ai.prompt",
            "wechat.corp_id", "wechat.agent_id", "wechat.secret", "wechat.webhook_url", "wechat.enabled",
            "wechat.token", "wechat.aes_key"]
    placeholders = ",".join(["?"] * len(keys))
    rows = db.query(f"SELECT key, updated_at FROM app_settings WHERE key IN ({placeholders})", tuple(keys))
    updated = {dict(r)["key"]: dict(r).get("updated_at") for r in rows}
    out = [{"key": k, "value": raw.get(k, ""), "updated_at": updated.get(k)} for k in keys]
    return {"settings": out}


@app.post("/api/settings", dependencies=[Depends(require_admin)])
def set_settings(body: Dict[str, Any]):
    """批量写入配置。支持 wechat.* 键：企业微信 corp_id/agent_id/secret 或 webhook_url。"""
    # BUG-33: window_minutes 必须为正整数
    wm = body.get("window_minutes")
    if wm is not None:
        try:
            wm_val = int(wm)
            if wm_val < 1 or wm_val > 1440:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "window_minutes 必须为 1-1440 的整数")
    for k, v in (body or {}).items():
        if k and not str(k).startswith("_"):
            icu.set_setting(str(k), str(v) if v is not None else "")
    return {"ok": True}


# ---------------------------------------------------------------- 提醒（语音/文字 + 企微）
WECHAT_WECHAT = "app_settings:wechat."


@app.post("/api/reminders/send", dependencies=[Depends(require_admin)])
def send_reminder(body: Dict[str, Any]):
    """医生向患者发提醒。可同时：① 推送到患者绑定设备（语音播报 + 屏幕显示）
    ② 通过企业微信 webhook 或 应用消息 通知医生自己。

    body: {
      "patient_id": "P001",      # P1 #20: 可选；有 device_id 时可不传
      "doctor_id": 1,            # 可选
      "doctor_name": "张三",     # 可选
      "device_id": "esp-xxx",    # 可选；省略则用患者第一台已绑设备
      "text": "下午 3 点复查血常规",
      "tts": true,               # 语音播报文字内容
      "wechat": true             # 企业微信推送
    }
    """
    pid = str(body.get("patient_id", "")).strip()
    did = str(body.get("device_id", "") or "").strip() or None
    # P1 #20: 有 device_id 但无 patient_id 时，从设备绑定反查患者
    if not pid and did:
        bound = db.query(
            "SELECT pd.patient_id, p.pid FROM patient_devices pd "
            "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=? LIMIT 1",
            (did,))
        if bound:
            pid = bound[0]["pid"]
    if not pid:
        raise HTTPException(400, "patient_id 或 device_id 至少传一个")
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text 必填")
    if len(text) > 512:
        raise HTTPException(400, "text 长度不能超过 512 字符")
    doctor_id = body.get("doctor_id")
    doctor_name = str(body.get("doctor_name", "") or "").strip() or None
    do_tts = bool(body.get("tts", True))
    do_wechat = bool(body.get("wechat", False))

    # 无 device_id 则取患者第一台已绑设备
    if not did:
        bound = db.query(
            "SELECT device_id FROM patient_devices WHERE patient_id=(SELECT id FROM patients WHERE pid=?) LIMIT 1",
            (pid,),
        )
        if bound:
            did = bound[0]["device_id"]

    sent_dev = 0
    sent_wx = 0
    tts_ok = True
    tts_err = ""
    wx_err = ""
    if do_tts and did:
        try:
            resp = _dispatch_reminder_to_device(did, text, pid)
            if resp and resp.get("ok"):
                sent_dev = 1
            else:
                tts_ok = False
                tts_err = (resp and resp.get("error")) or "未知错误"
        except Exception as e:  # noqa: BLE001
            tts_ok = False
            tts_err = str(e)
        # 无论 TTS 是否成功，都推送到设备屏幕 topic（供患者端屏幕显示）
        _broadcast_reminder_text(did, text)
    if do_wechat:
        sent_wx, wx_err = _send_wechat_reminder(text, pid, doctor_name, did)

    mid = db.remind_patient(
        patient_id=pid, device_id=did, doctor_id=doctor_id,
        doctor_name=doctor_name, text=text,
        sent_to_device=sent_dev, sent_to_wechat=sent_wx,
    )
    return {
        "ok": True, "reminder_id": mid,
        "patient_id": pid, "device_id": did,
        "tts": sent_dev == 1, "tts_error": None if tts_ok else (tts_err or "设备未连接或推送失败"),
        "wechat": sent_wx == 1, "wechat_error": wx_err or None,
    }


@app.get("/api/reminders", dependencies=[Depends(require_user)])
def list_reminders(patient_id: str = Query("", max_length=32),
                   limit: int = Query(100, ge=1, le=500)):
    return {"reminders": db.remind_list(patient_id, limit)}


def _dispatch_reminder_to_device(device_id: str, text: str, patient_id: str):
    """直接通过 MQTT 向设备下发语音播报文本（不再 HTTP 自调用，避免鉴权问题）。"""
    if not bridge.client or not bridge.connected:
        return {"ok": False, "error": "MQTT 未连接"}
    try:
        import paho.mqtt.client as mqtt
        payload = json.dumps({"text": text, "level": 0, "device_id": device_id}, ensure_ascii=False)
        topic = f"envmon/{device_id}/tts"
        res = bridge.client.publish(topic, payload, qos=1)
        ok = res.rc == mqtt.MQTT_ERR_SUCCESS
        return {"ok": ok, "topic": topic, "error": None if ok else f"MQTT publish rc={res.rc}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _broadcast_reminder_text(device_id: str, text: str) -> None:
    """推送文字到 envmon/{id}/reminder 主题——供患者端屏幕显示。
    不阻塞提醒下发（桥接未连接时静默跳过）。
    """
    try:
        if bridge.client and bridge.connected:
            bridge.client.publish(
                "envmon/" + device_id + "/reminder",
                json.dumps({"text": text}, ensure_ascii=False),
                qos=1,
            )
    except Exception:
        pass


# ---------------------------------------------------------------- 企业微信回调（医生→设备消息管道）
def _wecom_pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len

def _wecom_pkcs7_unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    return data[:-pad_len]

def _wecom_encrypt_msg(plain: str, key: str, recv: str) -> str:
    """AES-256-CBC 加密（企微回调要求）"""
    raw_key = base64.b64decode(key + "=")
    iv = raw_key[:16]
    msg_bytes = plain.encode("utf-8")
    random_bytes = struct.pack(">I", secrets.randbelow(0xFFFFFFFF))
    payload = random_bytes + struct.pack(">I", len(msg_bytes)) + msg_bytes + recv.encode("utf-8")
    padded = _wecom_pkcs7_pad(payload)
    enc = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-e", "-nopad",
         "-K", raw_key.hex(), "-iv", iv.hex()],
        input=padded, capture_output=True, check=True
    )
    return base64.b64encode(enc.stdout).decode("utf-8")

def _wecom_decrypt_msg(encrypted: str, key: str) -> str:
    """AES-256-CBC 解密（企微回调要求）"""
    raw_key = base64.b64decode(key + "=")
    iv = raw_key[:16]
    enc_bytes = base64.b64decode(encrypted)
    dec = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-d", "-nopad",
         "-K", raw_key.hex(), "-iv", iv.hex()],
        input=enc_bytes, capture_output=True, check=True
    )
    payload = _wecom_pkcs7_unpad(dec.stdout)
    msg_len = struct.unpack(">I", payload[16:20])[0]
    return payload[20:20 + msg_len].decode("utf-8")

@app.post("/api/wechat/callback")
async def wechat_callback(request: Request):
    """企业微信消息回调：医生在企微发消息 → 服务器解析 → MQTT 转发到设备屏幕。

    企微回调流程：
      1. 企微推送加密 XML 到本端点
      2. 服务器验证签名 + AES 解密
      3. 解析医生发消息（from user → message text）
      4. 查 doctors 表获取主管患者 → 获取绑定设备
      5. MQTT 发布到 envmon/{device_id}/reminder（设备屏幕显示）
      6. 返回 success 确认接收
    """
    from fastapi import Query
    msg_signature = request.query_params.get("msg_signature", "")
    timestamp = request.query_params.get("timestamp", "")
    nonce = request.query_params.get("nonce", "")

    # 获取企微配置
    corp = icu.get_setting_raw("wechat.corp_id") or ""
    agent = icu.get_setting_raw("wechat.agent_id") or ""
    secret = icu.get_setting_raw("wechat.secret") or ""
    token = icu.get_setting_raw("wechat.token") or ""
    aes_key = icu.get_setting_raw("wechat.aes_key") or ""
    # 回调加密 key 从 corp_secret 推导（简化模式，不依赖应用 token）
    # 实际部署时可添加独立 aes_key 配置
    if not aes_key:
        aes_key = secret[:43] + "="  # 企微标准 AESKey 格式

    if not corp or not token:
        return {"ok": False, "error": "wechat.corp_id / wechat.token 未配置"}

    body = await request.body()

    # 验证签名
    signature_calc = hashlib.sha1((
        token + timestamp + nonce + base64.b64encode(body).decode("utf-8")
    ).encode("utf-8")).hexdigest()

    if signature_calc != msg_signature:
        log.warning("WeChat callback signature mismatch: %s vs %s", signature_calc, msg_signature)
        return {"ok": False, "error": "签名验证失败"}

    # 解析 XML
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body)
        encrypt_tag = root.find("Encrypt")
        if not encrypt_tag or not encrypt_tag.text:
            return {"ok": False, "error": "XML 缺少 Encrypt 节点"}
        plain_xml = _wecom_decrypt_msg(encrypt_tag.text, aes_key)
        log.info("WeChat callback decrypted: %s", plain_xml[:200])
        msg_root = ET.fromstring(plain_xml.encode("utf-8"))
    except Exception as e:
        log.warning("WeChat callback XML parse/decrypt failed: %s", e)
        return {"ok": False, "error": f"解密失败: {str(e)}"}

    msg_type = msg_root.findtext("MsgType", "")
    content = msg_root.findtext("Content", "")
    sender = msg_root.findtext("FromUserName", "")

    if msg_type == "text" and content:
        log.info("WeChat doctor message from=%s: %s", sender, content[:50])
        # 查医生绑定的患者和设备
        # 链路：doctors.wechat_userid → patients.doctor → patient_devices
        patient_ids = db.query(
            "SELECT p.id AS patient_id, p.pid "
            "FROM patients p "
            "JOIN doctors d ON p.doctor = d.name "
            "WHERE d.wechat_userid = ?",
            (sender,)
        )
        for row in patient_ids:
            row_dict = dict(row) if not isinstance(row, dict) else row
            pid_int = row_dict.get("patient_id")
            pid_str = row_dict.get("pid")
            bound = db.query(
                "SELECT device_id FROM patient_devices WHERE patient_id=?", (pid_int,)
            )
            if bound:
                b0 = dict(bound[0]) if not isinstance(bound[0], dict) else bound[0]
                did = b0.get("device_id")
                if did:
                    _broadcast_reminder_text(did, content)
                    log.info("WeChat msg → device %s for patient %s", did, pid_str)
    return {"ok": True}


def _send_wechat_reminder(text: str, patient_id: str,
                          doctor_name: Optional[str], device_id: Optional[str]) -> tuple:
    """通过企业微信 webhook（或应用消息）发送提醒。

    两种模式（任选其一配置）：
      ① webhook_url: 群机器人 webhook，最简单。
      ② corp_id + agent_id + secret: 企业应用消息（可指定接收人的 userid）。

    返回 (sent:int, err:str)。
    """
    import urllib.request, urllib.error as ue
    enabled = icu.get_setting_raw("wechat.enabled") or ""
    if enabled not in ("1", "true", "True"):
        return 0, "企业微信未启用（wechat.enabled=0）"
    webhook = icu.get_setting_raw("wechat.webhook_url") or ""
    corp = icu.get_setting_raw("wechat.corp_id") or ""
    agent = icu.get_setting_raw("wechat.agent_id") or ""
    secret = icu.get_setting_raw("wechat.secret") or ""

    title = "[健康提醒]" + (doctor_name or "医生") + " 给 " + patient_id + ((". 设备 " + device_id) if device_id else "")
    content = title + "\n" + text

    try:
        if webhook and webhook.startswith("http"):
            payload = json.dumps({
                "msgtype": "text",
                "text": {"content": content},
            }, ensure_ascii=False).encode("utf-8")
            resp = urllib.request.urlopen(urllib.request.Request(webhook, data=payload,
                                                                headers={"Content-Type": "application/json"}),
                                         timeout=10)
            body = json.loads(resp.read().decode("utf-8") or "{}")
            if body.get("errcode", 0) != 0:
                code = body.get("errcode")
                desc = {93000: "企微 webhook URL 无效或未配置",
                        40001: "access_token 无效或已过期",
                        81013: "用户不在企业内",
                        45009: "接口调用频率超限",
                        60011: "不合法的secret",
                        40056: "IP 不在白名单内"}.get(code, "")
                msg = f"webhook errcode={code}"
                if desc:
                    msg += f" ({desc})"
                return 0, msg
            return 1, ""
        if corp and agent and secret:
            token_url = ("https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=" + corp +
                         "&corpsecret=" + secret)
            token_resp = urllib.request.urlopen(token_url, timeout=10)
            tok = json.loads(token_resp.read().decode("utf-8")).get("access_token")
            if not tok:
                return 0, "获取 access_token 失败"
            msg_url = "https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=" + tok
            payload = json.dumps({
                "touser": "@all",
                "msgtype": "text",
                "agentid": int(agent),
                "text": {"content": content},
            }, ensure_ascii=False).encode("utf-8")
            msg_resp = urllib.request.urlopen(msg_url, data=payload,
                                              headers={"Content-Type": "application/json"}, timeout=10)
            body = json.loads(msg_resp.read().decode("utf-8") or "{}")
            if body.get("errcode", 0) != 0:
                return 0, "应用消息 errcode=" + str(body.get("errcode"))
            return 1, ""
        return 0, "未配置 webhook_url 或 (corp_id+agent_id+secret)"
    except (ue.URLError, ValueError) as e:
        return 0, str(e)


@app.put("/api/settings", dependencies=[Depends(require_admin)])
def update_setting(body: SettingsUpdateIn):
    """写入单条设置。"""
    icu.set_setting(body.key, body.value)
    return {"ok": True, "key": body.key, "value": body.value}


@app.get("/api/realtime", dependencies=[Depends(require_user)])
def realtime(device: Optional[str] = None):
    devs = db.list_devices()
    out: List[dict] = []
    for d in devs:
        if device and d["id"] != device:
            continue
        last = db.latest_telemetry(d["id"])
        out.append({"device": d, "latest": last})
    return {"devices": out}


# ================================================================ 大屏（公开，无需登录，适合投屏）
@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/api/dashboard")
def dashboard_data():
    """大屏数据：每个患者最新体征 + 环境 + 报警。公开端点，供投屏刷新。"""
    import sqlite3 as _sqlite3
    conn = icu._get_conn()
    conn.row_factory = _sqlite3.Row
    patients = icu.list_patients(limit=200)
    vit_fields = ["ecg_hr", "sp_o2", "rr_bpm", "sbp", "dbp", "temp_c", "glucose"]
    env_fields = ["hum_pct", "pres_hpa"]
    out = []
    for p in patients:
        row = dict(p)
        # 最新体征
        latest = conn.execute(
            "SELECT ts, " + ", ".join(vit_fields + env_fields)
            + " FROM vitals WHERE patient_id=? ORDER BY ts DESC LIMIT 1", (row["id"],)
        ).fetchone()
        vit = None
        ts_age = None
        if latest:
            vit = {k: (latest[k] if latest[k] is not None else None) for k in vit_fields + env_fields}
            vit["ts"] = latest["ts"]
            # 计算数据新鲜度（秒）
            try:
                ts_age = (datetime.fromisoformat(latest["ts"].replace("Z", "+00:00"))
                          - datetime.now(timezone.utc)).total_seconds()
                ts_age = abs(int(ts_age))
            except Exception:
                ts_age = None
        # 最新未读报警（近 1h）
        alarms = icu.recent_alarms(row["id"], minutes=60) if hasattr(icu, "recent_alarms") else []
        row["vitals"] = vit
        row["age_sec"] = ts_age
        row["active_alarms"] = len(alarms)
        devs = icu.devices_for_patient(row["id"])
        row["online_devices"] = sum(1 for d in devs if d.get("online"))
        row["total_devices"] = len(devs)
        # 运行中医嘱摘要（供大屏/监护显示）
        active_orders = icu.orders_for_patient(row["id"])
        active_orders = [dict(o) for o in active_orders if o.get("status") == "active"][:5]
        row["orders"] = active_orders
        out.append(row)
    # 全局环境概览（所有设备最新 telemetry）
    env = []
    devs = db.list_devices()
    for d in devs:
        t = db.latest_telemetry(d["id"])
        if t:
            env.append({"device_id": d["id"], "name": d.get("name"),
                        "online": bool(d.get("online")),
                        "temp_c": t.get("temp_c"), "hum_pct": t.get("hum_pct"),
                        "pres_hpa": t.get("pres_hpa"), "ts": t.get("ts"),
                        "alarm_level": t.get("alarm_level")})
    return {"patients": out, "total": len(out), "environment": env,
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


@app.get("/api/history", dependencies=[Depends(require_user)])
def history(device: str = Query(...), hours: int = Query(24, ge=1, le=24 * 365)):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    if hours <= 3:
        rows = db.history_range(device,
                                start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        points = [{"ts": r["ts"], "t": r["temp_c"], "h": r["hum_pct"],
                   "p": r["pres_hpa"], "alarm": r["alarm_level"]} for r in rows]
        resolution = "raw"
    else:
        rows = db.query(
            """
            SELECT ts_minute, temp_avg, hum_avg, pres_hpa_avg, alarm_max
            FROM telemetry_1m
            WHERE device_id=? AND ts_minute>=?
            ORDER BY ts_minute ASC
            """,
            (device, start.strftime("%Y-%m-%dT%H:%M")),
        )
        points = [{"ts": r["ts_minute"] + ":00Z", "t": r["temp_avg"], "h": r["hum_avg"],
                   "p": r["pres_hpa_avg"], "alarm": r["alarm_max"]} for r in rows]
        resolution = "1m"
    return {"device": device, "resolution": resolution, "count": len(points),
            "points": points}


@app.get("/api/thresholds", dependencies=[Depends(require_user)])
def get_thresholds(device: str = "*"):
    th = db.get_thresholds(device)
    if not th:
        raise HTTPException(404, "no thresholds")
    th = dict(th)
    th["alarm_enabled"] = bool(th["alarm_enabled"])
    th["alarm_sound"] = bool(th["alarm_sound"])
    return th


@app.put("/api/thresholds", dependencies=[Depends(require_admin)])
def put_thresholds(body: ThresholdsIn):
    data = body.model_dump()
    device_id = data.pop("device_id")
    # BUG-03: 非全局设备必须先存在
    if device_id != "*" and not db.device_detail(device_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    data["alarm_enabled"] = int(data["alarm_enabled"])
    data["alarm_sound"] = int(data["alarm_sound"])
    db.save_thresholds(device_id, data)
    # 下发到设备
    if device_id == "*":
        pushed = [d["id"] for d in db.list_devices() if bridge.push_config(d["id"])]
    else:
        pushed = [device_id] if bridge.push_config(device_id) else []
    return {"ok": True, "device_id": device_id, "pushed_to": pushed}


@app.get("/api/alarms", dependencies=[Depends(require_user)])
def alarms(device: Optional[str] = None, limit: int = Query(50, le=500),
         level: Optional[int] = Query(None, ge=0, le=2, description="报警级别 0=正常 1=预警 2=报警")):
    return {"alarms": db.list_alarms(device, limit)}


# ================================================================ AI 报警分析
class AiSettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    timeout: Optional[int] = Field(default=None, ge=5, le=300)
    max_tokens: Optional[int] = Field(default=None, ge=50, le=8192)
    system_prompt: Optional[str] = None


@app.get("/api/ai/settings")
def ai_settings(user: Dict = Depends(require_admin)):
    """读取 AI 模型接入配置（管理员）。"""
    raw = icu.list_settings_raw()
    keys = ["ai.enabled", "ai.provider", "ai.base_url", "ai.model",
            "ai.api_key", "ai.timeout", "ai.max_tokens", "ai.system_prompt"]
    return {
        "ai_settings": {k: ("******" if k == "ai.api_key" and raw.get(k, "") else raw.get(k, "")) for k in keys},
        "providers": [
            {"value": "openai", "label": "OpenAI (api.openai.com)"},
            {"value": "deepseek", "label": "DeepSeek (api.deepseek.com)"},
            {"value": "qwen", "label": "通义千问 Qwen / 百炼"},
            {"value": "gemini", "label": "Google Gemini OpenAI 兼容"},
            {"value": "ollama", "label": "Ollama (本机/局域网)"},
            {"value": "custom", "label": "自定义 OpenAI 兼容"},
        ],
    }


@app.post("/api/ai/settings")
def save_ai_settings(body: AiSettingsPatch, user: Dict = Depends(require_admin)):
    changes: List[str] = []
    m: Dict[str, Any] = {}
    if body.enabled is not None:
        m["ai.enabled"] = "1" if body.enabled else "0"
        changes.append("enabled")
    for k_name, key in [
        ("provider", "ai.provider"), ("base_url", "ai.base_url"),
        ("model", "ai.model"), ("api_key", "ai.api_key"),
        ("system_prompt", "ai.system_prompt"),
    ]:
        if getattr(body, k_name) is not None:
            m[key] = str(getattr(body, k_name))
            changes.append(k_name)
    if body.timeout is not None:
        m["ai.timeout"] = str(body.timeout)
        changes.append("timeout")
    if body.max_tokens is not None:
        m["ai.max_tokens"] = str(body.max_tokens)
        changes.append("max_tokens")
    for k, v in m.items():
        icu.set_setting(k, v)
    return {"ok": True, "updated": changes, "keys": list(m.keys())}


@app.post("/api/wechat/test", dependencies=[Depends(require_admin)])
def test_wechat(body: Dict[str, Any]):
    """企业微信配置测试：发一条文本消息到已配置的 webhook 或应用消息通道。"""
    text = (body or {}).get("text") or "envmon 企微配置测试消息"
    sent, err = _send_wechat_reminder(text, patient_id="TEST",
                                       doctor_name="系统测试", device_id=None)
    if sent:
        return {"ok": True, "detail": "消息已推送到企业微信"}
    return {"ok": False, "detail": err or "推送失败"}


@app.post("/api/ai/test")
def ai_test_connection(user: Dict = Depends(require_admin)):
    """测试当前配置的模型是否可达。"""
    try:
        from . import ai_client
        content, err, usage = ai_client.test_connection(icu.list_settings_raw())
        # BUG-38: 错误消息转中文
        err_msg = None
        if err:
            if "Connection refused" in err or "Errno 111" in err:
                err_msg = "无法连接 AI 服务，请检查 ollama 是否已启动"
            elif "Connection reset" in err:
                err_msg = "AI 服务连接被拒绝"
            elif "timed out" in err or "Timeout" in err:
                err_msg = "AI 服务响应超时"
            else:
                err_msg = "AI 连接失败: " + err
        if not err_msg and not content:
            err_msg = "AI 返回空响应，请检查 model 名称、api_key 和 base_url 是否正确"
        return {
            "ok": bool(content),
            "content": content[:500],
            "error": err_msg,
            "usage": usage,
        }
    except Exception as e:
        return {"ok": False, "error": "AI 测试异常: " + str(e), "content": "", "usage": None}


@app.get("/api/ai/analyses")
def ai_analyses(device: Optional[str] = None,
                limit: int = Query(50, ge=1, le=200),
                user: Dict = Depends(require_user)):
    rows = db.list_ai_analyses(device, limit)
    return {"analyses": rows, "total": len(rows)}


# ================================================================ HL7 v2.x 解析
# OBX-3 标识符到 telemetry/vitals 字段名的映射表。
# 常见标识符（不区分大小写）覆盖主流监护设备输出。
_HL7_OBX_MAP: Dict[str, str] = {
    "temp": "temp_c", "temperature": "temp_c", "体温": "temp_c",
    "hum": "hum_pct", "humidity": "hum_pct", "湿度": "hum_pct",
    "pres": "pres_hpa", "pressure": "pres_hpa", "气压": "pres_hpa",
    "spo2": "sp_o2", "sp_o2": "sp_o2", "血氧": "sp_o2",
    "pr": "pr_hr", "hr": "pr_hr", "pulse": "pr_hr", "心率": "pr_hr",
    "rr": "rr_bpm", "rr_bpm": "rr_bpm", "呼吸": "rr_bpm",
    "sbp": "sbp", "dbp": "dbp", "map": "map_bp", "nibp_s": "sbp", "nibp_d": "dbp",
    "ecg_hr": "ecg_hr", "etco2": "etco2",
}


def _parse_hl7(text: str) -> Dict[str, Any]:
    """解析 HL7 v2.x 消息文本，返回结构化 dict。

    支持 ORU^R01（观察结果）消息类型，提取 MSH / PID / OBX 段。
    OBX 观察值通过 _HL7_OBX_MAP 映射到 temp_c/hum_pct/pres_hpa/sp_o2/pr_hr 等字段。

    返回示例::

        {
            "message_type": "ORU^R01",
            "device_id": "ESP32-001",
            "patient": {"pid": "12345", "name": "张三", "bed": "ICU-01"},
            "observations": [{"code": "Temp", "field": "temp_c", "value": 36.5, "unit": "C"}],
            "mapped": {"temp_c": 36.5, "sp_o2": 98},
        }
    """
    # HL7 段以行分隔（\\r 或 \\r\\n），字段以竖线 | 分隔
    lines = text.replace("\r\n", "\r").replace("\n", "\r").split("\r")
    segments: Dict[str, List[List[str]]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        fields = line.split("|")
        seg_id = fields[0].upper() if fields else ""
        segments.setdefault(seg_id, []).append(fields)

    result: Dict[str, Any] = {
        "message_type": "",
        "device_id": None,
        "patient": {},
        "observations": [],
        "mapped": {},
    }

    # MSH 段 —— 消息头
    msh_rows = segments.get("MSH", [])
    if msh_rows:
        msh = msh_rows[0]
        # MSH-3 发送应用（设备标识）、MSH-4 发送设施、MSH-9 消息类型
        if len(msh) > 2 and msh[2]:
            result["device_id"] = msh[2]
        elif len(msh) > 3 and msh[3]:
            result["device_id"] = msh[3]
        if len(msh) > 8:
            result["message_type"] = msh[8]

    # PID 段 —— 患者标识
    pid_rows = segments.get("PID", [])
    if pid_rows:
        pid = pid_rows[0]
        patient: Dict[str, str] = {}
        if len(pid) > 3:
            patient["pid"] = pid[3].split("^")[0] if pid[3] else ""
        if len(pid) > 5:
            # PID-5 患者姓名，格式：姓^名
            patient["name"] = pid[5].replace("^", "") if pid[5] else ""
        if len(pid) > 18:
            # PID-18 床号（部分系统用 PID-3 的访问号）
            patient["bed"] = pid[18] if pid[18] else ""
        result["patient"] = patient

    # OBX 段 —— 观察值
    for obx in segments.get("OBX", []):
        if len(obx) < 6:
            continue
        # OBX-3 观察标识、OBX-5 观察值、OBX-6 单位
        obs_id = obx[3] if len(obx) > 3 else ""
        obs_val = obx[5] if len(obx) > 5 else ""
        obs_unit = obx[6] if len(obx) > 6 else ""
        if not obs_val:
            continue
        # 标识符可能带 ^ 分隔的子字段（如 TEMP^BODY^L），取第一个
        obs_key = obs_id.split("^")[0].strip().lower() if obs_id else ""
        field_name = _HL7_OBX_MAP.get(obs_key)
        obs_entry: Dict[str, Any] = {
            "code": obs_id,
            "field": field_name or obs_key,
            "value": obs_val,
            "unit": obs_unit,
        }
        result["observations"].append(obs_entry)
        if field_name:
            try:
                val = float(obs_val)
                result["mapped"][field_name] = val
            except (ValueError, TypeError):
                pass

    return result




@app.post("/api/telemetry")
async def telemetry_upload(request: Request):
    """HTTP 遥测上报端点（免认证，设备直接 POST）。

    兼容固件 MQTT 格式: {"device_id":"xxx","t":25.5,"h":60,"p":1013,"rssi":-50,...}
    也接受标准字段名: {"device_id":"xxx","temp_c":25.5,"hum_pct":60,"pres_hpa":1013,...}
    自动注册设备，存遥测 + 体征（如有），创建监护记录。
    """
    try:
        data = await request.json()
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="replace").strip()
        if not raw:
            raise HTTPException(400, "请求体为空")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(400, "无法解析 JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON 必须是对象")
    device_id = data.get("device_id") or data.get("id")
    if not device_id:
        raise HTTPException(400, "缺少 device_id 或 id")
    device_id = str(device_id)

    # 自动注册设备
    ip_addr = data.get("ip") or data.get("ip_addr") or request.client.host if request.client else None
    fw = data.get("fw") or data.get("fw_version")
    db.upsert_device(device_id, fw_version=fw, ip_addr=ip_addr)
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)

    # 提取环境数据（兼容 t/h/p 和 temp_c/hum_pct/pres_hpa）
    temp = data.get("t") or data.get("temp_c") or data.get("temp")
    hum = data.get("h") or data.get("hum_pct") or data.get("hum")
    pres = data.get("p") or data.get("pres_hpa") or data.get("pres")
    rssi = data.get("rssi")
    seq = data.get("seq")
    ts_in = data.get("ts")

    # 存遥测
    try:
        temp_f = float(temp) if temp is not None else None
        hum_f = float(hum) if hum is not None else None
        pres_f = float(pres) if pres is not None else None
        level, reason = check_alarm(device_id, temp_f, hum_f, pres_f)
        db.insert_telemetry(device_id, temp_f, hum_f, pres_f, rssi, level,
                            data.get("heap"), ts=ts_in, seq=seq)
    except Exception as e:
        log.warning("telemetry_upload: insert_telemetry failed: %s", e)

    # 广播遥测
    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": device_id,
        "data": {"t": temp, "h": hum, "p": pres, "rssi": rssi},
        "ts": ts_in or db.utcnow(),
    })

    # 如果载荷也含体征字段，走体征入库流程
    if _looks_like_vitals(data):
        try:
            handle_vitals(device_id, data)
        except Exception as e:
            log.warning("telemetry_upload: handle_vitals failed: %s", e)

    # 自动创建监护记录
    try:
        _auto_ensure_session(device_id)
    except Exception:
        pass

    return {"ok": True, "device_id": device_id}




@app.post("/api/vitals")
async def vitals_upload(request: Request):
    """ESP8266 MAX30102 固件兼容端点（免 admin 认证，设备直接上报）。

    接受固件格式: {"device_id":"xxx","hr":72,"spo2":98,"pr_hr":72,"ecg_hr":72,"sp_o2":98,...}
    同时兼容旧格式: {"id":"xxx","hr":72,"spo2":98}
    自动注册设备，若设备已绑定患者则写入 vitals 表。
    """
    try:
        data = await request.json()
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="replace").strip()
        if not raw:
            raise HTTPException(400, "请求体为空")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(400, "无法解析 JSON")

    if not isinstance(data, dict):
        raise HTTPException(400, "JSON 必须是对象")

    # 兼容 device_id 和 id 两种字段名
    device_id = data.get("device_id") or data.get("id")
    if not device_id:
        raise HTTPException(400, "缺少 device_id 或 id")

    device_id = str(device_id)
    import re as _re
    if not _re.match(r'^[A-Za-z0-9_-]{1,32}$', device_id):
        raise HTTPException(400, "device_id 格式无效")

    # 自动注册设备
    db.ensure_device(device_id)
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)

    # 提取体征字段（兼容多种命名）
    hr = data.get("pr_hr") or data.get("ecg_hr") or data.get("hr")
    spo2 = data.get("sp_o2") or data.get("spo2")
    rr = data.get("rr_bpm")
    temp = data.get("temp_c")
    sbp = data.get("sbp")
    dbp = data.get("dbp")
    glucose = data.get("glucose")

    vital_vals = {}
    if hr is not None:
        try:
            vital_vals["pr_hr"] = float(hr)
            vital_vals["ecg_hr"] = float(hr)
        except (TypeError, ValueError):
            pass
    if spo2 is not None:
        try:
            vital_vals["sp_o2"] = float(spo2)
        except (TypeError, ValueError):
            pass
    if rr is not None:
        try:
            vital_vals["rr_bpm"] = float(rr)
        except (TypeError, ValueError):
            pass
    if temp is not None:
        try:
            vital_vals["temp_c"] = float(temp)
        except (TypeError, ValueError):
            pass
    if sbp is not None:
        try:
            vital_vals["sbp"] = float(sbp)
        except (TypeError, ValueError):
            pass
    if dbp is not None:
        try:
            vital_vals["dbp"] = float(dbp)
        except (TypeError, ValueError):
            pass
    if glucose is not None:
        try:
            vital_vals["glucose"] = float(glucose)
        except (TypeError, ValueError):
            pass

    # 若设备已绑定患者，写入 vitals 表
    from .icu import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT p.id AS patient_id, p.pid AS pid FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchall()

    ts = icu._now()
    if rows and vital_vals:
        target = rows[0]
        try:
            icu.insert_vital(
                target["patient_id"], ts, "esp8266",
                source_device=device_id,
                **vital_vals,
            )
            # 自动创建监护记录（若该患者无活跃会话）
            try:
                _ensure_monitor_session(target["patient_id"], device_id)
            except Exception:
                pass
            # 检查体征异常并记录报警
            try:
                _check_vital_alarms(device_id, target["pid"], data)
            except Exception:
                pass
            hub.broadcast_threadsafe({
                "type": "vital", "patient_id": target["patient_id"],
                "pid": target["pid"], "ts": ts, "source": "esp8266",
            })
        except Exception as e:  # noqa: BLE001
            log.warning("vitals_upload: insert_vital failed: %s", e)

    # 也存 telemetry（环境数据如果有）
    temp_env = data.get("temp_c") or data.get("temp")
    hum_env = data.get("hum_pct") or data.get("hum")
    pres_env = data.get("pres_hpa") or data.get("pres")
    rssi = data.get("rssi")
    if temp_env is not None or hum_env is not None or pres_env is not None:
        try:
            t_f = float(temp_env) if temp_env is not None else None
            h_f = float(hum_env) if hum_env is not None else None
            p_f = float(pres_env) if pres_env is not None else None
            db.insert_telemetry(device_id, t_f, h_f, p_f, rssi)
            # 检查环境报警
            level, reason = check_alarm(device_id, t_f, h_f, p_f)
            record_alarm_transition(device_id, level, reason, t_f, h_f, p_f)
        except Exception:
            pass

    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": device_id,
        "data": {"hr": hr, "spo2": spo2, "rssi": rssi},
        "ts": ts,
    })

    return {"ok": True, "device_id": device_id, "vitals": bool(vital_vals)}


@app.post("/api/ingest", dependencies=[Depends(require_admin)])
async def ingest(request: Request):
    """HTTP 数据接入通道，支持 JSON 和 HL7 v2.x 文本两种格式。

    - Content-Type: application/json
        {"device_id":"xxx","temp_c":25,"hum_pct":60,"pres_hpa":1013}
        或 {"device_id":"xxx","temp":25,"hum":60,"pres":1013,"rssi":-55}
    - Content-Type: text/plain
        HL7 v2.x 文本（ORU^R01），解析 OBX 段并映射到遥测字段

    设备不存在时自动注册；解析后自动创建 telemetry 记录。
    """
    content_type = (request.headers.get("content-type") or "").lower()
    raw_body = (await request.body()).decode("utf-8", errors="replace").strip()
    if not raw_body:
        raise HTTPException(400, "请求体为空")

    device_id: Optional[str] = None
    temp: Optional[float] = None
    hum: Optional[float] = None
    pres: Optional[float] = None
    rssi: Optional[int] = None
    source = "json"
    vital_fields: Dict[str, Any] = {}  # P1 #17: JSON/HL7 都可能携带体征

    if "text/plain" in content_type or raw_body.startswith("MSH|"):
        # —— HL7 v2.x 文本格式 ——
        source = "hl7"
        parsed = _parse_hl7(raw_body)
        device_id = parsed.get("device_id")
        mapped = parsed.get("mapped", {})
        temp = mapped.get("temp_c")
        hum = mapped.get("hum_pct")
        pres = mapped.get("pres_hpa")
        # HL7 消息不包含 rssi，保持 None
        # 若映射到 sp_o2 / pr_hr 等 vitals 字段，也尝试写入 patient_vitals
        vital_fields = {k: v for k, v in mapped.items()
                        if k in ("sp_o2", "pr_hr", "ecg_hr", "rr_bpm",
                                 "etco2", "sbp", "dbp", "map_bp")}
        hl7_patient = parsed.get("patient", {})
        hl7_pid = hl7_patient.get("pid", "")
        if vital_fields and hl7_pid:
            try:
                p = icu.patient_by_pid(hl7_pid)
                if p:
                    icu.insert_vital(
                        p["id"], icu._now(), "hl7",
                        source_device=device_id or "",
                        **vital_fields,
                    )
                    # 自动创建监护记录（若该患者无活跃会话）
                    try:
                        _ensure_monitor_session(p["id"], device_id or "")
                    except Exception:
                        pass
                    # 检查体征异常并记录报警
                    try:
                        _check_vital_alarms(device_id or "", hl7_pid, vital_fields)
                    except Exception:
                        pass
                    hub.broadcast_threadsafe({
                        "type": "vital", "patient_id": p["id"], "pid": hl7_pid,
                        "ts": icu._now(), "source": "hl7",
                    })
            except Exception as e:  # noqa: BLE001
                log.warning("ingest: HL7 vital insert failed: %s", e)
    else:
        # —— JSON 格式 ——
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(400, "无法解析 JSON 请求体")
        if not isinstance(data, dict):
            raise HTTPException(400, "JSON 请求体必须是对象")
        device_id = data.get("device_id")
        # 兼容两种字段命名：temp_c / temp，hum_pct / hum，pres_hpa / pres
        temp = data.get("temp_c", data.get("temp"))
        hum = data.get("hum_pct", data.get("hum"))
        pres = data.get("pres_hpa", data.get("pres"))
        rssi = data.get("rssi")
        # P1 #17: 提取体征字段（pr_hr/sp_o2/rr_bpm/etco2 等）
        _VITAL_KEYS = ("pr_hr", "sp_o2", "rr_bpm", "etco2", "ecg_hr", "sbp", "dbp", "map_bp")
        vital_fields = {k: data.get(k) for k in _VITAL_KEYS if data.get(k) is not None}

    if not device_id:
        raise HTTPException(400, "缺少 device_id")
    
    # BUG-29: device_id 字符校验（与 /api/devices 统一）
    import re as _re
    if not _re.match(r'^[A-Za-z0-9_-]{1,32}$', device_id):
        raise HTTPException(400, "device_id 仅允许字母数字下划线和连字符，1-32 字符")
    
    # BUG-27/28/30: 数值范围校验
    import math
    for val, name, lo, hi in [(temp, "temp_c", -50, 150),
                              (hum, "hum_pct", 0, 100),
                              (pres, "pres_hpa", 300, 1300)]:
        if val is not None:
            if isinstance(val, str):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    raise HTTPException(400, f"{name}={val} 不是有效数字")
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                raise HTTPException(400, f"{name} 不能为 NaN/Infinity")
            if val < lo or val > hi:
                raise HTTPException(400, f"{name}={val} 超出范围 [{lo},{hi}]")
    
    # BUG-30: 至少提供一个测量值（HL7 仅含 vitals 时跳过此检查）
    if temp is None and hum is None and pres is None and rssi is None:
        if source == "hl7":
            pass  # HL7 可能只含 sp_o2/pr_hr 等 vitals，无遥测字段
        else:
            raise HTTPException(400, "至少提供一个测量值 (temp_c/hum_pct/pres_hpa/rssi)")
    
    # 设备不存在时自动登记（只保证记录存在，不碰 online/last_seen）
    db.ensure_device(device_id)

    level, reason = check_alarm(device_id, temp, hum, pres)
    db.insert_telemetry(device_id, temp, hum, pres, rssi, level, None)
    # 数据写入后同步刷新在线与最近上报时间（与 handle_telemetry 一致）
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)
    record_alarm_transition(device_id, level, reason, temp, hum, pres)

    # P1 #17: 若携带体征字段且设备已关联患者，写入 vitals 表
    if vital_fields and source == "json":
        try:
            rows = db.query_locked(
                "SELECT pd.patient_id, p.pid FROM patient_devices pd "
                "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
                (device_id,))
            if rows:
                r0 = dict(rows[0])
                pid = int(r0["patient_id"])
                pid_str = r0.get("pid") or str(pid)
                icu.insert_vital(pid, db.utcnow(), "ingest",
                                 source_device=device_id, **vital_fields)
                # 自动创建监护记录（若该患者无活跃会话）
                try:
                    _ensure_monitor_session(pid, device_id)
                except Exception:
                    pass
                # 检查体征异常并记录报警
                try:
                    _check_vital_alarms(device_id, pid_str, vital_fields)
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            log.warning("ingest: vital insert failed for %s: %s", device_id, e)

    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": device_id,
        "data": {"t": temp, "h": hum, "p": pres,
                 "rssi": rssi, "alarm": level, "source": source},
        "ts": db.utcnow(),
    })
    return {"ok": True, "alarm": level, "source": source, "device_id": device_id}


@app.post("/api/hl7/parse", dependencies=[Depends(require_user)])
async def hl7_parse(request: Request):
    """解析 HL7 v2.x 消息文本，返回结构化 JSON。

    请求体为 HL7 原始文本（Content-Type: text/plain 或任意文本）。
    支持 ORU^R01 消息类型，提取 MSH/PID/OBX 段。
    """
    raw = await request.body()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(400, "请求体为空")
    return _parse_hl7(text)


# ---------- 临床数据 Webhook（外部系统 → EnvMon）----------
@app.post("/api/ingest/clinical", dependencies=[Depends(require_admin)])
async def ingest_clinical(request: Request):
    """外部系统 (HIS/LIS/PACS) 推送临床数据的通用 webhook。

    请求体 JSON 格式：
    {
      "pid": "P001",              // 患者编号（必填）
      "data_type": "order",       // order / lab / exam / io（必填）
      "payload": { ... }          // 对应类型的数据（必填）
    }

    data_type=payload 对应字段：
    - order: order_no, drug_name, dosage, route, start_ts, end_ts, rate_mlph, operator
    - lab:   source, item_code, item_name, value, unit, ref_min, ref_max, result_ts, critical
    - exam:  source, exam_type, exam_name, result, report_url, operator, exam_ts
    - io:    direction, kind, amount_ml, amount_g, sub_type, route, note, source, operator, ts
    - vital: source, ts, ecg_hr, sp_o2, rr_bpm, sbp, dbp, temp_c, glucose, ... (任意体征字段)

    写入 DB 后自动广播 WebSocket，监护界面实时刷新。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "无法解析 JSON 请求体")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    pid = body.get("pid")
    data_type = body.get("data_type")
    payload = body.get("payload")
    if not pid or not data_type or not payload:
        raise HTTPException(422, "必填字段: pid, data_type, payload")
    if not isinstance(payload, dict):
        raise HTTPException(422, "payload 必须是 JSON 对象")

    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, f"患者 {pid} 不存在")

    if data_type == "order":
        oid = icu.order_insert(
            p["id"], payload.get("source", "his"),
            payload.get("order_no"), payload.get("drug_name"),
            payload.get("dosage"), payload.get("route"),
            payload.get("start_ts"), payload.get("end_ts"),
            payload.get("rate_mlph"), operator=payload.get("operator"),
        )
        hub.broadcast_threadsafe({"type": "order", "patient_id": p["id"], "pid": pid, "order_id": oid})
        return {"ok": True, "data_type": "order", "id": oid}

    elif data_type == "lab":
        lid = icu.lab_result_insert(
            p["id"], payload.get("source", "lis"),
            payload.get("item_code"), payload.get("item_name"),
            payload.get("value"), payload.get("unit"),
            payload.get("ref_min"), payload.get("ref_max"),
            payload.get("result_ts"), 1 if payload.get("critical") else 0,
        )
        hub.broadcast_threadsafe({"type": "lab", "patient_id": p["id"], "pid": pid, "lab_id": lid})
        return {"ok": True, "data_type": "lab", "id": lid}

    elif data_type == "exam":
        eid = icu.exam_insert(
            p["id"], payload.get("source", "pacs"),
            payload.get("exam_type"), payload.get("exam_name"),
            payload.get("result", ""), payload.get("report_url", ""),
            payload.get("operator"), payload.get("exam_ts"),
        )
        hub.broadcast_threadsafe({"type": "exam", "patient_id": p["id"], "pid": pid, "exam_id": eid})
        return {"ok": True, "data_type": "exam", "id": eid}

    elif data_type == "io":
        direction = payload.get("direction")
        if direction not in ("in", "out"):
            raise HTTPException(422, "io: direction 必须为 in 或 out")
        if not payload.get("kind"):
            raise HTTPException(422, "io: kind 必填")
        rid = icu.add_io_log(
            p["id"], direction, payload["kind"],
            payload.get("amount_ml"), payload.get("amount_g"),
            payload.get("sub_type"), payload.get("route"),
            payload.get("note"), payload.get("source", "external"),
            payload.get("operator"), payload.get("ts"), payload.get("unique_id"),
        )
        hub.broadcast_threadsafe({"type": "io", "patient_id": p["id"], "pid": pid, "io_id": rid})
        return {"ok": True, "data_type": "io", "id": rid}

    elif data_type == "vital":
        ts = payload.get("ts") or icu._now()
        vital_fields = {k: v for k, v in payload.items()
                        if k in ("sp_o2", "pr_hr", "ecg_hr", "ecg_st", "rr_bpm",
                                 "etco2", "sbp", "dbp", "map_bp", "ibp",
                                 "temp_c", "glucose", "hum_pct", "pres_hpa",
                                 "k_mmol", "na_mmol", "cl_mmol", "ca_mmol",
                                 "glucose_lab", "lactate", "ph", "pco2",
                                 "po2", "hco3", "be") and v is not None}
        if not vital_fields:
            raise HTTPException(422, "vital: payload 中无有效体征字段")
        icu.insert_vital(
            p["id"], ts, payload.get("source", "external"),
            source_device=payload.get("source_device") or None,
            **vital_fields,
        )
        try:
            _ensure_monitor_session(p["id"], payload.get("source_device") or "")
        except Exception:
            pass
        try:
            _check_vital_alarms(payload.get("source_device") or "", pid, vital_fields)
        except Exception:
            pass
        hub.broadcast_threadsafe({"type": "vital", "patient_id": p["id"], "pid": pid, "ts": ts, "source": payload.get("source", "external")})
        return {"ok": True, "data_type": "vital"}

    else:
        raise HTTPException(422, f"未知 data_type: {data_type}，支持: order/lab/exam/io/vital")


@app.post("/api/devices/{device_id}/push-config", dependencies=[Depends(require_admin)])
def push_config(device_id: str):
    ok = bridge.push_config(device_id)
    return {"ok": ok}


# ================================================================ OTA
from fastapi import UploadFile, File
from fastapi.responses import Response, JSONResponse

@app.get("/api/ota/version")
def ota_version():
    """返回当前最新版本元数据（无需登录）。"""
    latest = db.ota_get_latest()
    if not latest:
        raise HTTPException(status_code=404, detail="no firmware uploaded")
    return {
        "version": latest["version"],
        "size": latest["size"],
        "sha256": latest["sha256"],
        "id": latest["id"],
        "uploaded": latest["uploaded"],
    }

@app.get("/api/ota/image")
def ota_image():
    """返回当前最新版本固件二进制。"""
    latest = db.ota_get_latest()
    if not latest:
        raise HTTPException(status_code=404, detail="no firmware uploaded")
    bin_data = db.ota_get_binary(latest["id"])
    if not bin_data:
        raise HTTPException(status_code=404, detail="binary not found")
    return Response(
        content=bin_data,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(latest["size"]),
            "Content-Disposition": f'attachment; filename="envmon-{latest["version"]}.bin"',
            "X-OTA-Version": latest["version"],
            "X-OTA-SHA256": latest["sha256"],
        },
    )

@app.get("/api/ota/list", dependencies=[Depends(require_admin)])
def ota_list():
    return {"images": db.ota_list()}

@app.post("/api/ota/upload", dependencies=[Depends(require_admin)])
async def ota_upload(file: UploadFile = File(...), version: str = Query(...)):
    """上传固件 bin 并标记为 latest。校验 sha256。"""
    content = await file.read()
    import hashlib
    sha = hashlib.sha256(content).hexdigest()
    if len(content) < 1024:
        raise HTTPException(status_code=400, detail="file too small")
    oid = db.ota_upload(version, sha, content)
    return {"ok": True, "id": oid, "version": version, "size": len(content), "sha256": sha}

@app.delete("/api/ota/{image_id}", dependencies=[Depends(require_admin)])
def ota_delete(image_id: int):
    db.ota_delete(image_id)
    return {"ok": True}

@app.post("/api/ota/push/{device_id}", dependencies=[Depends(require_admin)])
def ota_push(device_id: str):
    """通过 MQTT 触发设备立即检查 OTA。"""
    payload = {"action": "check"}
    latest = db.ota_get_latest()
    if latest:
        payload["version"] = latest["version"]
        payload["sha256"] = latest["sha256"]
    topic = f"envmon/{device_id}/ota"
    try:
        import paho.mqtt.client as mqtt
        if bridge.client and bridge.connected:
            res = bridge.client.publish(topic, __import__("json").dumps(payload), qos=1)
            return {"ok": res.rc == mqtt.MQTT_ERR_SUCCESS, "topic": topic}
        return {"ok": False, "topic": topic, "error": "mqtt offline"}
    except Exception as e:
        return {"ok": False, "topic": topic, "error": str(e)}


# ================================================================ TTS 语音合成
from fastapi import UploadFile, File
from fastapi.responses import Response, JSONResponse


@app.get("/api/tts/status")
def tts_status():
    """查询 TTS 服务状态。"""
    return {
        "enabled": tts_mod.is_enabled(),
        "host": tts_mod.TTS_HOST,
        "port": tts_mod.TTS_PORT,
        "voice": tts_mod.TTS_VOICE,
    }


@app.get("/api/tts/speak")
@app.post("/api/tts/speak")
async def tts_speak(request: Request, body: dict = None):
    """文本转语音：调用 Piper 本地合成，返回 WAV 音频。

    固件通过 GET + query 参数调用（`GET /api/tts/speak?text=...`），
    前端/脚本用 POST + JSON body 调用（`{"text":"..."}`）。
    两种形式都接受，否则固件端 HTTP 播放器会收到 405 拿不到 WAV。
    返回: audio/wav 二进制流
    """
    text = ""
    voice = None
    if body:
        text = str(body.get("text", "")).strip()
        voice = body.get("voice")
    else:
        text = str(request.query_params.get("text", "")).strip()
        voice = request.query_params.get("voice")
    if not text:
        raise HTTPException(400, "text 不能为空")
    try:
        wav_data = await tts_mod.synthesize(text, voice)
        return Response(
            content=wav_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": 'inline; filename="tts.wav"',
                "Cache-Control": "no-store",
            },
        )
    except ConnectionError as e:
        raise HTTPException(503, f"TTS 服务不可用: {e}")
    except Exception as e:
        raise HTTPException(500, f"TTS 合成失败: {e}")


@app.post("/api/tts/dispatch/{device_id}", dependencies=[Depends(require_admin)])
async def tts_dispatch(device_id: str, body: dict):
    """通过 MQTT 向指定设备下发语音播报文本。

    请求体: {"text": "播报文本", "level": 0}
    设备端订阅 envmon/{device_id}/tts 主题接收。
    """
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    level = int(body.get("level", 0))
    if not bridge.client or not bridge.connected:
        raise HTTPException(503, "MQTT 未连接")
    payload = json.dumps({
        "text": text,
        "level": level,
        "device_id": device_id,
    }, ensure_ascii=False)
    topic = f"envmon/{device_id}/tts"
    import paho.mqtt.client as mqtt
    res = bridge.client.publish(topic, payload, qos=1)
    return {
        "ok": res.rc == mqtt.MQTT_ERR_SUCCESS,
        "topic": topic,
        "text": text,
    }

# ================================================================ 数据源配置
# 支持6类外部数据源: medication(用药) io_balance(出入量) lab(检验/血气)
# exam(检查) patient(患者) doctor(医生)
# 每类可配置: enabled, type(hl7/rest/db/ws), url, auth_type, auth_key,
#             sync_interval(manual/hourly/daily/realtime), extra(自定义参数)
_DS_TYPES = ["medication", "io_balance", "lab", "exam", "vital", "patient", "doctor"]
_DS_LABELS = {
    "medication": "用药", "io_balance": "出入量", "lab": "检验/血气",
    "exam": "检查", "vital": "体征", "patient": "患者", "doctor": "医生",
}
_DS_FIELDS = ["enabled", "type", "url", "auth_type", "auth_key",
              "sync_interval", "extra",
              "db_host", "db_port", "db_name", "db_user", "db_pass"]


@app.get("/api/datasources", dependencies=[Depends(require_user)])
def list_datasources():
    """列出所有数据源配置。"""
    raw = icu.list_settings_raw()
    result = []
    for name in _DS_TYPES:
        ds = {"name": name, "label": _DS_LABELS[name]}
        for f in _DS_FIELDS:
            key = f"datasource.{name}.{f}"
            ds[f] = raw.get(key, "")
        ds["enabled"] = ds["enabled"] == "true" or ds["enabled"] == "1"
        result.append(ds)
    return {"datasources": result}


@app.post("/api/datasources", dependencies=[Depends(require_admin)])
def save_datasources(body: Dict[str, Any]):
    """批量保存数据源配置。body: { "datasource.medication.enabled": "true", ... }"""
    for k, v in (body or {}).items():
        if k and k.startswith("datasource."):
            icu.set_setting(str(k), str(v) if v is not None else "")
    return {"ok": True}



@app.put("/api/datasources/{name}", dependencies=[Depends(require_admin)])
def save_one_datasource(name: str, body: Dict[str, Any]):
    """保存单个数据源配置。body: { "enabled": "true", "type": "db", ... }"""
    if name not in _DS_TYPES:
        raise HTTPException(404, "未知数据源类型")
    for k, v in (body or {}).items():
        if k in _DS_FIELDS:
            icu.set_setting(f"datasource.{name}.{k}", str(v) if v is not None else "")
    return {"ok": True, "name": name}
@app.post("/api/datasources/{name}/test", dependencies=[Depends(require_admin)])
def test_datasource(name: str):
    """测试数据源连接。根据连接类型做不同的可达性检查。"""
    if name not in _DS_TYPES:
        raise HTTPException(404, "未知数据源类型")
    raw = icu.list_settings_raw()
    ds_type = raw.get(f"datasource.{name}.type", "")

    # --- 数据库直连：尝试 TCP 端口连通 ---
    if ds_type == "db":
        db_host = raw.get(f"datasource.{name}.db_host", "")
        db_port = raw.get(f"datasource.{name}.db_port", "3306")
        if not db_host:
            return {"ok": False, "error": "数据库 IP/主机 未配置"}
        try:
            import socket
            port = int(db_port) if db_port else 3306
            with socket.create_connection((db_host, port), timeout=5):
                return {"ok": True, "msg": f"数据库端口可达 {db_host}:{port}"}
        except Exception as e:
            return {"ok": False, "error": f"数据库连接失败: {str(e)}"}

    # --- HL7 / REST / WS：URL 可达性检查 ---
    url = raw.get(f"datasource.{name}.url", "")
    if not url:
        return {"ok": False, "error": "URL 未配置"}
    try:
        import urllib.request, urllib.error as ue
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "envmon-datasource-test/1.0")
        auth_key = raw.get(f"datasource.{name}.auth_key", "")
        if auth_key:
            req.add_header("Authorization", f"Bearer {auth_key}")
        resp = urllib.request.urlopen(req, timeout=10)
        return {"ok": True, "status": resp.status, "msg": f"连接成功 (HTTP {resp.status})"}
    except ue.HTTPError as e:
        return {"ok": True, "status": e.code, "msg": f"可达 (HTTP {e.code})"}
    except ue.URLError as e:
        return {"ok": False, "error": f"连接失败: {str(e.reason)}"}
    except Exception as e:
        return {"ok": False, "error": f"测试异常: {str(e)}"}

    """导出遥测数据为 CSV 下载。"""
    if not device:
        raise HTTPException(400, "device 参数必填")
    end = end or db.utcnow()
    start = start or "1970-01-01"
    rows = db.history_range(device, start, end, limit)
    if not rows:
        return {"ok": True, "count": 0, "csv": ""}
    cols = ["ts", "seq", "temp_c", "hum_pct", "pres_hpa", "rssi", "alarm_level", "free_heap"]
    header = ",".join(cols)
    lines = [header]
    for r in rows:
        d = dict(r)
        lines.append(",".join(str(d.get(c, "")) for c in cols))
    csv_content = "\n".join(lines)
    return {"ok": True, "count": len(rows), "csv": csv_content}


@app.get("/api/sync-time")
def sync_time():
    """返回服务器当前 UTC 时间戳（设备时间同步用，无需登录）。"""
    return {"utc_ms": int(time.time() * 1000), "utc": db.utcnow(),
            "local": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}


@app.post("/api/cleanup", dependencies=[Depends(require_admin)])
def cleanup_data(older_than_days: int = Query(30, ge=1, le=365)):
    """清理超过指定天数的历史遥测数据。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    deleted = db.execute("DELETE FROM telemetry WHERE ts < ?", (cutoff,))
    log.info("cleanup: deleted %d telemetry records older than %s", deleted, cutoff)
    return {"ok": True, "deleted": deleted, "cutoff": cutoff}


@app.post("/api/ai/analyses", dependencies=[Depends(require_admin)])
def create_ai_analysis(body: Dict[str, Any]):
    """手动触发 AI 分析。body: {device_id, text?}"""
    device_id = body.get("device_id")
    if not device_id:
        raise HTTPException(400, "device_id 必填")
    text = body.get("text") or f"设备 {device_id} 的报警分析"

    # 查设备最新遥测 + 关联患者
    latest = db.latest_telemetry(device_id) or {}
    th = db.get_thresholds(device_id) or {}
    patient_id = None
    patient_name = None
    rows = db.query_locked(
        "SELECT p.id, p.name FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=? "
        "ORDER BY pd.linked_at DESC LIMIT 1", (device_id,))
    if rows:
        d = dict(rows[0])
        patient_id = d.get("id")
        patient_name = d.get("name")

    # 构建分析文本
    t = latest.get("temp_c", "-")
    h = latest.get("hum_pct", "-")
    p = latest.get("pres_hpa", "-")
    alarm_level = latest.get("alarm_level", 0)
    prompt = (f"设备 {device_id} 当前数据：温度={t}℃ 湿度={h}%RH 气压={p}hPa "
              f"报警级别={alarm_level}\n用户请求分析：{text}")

    # 调 AI
    try:
        from . import ai_client
        enabled = icu.get_setting_raw("ai.enabled") or ""
        model = icu.get_setting_raw("ai.model") or ""
        if enabled not in ("1", "true", "True", "yes"):
            return {"ok": False, "error": "AI 未启用 (ai.enabled 未开启)"}
        if not model.strip():
            return {"ok": False, "error": "AI 模型未配置 (ai.model 为空)"}

        content, err, usage = ai_client.call_model(
            ai_client._read_settings(icu),
            [{"role": "system", "content": "你是 ICU 重症监护助理。请根据监护数据做简要的中文医学分析。"},
             {"role": "user", "content": prompt}],
        )
        if err:
            return {"ok": False, "error": f"AI 分析失败: {err}"}
        # 落库
        db.execute(
            "INSERT INTO ai_analyses (device_id, ts, prompt, content, usage) "
            "VALUES (?,?,?,?,?)",
            (device_id, db.utcnow(), prompt, content, str(usage or {})))
        return {"ok": True, "content": content, "usage": usage}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"AI 分析失败: {e}"}


# ================================================================ WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # 通过 ?token= 或 Sec-WebSocket-Protocol 传递会话 token
    token = ws.query_params.get("token", "")
    user = db.get_session_user(token) if token else None
    if not user:
        await ws.close(code=4401, reason="unauthorized")
        return
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.discard(ws)


# ================================================================ ICU 重症监护路由组
# ---------- 患者 ----------
@app.get("/api/patients", dependencies=[Depends(require_user)])
def list_patients(limit: int = Query(200, ge=1, le=1000)):
    return {"patients": icu.list_patients(limit)}


@app.post("/api/patients", dependencies=[Depends(require_admin)])
def create_patient(body: PatientCreate):
    existing = icu.patient_by_pid(body.pid)
    if existing:
        raise HTTPException(409, f"患者编号 {body.pid} 已存在")
    pid_id = icu.patient_create(
        body.pid, body.name, body.gender, body.age, body.bed_no,
        body.admit_ts, body.diagnosis, body.doctor, body.phone,
        body.wechat_userid,
    )
    return {"ok": True, "patient_id": pid_id, "pid": body.pid}


@app.get("/api/patients/{pid}", dependencies=[Depends(require_user)])
def get_patient(pid: str):
    # 先按 pid 字符串查（前端传的始终是 pid），查不到再按整数 id 回退
    p = icu.patient_by_pid(pid)
    if not p and pid.isdigit():
        p = icu.patient_by_id(int(pid))
    if not p:
        raise HTTPException(404, "患者不存在")
    return p


@app.put("/api/patients/{pid}", dependencies=[Depends(require_admin)])
@app.patch("/api/patients/{pid}", dependencies=[Depends(require_admin)])
def update_patient(pid: str, body: PatientUpdate):
    p = icu.patient_by_pid(pid)
    if not p and pid.isdigit():
        p = icu.patient_by_id(int(pid))
    if not p:
        raise HTTPException(404, "患者不存在")
    icu.patient_update(p["id"], **body.model_dump())
    return {"ok": True}


@app.delete("/api/patients/{pid}", dependencies=[Depends(require_admin)])
def delete_patient(pid: str):
    p = icu.patient_by_pid(pid)
    if not p and pid.isdigit():
        p = icu.patient_by_id(int(pid))
    if not p:
        raise HTTPException(404, "患者不存在")
    icu.patient_delete(p["id"])
    return {"ok": True}


# ---------- 患者-设备关联 ----------
@app.post("/api/patients/{pid}/link/{device_id}", dependencies=[Depends(require_admin)])
def link_device(pid: str, device_id: str, role: str = Query("primary", pattern=r"^(primary|secondary)$")):
    p = icu.patient_by_pid(pid)
    if not p and pid.isdigit():
        p = icu.patient_by_id(int(pid))
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        icu.link_device(p["id"], device_id, role)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"关联失败: {str(e)}")
    # BUG-05: 关联后同步更新 device.patient_id
    db.execute("UPDATE devices SET patient_id=? WHERE id=?", (p["id"], device_id))
    return {"ok": True}


@app.delete("/api/patients/{pid}/unlink/{device_id}", dependencies=[Depends(require_admin)])
@app.post("/api/patients/{pid}/unlink/{device_id}", dependencies=[Depends(require_admin)])
def unlink_device(pid: str, device_id: str):
    p = icu.patient_by_pid(pid)
    if not p and pid.isdigit():
        p = icu.patient_by_id(int(pid))
    if not p:
        raise HTTPException(404, "患者不存在")
    ok = icu.unlink_device(p["id"], device_id)
    if not ok:
        raise HTTPException(404, "未找到该患者-设备绑定")
    # BUG-05: 解绑后清除 device.patient_id
    db.execute("UPDATE devices SET patient_id=NULL WHERE id=?", (device_id,))
    return {"ok": True}


@app.get("/api/devices/{device_id}/binding", dependencies=[Depends(require_user)])
def device_binding(device_id: str):
    b = icu.device_current_binding(device_id)
    return {"device_id": device_id, "bound": b is not None, "binding": b}


@app.get("/api/patients/{pid}/devices", dependencies=[Depends(require_user)])
def patient_devices(pid: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    devices = icu.devices_for_patient(p["id"])
    # 为每个设备补充最新 telemetry 数据，前端监护界面可直接展示
    for d in devices:
        dev_id = d.get("device_id")
        if dev_id:
            d["latest_telemetry"] = db.latest_telemetry(dev_id)
        else:
            d["latest_telemetry"] = None
    return {"devices": devices}


# ---------- 最新体征（逐指标独立取值 + 遥测回退）----------
@app.get("/api/patients/{pid}/latest-signs", dependencies=[Depends(require_user)])
def latest_signs(pid: str):
    """每个生命体征指标各自查最新非空值，同时查关联设备遥测做回退。

    返回 {sign: {value, ts, source}} — 谁有数据就返回谁，互不依赖。
    """
    import sqlite3 as _sqlite3
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    patient_id = p["id"]

    # 需要独立取值的指标列表
    signs = ["ecg_hr", "sp_o2", "rr_bpm", "sbp", "dbp", "temp_c", "glucose",
             "hum_pct", "pres_hpa", "pr_hr", "map_bp"]
    result = {}

    conn = icu._get_conn()
    conn.row_factory = _sqlite3.Row

    # 1) 从 vitals 表逐指标查最新非空值
    for sign in signs:
        row = conn.execute(
            f"SELECT {sign} AS val, ts, source_device FROM vitals "
            f"WHERE patient_id=? AND {sign} IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
        if row and row["val"] is not None:
            result[sign] = {"value": row["val"], "ts": row["ts"],
                            "source": "vitals", "device": row["source_device"]}

    # 2) 遥测回退：对 vitals 表没有的指标，查关联设备的最新遥测
    devices = icu.devices_for_patient(patient_id)
    # 若无绑定设备，查所有设备（设备可能换了还没绑定）
    if not devices:
        all_devs = db.list_devices()
        devices = [{"device_id": d["id"]} for d in all_devs]
    for d in devices:
        dev_id = d.get("device_id")
        if not dev_id:
            continue
        tel = db.latest_telemetry(dev_id)
        if not tel:
            continue
        # 遥测字段 → 体征字段映射
        tel_map = {"temp_c": "temp_c", "hum_pct": "hum_pct", "pres_hpa": "pres_hpa"}
        for tel_field, sign in tel_map.items():
            if sign not in result and tel.get(tel_field) is not None:
                result[sign] = {"value": tel[tel_field], "ts": tel["ts"],
                                "source": "telemetry", "device": dev_id}

    conn.close()
    return {"pid": pid, "signs": result}


# ---------- 生命体征 ----------
@app.post("/api/patients/{pid}/vitals", dependencies=[Depends(require_admin)])
def add_vital(pid: str, body: VitalIn):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    ts = body.ts or icu._now()
    kwargs = body.model_dump()
    kwargs.pop("source", None)
    kwargs.pop("source_device", None)
    kwargs.pop("ts", None)
    icu.insert_vital(
        p["id"], ts, body.source,
        source_device=body.source_device or None,
        **kwargs,
    )
    # 自动创建监护记录（若该患者无活跃会话）
    try:
        _ensure_monitor_session(p["id"], body.source_device or "")
    except Exception:
        pass
    # 检查体征异常并记录报警
    try:
        _check_vital_alarms(body.source_device or "", pid, kwargs)
    except Exception:
        pass
    hub.broadcast_threadsafe({
        "type": "vital", "patient_id": p["id"], "pid": pid,
        "ts": ts, "source": body.source,
    })
    _push_to_external("vital", {"pid": pid, "ts": ts, "source": body.source, **kwargs})
    return {"ok": True}


@app.get("/api/patients/{pid}/vitals")
def get_vitals(pid: str, start: Optional[str] = None, end: Optional[str] = None,
               fields: str = Query("", description="逗号分隔字段名"),
               hours: Optional[int] = Query(None, ge=1)):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    if hours:
        from datetime import timedelta
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=hours)
        end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
    rows = icu.patient_vitals(p["id"], start or "1970-01-01T00:00:00Z",
                              end or "9999-12-31T00:00:00Z", field_list)
    # 回退1：如果时间窗口查询无结果，但数据库中有体征数据（可能因设备时钟偏差
    # 或时区格式不一致导致 ts 落在窗口外），则不按时间过滤取最新 100 条，
    # 确保实时监护界面与监护屏一致地展示数据。
    if not rows and hours:
        rows = icu.patient_vitals_latest(p["id"], limit=100, fields=field_list)
    # 回退2：vitals 表完全无数据时，查关联设备遥测（含所有设备做回退），
    # 把遥测的 temp_c/hum_pct/pres_hpa 转成 vitals 格式返回，趋势图能画出来。
    if not rows:
        import sqlite3 as _sqlite3
        _conn = icu._get_conn()
        _conn.row_factory = _sqlite3.Row
        # 先查绑定的设备，没有则查所有设备
        dev_rows = _conn.execute(
            "SELECT device_id FROM patient_devices WHERE patient_id=?", (p["id"],)
        ).fetchall()
        dev_ids = [r["device_id"] for r in dev_rows if r["device_id"]]
        if not dev_ids:
            all_devs = _conn.execute("SELECT id FROM devices").fetchall()
            dev_ids = [r["id"] for r in all_devs if r["id"]]
        _conn.close()
        for did in dev_ids:
            tel_rows = db.query(
                "SELECT ts, temp_c, hum_pct, pres_hpa FROM telemetry "
                "WHERE device_id=? ORDER BY ts DESC LIMIT 200", (did,))
            for tr in tel_rows:
                rows.append({
                    "ts": tr["ts"], "temp_c": tr["temp_c"],
                    "hum_pct": tr["hum_pct"], "pres_hpa": tr["pres_hpa"],
                    "source": "telemetry", "alarm_flag": 0,
                })
        rows.sort(key=lambda r: r["ts"])
        if hours:
            from datetime import timedelta
            _cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = [r for r in rows if r["ts"] >= _cutoff]
    return {"patient_id": p["id"], "count": len(rows), "points": rows}



# ---------- 临床数据双向同步 ----------
def _push_to_external(ds_name: str, payload: dict):
    """手工录入时，根据 datasource 配置推送到外部系统 (REST POST)。

    ds_name: 数据源类型名 (medication / lab / exam / io_balance)
    payload: 要推送的 JSON 数据
    """
    try:
        raw = icu.list_settings_raw()
        enabled = raw.get(f"datasource.{ds_name}.enabled", "")
        if enabled not in ("true", "1"):
            return  # 未启用，跳过
        url = raw.get(f"datasource.{ds_name}.url", "")
        if not url or not url.startswith("http"):
            return  # 无有效 URL，跳过
        auth_key = raw.get(f"datasource.{ds_name}.auth_key", "")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "envmon-sync/1.0")
        if auth_key:
            req.add_header("Authorization", f"Bearer {auth_key}")
        resp = urllib.request.urlopen(req, timeout=10)
        log.info("push_to_external[%s]: %s -> HTTP %s", ds_name, url, resp.status)
    except Exception as e:
        log.warning("push_to_external[%s] failed: %s", ds_name, e)


# ---------- 医嘱 ----------
@app.post("/api/patients/{pid}/orders", dependencies=[Depends(require_admin)])
def add_order(pid: str, body: OrderIn):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    oid = icu.order_insert(
        p["id"], body.source, body.order_no, body.drug_name, body.dosage,
        body.route, body.start_ts, body.end_ts, body.rate_mlph,
        operator=body.operator or None,
    )
    hub.broadcast_threadsafe({"type": "order", "patient_id": p["id"], "pid": pid, "order_id": oid})
    _push_to_external("medication", {"pid": pid, "order_id": oid, "source": body.source,
        "order_no": body.order_no, "drug_name": body.drug_name, "dosage": body.dosage,
        "route": body.route, "start_ts": body.start_ts, "end_ts": body.end_ts,
        "rate_mlph": body.rate_mlph, "operator": body.operator})
    return {"ok": True, "order_id": oid}


@app.get("/api/patients/{pid}/orders")
def get_orders(pid: str, start: Optional[str] = None, end: Optional[str] = None):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        return {"orders": icu.orders_for_patient(p["id"], start, end)}
    except Exception as e:
        log.warning("get_orders failed (table missing?): %s", e)
        return {"orders": []}


@app.post("/api/patients/{pid}/orders/{order_id}/stop", dependencies=[Depends(require_admin)])
def stop_order(pid: str, order_id: int):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    ok = icu.order_stop(order_id)
    return {"ok": ok}


# ---------- LIS 检验 ----------
@app.post("/api/patients/{pid}/lab", dependencies=[Depends(require_admin)])
def add_lab(pid: str, body: LabResultIn):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    lid = icu.lab_result_insert(
        p["id"], body.source, body.item_code, body.item_name,
        body.value, body.unit, body.ref_min, body.ref_max,
        body.result_ts or None, 1 if body.critical else 0,
    )
    hub.broadcast_threadsafe({"type": "lab", "patient_id": p["id"], "pid": pid, "lab_id": lid})
    _push_to_external("lab", {"pid": pid, "lab_id": lid, "source": body.source,
        "item_code": body.item_code, "item_name": body.item_name, "value": body.value,
        "unit": body.unit, "ref_min": body.ref_min, "ref_max": body.ref_max,
        "result_ts": body.result_ts, "critical": body.critical})
    return {"ok": True, "lab_id": lid}


@app.get("/api/patients/{pid}/lab")
def get_lab(pid: str, start: Optional[str] = None, end: Optional[str] = None):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        return {"results": icu.lab_results_for_patient(p["id"], start, end)}
    except Exception as e:
        log.warning("get_lab failed: %s", e)
        return {"results": []}



# ---------- 检查报告 ----------
@app.post("/api/patients/{pid}/exam", dependencies=[Depends(require_admin)])
def add_exam(pid: str, body: ExamIn):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    eid = icu.exam_insert(
        p["id"], body.source, body.exam_type, body.exam_name,
        body.result, body.report_url, body.operator, body.exam_ts or None,
    )
    hub.broadcast_threadsafe({"type": "exam", "patient_id": p["id"], "pid": pid, "exam_id": eid})
    _push_to_external("exam", {"pid": pid, "exam_id": eid, "source": body.source,
        "exam_type": body.exam_type, "exam_name": body.exam_name,
        "result": body.result, "report_url": body.report_url,
        "operator": body.operator, "exam_ts": body.exam_ts})
    return {"ok": True, "exam_id": eid}


@app.get("/api/patients/{pid}/exam")
def get_exams(pid: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        return {"exams": icu.exams_for_patient(p["id"])}
    except Exception as e:
        log.warning("get_exams failed: %s", e)
        return {"exams": []}


# ---------- 出入量 ----------
@app.post("/api/patients/{pid}/io", dependencies=[Depends(require_admin)])
def add_io(pid: str, body: Dict[str, Any]):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    if body.get("direction") not in ("in", "out"):
        raise HTTPException(422, "direction 必须为 in 或 out")
    if not body.get("kind"):
        raise HTTPException(422, "kind 必填")
    try:
        rid = icu.add_io_log(p["id"], body["direction"], body["kind"],
                              body.get("amount_ml"), body.get("amount_g"),
                              body.get("sub_type"), body.get("route"),
                              body.get("note"), body.get("source", "manual"),
                              body.get("operator"), body.get("ts"), body.get("unique_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    hub.broadcast_threadsafe({"type": "io", "patient_id": p["id"], "pid": pid, "io_id": rid})
    _push_to_external("io_balance", {"pid": pid, "io_id": rid, "direction": body["direction"],
        "kind": body["kind"], "amount_ml": body.get("amount_ml"), "amount_g": body.get("amount_g"),
        "sub_type": body.get("sub_type"), "route": body.get("route"),
        "note": body.get("note"), "source": body.get("source", "manual"),
        "operator": body.get("operator"), "ts": body.get("ts")})
    return {"ok": True, "io_id": rid}


@app.get("/api/patients/{pid}/io")
def list_io(pid: str, hours: int = 72):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        return {"entries": icu.list_io_log(p["id"], hours)}
    except Exception as e:
        log.warning("list_io failed: %s", e)
        return {"entries": []}


@app.get("/api/patients/{pid}/io/balance")
def io_balance(pid: str, hours: int = Query(24, ge=1, le=720)):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        return icu.io_balance(p["id"], hours)
    except Exception as e:
        log.warning("io_balance failed: %s", e)
        return {"in_ml": 0, "out_ml": 0, "net_ml": 0}


# ================================================================ 就诊记录
@app.get("/api/encounters", dependencies=[Depends(require_user)])
def list_encounters_api(patient_id: Optional[int] = Query(None),
                        status: Optional[str] = Query(None),
                        limit: int = Query(100, ge=1, le=500)):
    """查询就诊记录列表。"""
    encounters = icu.list_encounters(patient_id=patient_id, status=status, limit=limit)
    # 为每条就诊补充监护会话数和报警数
    for e in encounters:
        eid = e["id"]
        e["session_count"] = db.query_one_locked(
            "SELECT COUNT(*) FROM monitor_sessions WHERE encounter_id=?", (eid,))[0]
        e["alarm_count"] = db.query_one_locked(
            "SELECT COUNT(*) FROM alarms WHERE encounter_id=?", (eid,))[0]
        e["duration_str"] = _duration_str(e.get("start_ts"), e.get("end_ts"))
    return {"encounters": encounters, "total": len(encounters)}


@app.get("/api/encounters/{encounter_id}", dependencies=[Depends(require_user)])
def get_encounter_detail(encounter_id: int):
    """就诊记录详情：含监护会话列表 + 报警列表 + 体征摘要。"""
    enc = icu.get_encounter(encounter_id)
    if not enc:
        raise HTTPException(404, "就诊记录不存在")
    eid = enc["id"]
    # 该就诊下的监护会话
    sessions = icu.list_monitor_sessions(patient_id=enc["patient_id"], limit=100)
    sessions = [s for s in sessions if s.get("encounter_id") == eid]
    for s in sessions:
        s["duration_str"] = _duration_str(s.get("start_ts"), s.get("end_ts"))
    # 该就诊下的报警
    alarms = [dict(r) for r in db.query(
        "SELECT * FROM alarms WHERE encounter_id=? ORDER BY ts DESC LIMIT 200", (eid,))]
    # 体征统计
    start_ts = enc["start_ts"]
    end_ts = enc["end_ts"] or db.utcnow()
    vitals = icu.patient_vitals(enc["patient_id"], start_ts, end_ts)
    enc["duration_str"] = _duration_str(start_ts, enc.get("end_ts"))
    return {
        "encounter": enc,
        "sessions": sessions,
        "alarms": alarms,
        "vitals_count": len(vitals),
    }


@app.post("/api/patients/{pid}/encounters", dependencies=[Depends(require_admin)])
def create_encounter(pid: str, body: Dict[str, Any] = None):
    """为患者创建新就诊记录（新住院）。若已有活跃就诊，先自动结束。"""
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    body = body or {}
    # 自动结束现有活跃就诊
    active = icu.active_encounter_for_patient(p["id"])
    if active:
        icu.end_encounter(active["id"], body.get("prior_summary", ""))
    enc = icu.ensure_active_encounter(
        p["id"],
        bed_no=body.get("bed_no", p.get("bed_no")),
        diagnosis=body.get("diagnosis", p.get("diagnosis")),
    )
    return {"ok": True, "encounter": enc}


@app.post("/api/encounters/{encounter_id}/end", dependencies=[Depends(require_admin)])
def end_encounter_api(encounter_id: int, body: Dict[str, Any] = None):
    """结束就诊记录（出院）。"""
    body = body or {}
    summary = body.get("summary", "")
    result = icu.end_encounter(encounter_id, summary)
    return {"ok": True, **result}


@app.get("/api/patients/{pid}/encounters", dependencies=[Depends(require_user)])
def list_patient_encounters(pid: str):
    """列出某患者的所有就诊记录。"""
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    encounters = icu.list_encounters(patient_id=p["id"], limit=100)
    for e in encounters:
        eid = e["id"]
        e["session_count"] = db.query_one_locked(
            "SELECT COUNT(*) FROM monitor_sessions WHERE encounter_id=?", (eid,))[0]
        e["alarm_count"] = db.query_one_locked(
            "SELECT COUNT(*) FROM alarms WHERE encounter_id=?", (eid,))[0]
        e["duration_str"] = _duration_str(e.get("start_ts"), e.get("end_ts"))
    return {"encounters": encounters}


@app.get("/api/monitor/sessions", dependencies=[Depends(require_user)])
def list_monitor_sessions(patient_id: Optional[int] = Query(None),
                          device_id: Optional[str] = Query(None),
                          start: Optional[str] = Query(None),
                          end: Optional[str] = Query(None),
                          limit: int = Query(200, ge=1, le=1000)):
    """查询监护记录列表（支持按患者/设备/日期范围过滤）。"""
    try:
        sessions = icu.list_monitor_sessions(
            patient_id=patient_id, device_id=device_id,
            start=start, end=end, limit=limit,
        )
    except Exception as e:
        log.warning("list_monitor_sessions failed: %s", e)
        sessions = []
    # 计算每条会话持续时间
    for s in sessions:
        s["duration_str"] = _duration_str(s.get("start_ts"), s.get("end_ts"))
    return {"sessions": sessions, "total": len(sessions)}


@app.get("/api/monitor/sessions/{session_id}", dependencies=[Depends(require_user)])
def get_monitor_session_detail(session_id: int):
    """获取单条监护记录详情 + 该时段内的体征/医嘱/检验/出入量。"""
    sess = icu.get_monitor_session(session_id)
    if not sess:
        raise HTTPException(404, "监护记录不存在")
    start_ts = sess["start_ts"]
    end_ts = sess["end_ts"] or db.utcnow()
    # 该时段内的数据
    vitals = icu.patient_vitals(sess["patient_id"], start_ts, end_ts)
    orders = icu.orders_for_patient(sess["patient_id"], start_ts, end_ts)
    labs = icu.lab_results_for_patient(sess["patient_id"], start_ts, end_ts)
    io_logs = icu.list_io_log(sess["patient_id"], hours=9999)  # 全量
    io_logs = [io for io in io_logs if io.get("ts", "") >= start_ts and io.get("ts", "") <= end_ts]
    sess["duration_str"] = _duration_str(start_ts, sess.get("end_ts"))
    return {
        "session": sess,
        "vitals": vitals,
        "orders": orders,
        "labs": labs,
        "io_logs": io_logs,
    }


@app.get("/api/devices/{device_id}/active-patient", dependencies=[Depends(require_user)])
def get_active_patient_for_device(device_id: str):
    """查询某设备当前绑定的患者。优先查 patient_devices 绑定表，
    其次查 monitor_sessions 活跃监护记录（兼容旧逻辑）。"""
    # 优先：patient_devices 绑定关系
    binding = icu.device_current_binding(device_id)
    if binding:
        return {"device_id": device_id, "patient_pid": binding.get("pid"),
                "patient_id": binding.get("patient_id"),
                "patient_name": binding.get("name"), "bed_no": binding.get("bed_no")}
    # 兼容：活跃监护记录
    sess = icu.active_session_for_device(device_id)
    if not sess:
        return {"device_id": device_id, "patient_pid": None}
    return {"device_id": device_id, "patient_pid": sess.get("pid"),
            "patient_id": sess.get("patient_id"),
            "patient_name": sess.get("name"), "bed_no": sess.get("bed_no"),
            "session_id": sess.get("id")}


def _duration_str(start_ts: str, end_ts: str = None) -> str:
    """计算持续时间的可读字符串。"""
    if not start_ts:
        return "-"
    try:
        start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        if end_ts:
            end_dt = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
        else:
            end_dt = datetime.now(timezone.utc)
        delta = end_dt - start_dt
        total_sec = int(delta.total_seconds())
        if total_sec < 0:
            return "-"
        days = total_sec // 86400
        hours = (total_sec % 86400) // 3600
        mins = (total_sec % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except Exception:
        return "-"


@app.post("/api/patients/{pid}/monitor/start", dependencies=[Depends(require_admin)])
def start_monitor_session(pid: str, body: Dict[str, Any] = None):
    """开始监护记录：关联患者与设备，记录 start_ts。"""
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    body = body or {}
    device_id = body.get("device_id")
    result = icu.start_monitor_session(p["id"], device_id)
    return {"ok": True, **result}


@app.post("/api/patients/{pid}/monitor/{sid}/end", dependencies=[Depends(require_admin)])
def end_monitor_session_route(pid: str, sid: int, body: Dict[str, Any] = None):
    """结束监护记录：设置 end_ts 和可选 summary。"""
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    body = body or {}
    summary = body.get("summary", "")
    result = icu.end_monitor_session(sid, summary)
    return {"ok": True, **result}


@app.get("/api/patients/{pid}/monitor/sessions", dependencies=[Depends(require_user)])
def list_patient_monitor_sessions(pid: str):
    """列出某患者的所有监护记录。"""
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    sessions = icu.list_monitor_sessions(patient_id=p["id"])
    for s in sessions:
        s["duration_str"] = _duration_str(s.get("start_ts"), s.get("end_ts"))
    return {"sessions": sessions, "total": len(sessions)}


# ---------- AI 评估 ----------
@app.get("/api/patients/{pid}/assessment")
def assess(pid: str, hours: int = Query(24, ge=1, le=720), ai: bool = Query(False)):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    if ai:
        return icu.assess_with_ai(p["id"], hours)
    return icu.assess_patient(p["id"], hours)


# ---------- 备份 ----------
@app.post("/api/backup", dependencies=[Depends(require_admin)])
def trigger_backup():
    global _backup_last_ts
    now = time.time()
    if now - _backup_last_ts < 60:
        remaining = round(60 - (now - _backup_last_ts), 1)
        raise HTTPException(429, f"备份过于频繁，请 {remaining}s 后重试")
    _backup_last_ts = now
    info = icu.do_backup()
    return {"ok": True, **info}


@app.get("/api/backup", dependencies=[Depends(require_admin)])
def list_backups(limit: int = Query(20, ge=1, le=100)):
    return {"backups": icu.list_backups(limit)}


@app.get("/api/backup/{filename}/download", dependencies=[Depends(require_admin)])
def download_backup(filename: str):
    """下载指定备份文件。"""
    # 防止路径穿越
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    filepath = os.path.join(icu.BACKUP_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(404, "备份文件不存在")
    return FileResponse(filepath, filename=filename,
                        media_type="application/octet-stream")


@app.delete("/api/backup/{filename}", dependencies=[Depends(require_admin)])
def delete_backup(filename: str):
    """删除指定备份文件。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    filepath = os.path.join(icu.BACKUP_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(404, "备份文件不存在")
    os.remove(filepath)
    log.info("backup deleted: %s", filename)
    return {"ok": True}


@app.post("/api/backup/restore", dependencies=[Depends(require_admin)])
async def restore_backup(file: UploadFile = File(...)):
    """上传备份文件并恢复数据库。

    流程：保存上传文件 → 校验是合法 SQLite → 备份当前库 → 替换 → 重连。
    需要重启服务才能完全生效（重新执行 init_db / bootstrap_admin）。
    """
    import sqlite3 as _sqlite3
    import tempfile
    import shutil as _shutil

    # 1. 保存上传文件到临时路径
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(400, f"上传失败: {e}")

    # 2. 校验是合法 SQLite 数据库
    try:
        test_conn = _sqlite3.connect(tmp_path)
        tables = [r[0] for r in test_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        test_conn.close()
        if not tables:
            raise ValueError("文件中没有数据库表")
        if "users" not in tables:
            raise ValueError("文件中缺少 users 表，不是有效的系统备份")
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(400, f"无效的数据库文件: {e}")

    # 3. 备份当前数据库（恢复前的安全网）
    try:
        safety = icu.do_backup()
        log.info("pre-restore safety backup: %s", safety["path"])
    except Exception as e:
        log.warning("pre-restore backup failed: %s", e)

    # 4. 替换数据库文件
    try:
        _shutil.copy2(tmp_path, icu.DB_PATH)
        log.info("database restored from uploaded file: %s", file.filename)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(500, f"恢复失败: {e}")
    finally:
        os.unlink(tmp_path)

    return {"ok": True, "msg": "数据库已恢复，请重启服务使更改完全生效"}


# ================================================================ ICU 重症监护路由组
# ---------- 医生档案 ----------
@app.get("/api/doctors", dependencies=[Depends(require_user)])
def list_doctors(limit: int = Query(200, ge=1, le=1000)):
    return {"doctors": db.doctor_list(limit)}


@app.post("/api/doctors", dependencies=[Depends(require_admin)])
def create_doctor(body: DoctorCreateIn):
    did = db.doctor_create(
        body.name, body.title, body.department, body.contact, body.note,
        wechat_userid=body.wechat_userid,
    )
    return {"ok": True, "doctor_id": did, "name": body.name}


@app.get("/api/doctors/{did}", dependencies=[Depends(require_user)])
def get_doctor(did: int):
    d = db.doctor_by_id(did)
    if not d:
        raise HTTPException(404, "医生不存在")
    return d


@app.put("/api/doctors/{did}", dependencies=[Depends(require_admin)])
def update_doctor(did: int, body: DoctorUpdateIn):
    d = db.doctor_by_id(did)
    if not d:
        raise HTTPException(404, "医生不存在")
    db.doctor_update(did, **body.model_dump(exclude_unset=True))
    return {"ok": True}


@app.delete("/api/doctors/{did}", dependencies=[Depends(require_admin)])
def delete_doctor(did: int):
    d = db.doctor_by_id(did)
    if not d:
        raise HTTPException(404, "医生不存在")
    ok = db.doctor_delete(did)
    return {"ok": ok}


# ---------- 文字消息（独立通道 + TTS 双模式） ----------
@app.post("/api/messages/send", dependencies=[Depends(require_admin)])
def send_message(body: MessageSendIn):
    """向设备下发文字消息。

    - tts=True: 复用 TTS 语音通道（envmon/{device_id}/tts），设备即刻放音。
    - tts=False: 推送 envmon/{device_id}/message 独立文字主题并落库（**固件未实现，
      设备暂无法接收文字，仅保证记录留底，供固件补齐后生效**）。
    """
    import paho.mqtt.client as mqtt
    did = body.device_id
    if not db.query("SELECT id FROM devices WHERE id=?", (did,)):
        raise HTTPException(404, "设备不存在")
    text = body.text
    delivered = 0
    delivered_at = None
    if body.tts:
        # 走语音通道
        if not bridge.client or not bridge.connected:
            raise HTTPException(503, "MQTT 未连接")
        payload = json.dumps({"text": text, "level": body.level, "device_id": did},
                             ensure_ascii=False)
        res = bridge.client.publish(f"envmon/{did}/tts", payload, qos=1)
        if res.rc == mqtt.MQTT_ERR_SUCCESS:
            delivered = 1
            delivered_at = db.utcnow()
    else:
        # 独立文字 topic（尽力推送；失败也落库）
        try:
            if bridge.client and bridge.connected:
                payload = json.dumps({"text": text, "device_id": did},
                                     ensure_ascii=False)
                res = bridge.client.publish(f"envmon/{did}/message", payload, qos=1)
                if res.rc == mqtt.MQTT_ERR_SUCCESS:
                    delivered = 1
                    delivered_at = db.utcnow()
        except Exception:
            delivered = 0
    mid = db.add_message(did, text, sender="system",
                         delivered=delivered, delivered_at=delivered_at)
    return {
        "ok": True,
        "message_id": mid,
        "device_id": did,
        "text": text,
        "tts": body.tts,
        "topic": f"envmon/{did}/tts" if body.tts else f"envmon/{did}/message",
        "delivered": bool(delivered),
        "firmware_note": "" if body.tts else "固件未实现文字接收，本次仅落库+尽力推送；补齐固件后设备方可接收",
    }


@app.get("/api/messages", dependencies=[Depends(require_user)])
def list_messages(device_id: str = Query("", max_length=64),
                  limit: int = Query(200, ge=1, le=1000)):
    return {"messages": db.message_list(device_id, limit)}


@app.get("/api/messages/stat", dependencies=[Depends(require_user)])
def message_stat():
    return {"stat": db.message_stat()}


@app.delete("/api/messages", dependencies=[Depends(require_admin)])
def clear_messages():
    n = db.message_clear()
    return {"ok": True, "cleared": n}
