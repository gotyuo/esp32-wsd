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
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .aggregator import Aggregator
from .models import IngestIn, ThresholdsIn, LoginIn, UserCreate, PasswordChangeIn, SoundPrefIn, RegisterDeviceIn, SettingsUpdateIn, UpdateDeviceIn, DoctorCreateIn, DoctorUpdateIn, MessageSendIn
from . import icu
from .models import PatientCreate, PatientUpdate, LinkDeviceIn, VitalIn, OrderIn, LabResultIn
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
    """users 表为空时创建首个管理员。"""
    if db.list_users():
        return
    if not ADMIN_PASS:
        log.warning("============================================================")
        log.warning("首次启动：未设置 ADMIN_PASS 环境变量，使用默认密码 admin/admin123")
        log.warning("请立即登录后在【系统设置】中修改密码！")
        log.warning("============================================================")
        password = "admin123"
    else:
        password = ADMIN_PASS
    h, salt = hash_password(password)
    db.create_user(ADMIN_USER, "系统管理员", h, salt, role="admin")
    masked = password[:1] + "****" if len(password) > 2 else "****"
    log.info("bootstrap admin created: %s (password=%s)", ADMIN_USER, masked)
    log.info("login endpoint: http://<host>:12090  username=%s", ADMIN_USER)
    # 启动后自检：用刚生成的 hash 验证密码能否通过
    if verify_password(password, salt, h):
        log.info("bootstrap admin self-check PASS (password=admin123 verified)")
    else:
        log.error("bootstrap admin self-check FAIL — please check ADMIN_PASS env")


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
    margin = (hi - lo) * 0.10
    if v < lo + margin or v > hi - margin:
        return 1
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


def record_alarm_transition(device_id: str, level: int, reason: str, temp, hum, pres):
    """报警状态迁移：触发时记录，恢复时销警。"""
    open_alarm = db.open_alarm_for(device_id)
    if level >= 1:
        if not open_alarm or open_alarm["level"] != level:
            if open_alarm:
                db.clear_open_alarms(device_id)
            db.insert_alarm(device_id, level, reason, temp, hum, pres)
            hub.broadcast_threadsafe({"type": "alarm", "device_id": device_id,
                                      "level": level, "reason": reason})
            log.warning("ALARM [%s] lv%d %s", device_id, level, reason)
            # TTS 语音播报：报警触发时自动合成语音并下发到设备
            _trigger_tts_alarm(device_id, level, reason)
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


def _looks_like_vitals(payload: dict) -> bool:
    return any(k in payload for k in VITAL_KEYS)


VITAL_KEYS = ("sp_o2", "pr_hr", "ecg_hr", "ecg_st", "rr_bpm", "etco2",
              "sbp", "dbp", "map_bp", "ibp", "temp_c", "glucose")


def _vital_values(payload: dict) -> dict:
    """把载荷中的数值型体征字段转为 float 后挑出，供 insert_vital 使用。"""
    out = {}
    for k in VITAL_KEYS:
        v = payload.get(k)
        if v is None:
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            pass
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
        hub.broadcast_threadsafe({"type": "vital", "patient_id": target["patient_id"],
                                  "pid": target["pid"], "ts": ts,
                                  "source": payload.get("source", "esp32")})


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
    """每 3 天备份一次，并清理 3 天前的旧备份。"""
    import asyncio as aio
    while True:
        await aio.sleep(3 * 24 * 3600)
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
    log.info("EnvMon backend started")
    yield
    aggregator.stop()
    bridge.stop()
    if _backup_task:
        _backup_task.cancel()


app = FastAPI(title="EnvMon Backend", version="2.0.0", lifespan=lifespan)


# ================================================================ 页面
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


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


@app.get("/api/devices")
def devices():
    """设备列表 + 每台设备的最新一帧遥测。
    latest 供设备卡片直接显示 SP.T/HUM/PRESS/SIG，避免退化成"无历史数据"。
    """
    return {"devices": [dict(d, latest=db.latest_telemetry(d["id"]))
                        for d in db.list_devices()]}


@app.post("/api/devices", dependencies=[Depends(require_admin)])
def register_device(body: RegisterDeviceIn):
    """注册设备。ip_addr 可选：外网设备可登记接入地址（域名/IP:端口）。"""
    db.register_device(body.device_id, body.name or None, body.ip_addr or None)
    return {"ok": True}


@app.patch("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def update_device(device_id: str, body: UpdateDeviceIn):
    """更新设备名称和/或 IP 地址，用于人工修正「设备名 ↔ IP」对应关系。

    用 exclude_unset 区分「没传该字段」与「显式传 null（清空）」，
    否则用户想清空名称/IP 时会因为没有字段可改而误报 404。
    """
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


@app.get("/api/devices/{device_id}")
def get_device_detail(device_id: str):
    d = db.device_detail(device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return d


@app.get("/api/devices/{device_id}/history")
def device_patient_timeline(device_id: str):
    """某设备的历次患者分配时间线（同设备换患者时可追溯）。"""
    return {"device_id": device_id, "history": icu.device_patient_history(device_id)}


@app.put("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def rename_device(device_id: str, name: str = Query(..., max_length=64),
                  _: Dict = Depends(require_admin)):
    db.rename_device(device_id, name)
    return {"ok": True}


@app.delete("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def delete_device(device_id: str, _: Dict = Depends(require_admin)):
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


@app.post("/api/devices/probe", dependencies=[Depends(require_user)])
def probe_devices(body: dict = None):
    """主动扫描设备在线状态：读取 broker 上每台设备的 status 保留消息。
    body 可带 {"device_ids": [...]}；缺省扫描全部设备。
    无保留消息（从未连过）且无新鲜遥测 = 离线。
    返回 online/offline 两组 + 每台设备的 probe 标记。"""
    devs = db.list_devices()
    body = body or {}
    want = body.get("device_ids")
    if not isinstance(want, list) or not want:
        ids = [d["id"] for d in devs]
    else:
        ids = [str(x) for x in want]

    started = time.time()
    online = _probe_status_ids(ids)

    # 把新鲜度判定结果同步回 devices.online，让设备列表页与 probe 结果一致。
    # 之前 online 只由 LWT 维护，而保留的 "online" 消息在 bridge 重连时会重新
    # 投递，导致已死设备在列表页一直显示在线。现在列表页以「保留状态 + 遥测
    # 新鲜度」为准，与本页 probe 结论保持同一套逻辑。
    for d in devs:
        if d["id"] not in ids:
            continue
        want_online = 1 if d["id"] in online else 0
        if d.get("online") != want_online:
            db.set_device_online(d["id"], want_online == 1)

    groups: Dict[str, list] = {"online": [], "offline": []}
    for d in devs:
        if d["id"] not in ids:
            continue
        rec = dict(d)
        rec["latest"] = db.latest_telemetry(d["id"])
        rec["probe"] = d["id"] in online
        groups["online" if d["id"] in online else "offline"].append(rec)
    return {"ok": True, "mqtt_connected": bridge.connected,
            "probed": len(ids),
            "elapsed_s": round(time.time() - started, 2),
            "online": groups["online"], "offline": groups["offline"],
            "online_count": len(groups["online"]),
            "offline_count": len(groups["offline"])}


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
    existing = {d["id"] for d in db.list_devices()}
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
    return {"ok": True, "deleted": deleted, "missing": missing,
            "errors": errors, "total": len(ids)}


# ================================================================ 设备网络接入
def _network_type(ip: str | None) -> str:
    """根据设备上报 IP 归类为内网/外网。RFC1918 私有地址 = internal，其余 = external。"""
    if not ip:
        return "unknown"
    ip = ip.strip()
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
    db_ids = {d["id"] for d in devs}
    # devices 表里没有、但 telemetry 里出现过的（未接入设备），也列出来供注册。
    # db_ids 保持不变，只放设备表里真实存在的 ID，用于区分 registered / unregistered。
    extra_ids = [r["device_id"] for r in db.query(
        "SELECT DISTINCT device_id FROM telemetry")]
    for eid in extra_ids:
        if eid not in db_ids:
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
        if x["device_id"] not in {d["id"] for d in db.list_devices()}:
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
    keys = ["ai.enabled", "ai.provider", "ai.base_url", "ai.model", "ai.api_key", "ai.prompt"]
    rows = db.query("SELECT key, updated_at FROM app_settings WHERE key IN (?,?,?,?,?,?)", tuple(keys))
    updated = {dict(r)["key"]: dict(r).get("updated_at") for r in rows}
    out = [{"key": k, "value": raw.get(k, ""), "updated_at": updated.get(k)} for k in keys]
    return {"settings": out}


@app.put("/api/settings", dependencies=[Depends(require_admin)])
def update_setting(body: SettingsUpdateIn):
    """写入单条设置。"""
    icu.set_setting(body.key, body.value)
    return {"ok": True, "key": body.key, "value": body.value}


@app.get("/api/realtime")
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


@app.get("/api/history")
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


@app.get("/api/thresholds")
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
    data["alarm_enabled"] = int(data["alarm_enabled"])
    data["alarm_sound"] = int(data["alarm_sound"])
    db.save_thresholds(device_id, data)
    # 下发到设备
    if device_id == "*":
        pushed = [d["id"] for d in db.list_devices() if bridge.push_config(d["id"])]
    else:
        pushed = [device_id] if bridge.push_config(device_id) else []
    return {"ok": True, "device_id": device_id, "pushed_to": pushed}


@app.get("/api/alarms")
def alarms(device: Optional[str] = None, limit: int = Query(50, le=500)):
    return {"alarms": db.list_alarms(device, limit)}


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


@app.post("/api/ingest", dependencies=[Depends(require_user)])
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

    if not device_id:
        raise HTTPException(400, "缺少 device_id")

    # 设备不存在时自动登记（只保证记录存在，不碰 online/last_seen）
    db.ensure_device(device_id)

    level, reason = check_alarm(device_id, temp, hum, pres)
    db.insert_telemetry(device_id, temp, hum, pres, rssi, level, None)
    # 数据写入后同步刷新在线与最近上报时间（与 handle_telemetry 一致）
    db.set_device_online(device_id, True)
    db.set_device_seen(device_id, None)
    record_alarm_transition(device_id, level, reason, temp, hum, pres)
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
    )
    return {"ok": True, "patient_id": pid_id, "pid": body.pid}


@app.get("/api/patients/{pid}", dependencies=[Depends(require_user)])
def get_patient(pid: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return p


@app.put("/api/patients/{pid}", dependencies=[Depends(require_admin)])
def update_patient(pid: str, body: PatientUpdate):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    icu.patient_update(p["id"], **body.model_dump())
    return {"ok": True}


@app.delete("/api/patients/{pid}", dependencies=[Depends(require_admin)])
def delete_patient(pid: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    icu.patient_delete(p["id"])
    return {"ok": True}


# ---------- 患者-设备关联 ----------
@app.post("/api/patients/{pid}/link/{device_id}", dependencies=[Depends(require_admin)])
def link_device(pid: str, device_id: str, role: str = Query("primary", pattern=r"^(primary|secondary)$")):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    try:
        icu.link_device(p["id"], device_id, role)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.delete("/api/patients/{pid}/unlink/{device_id}", dependencies=[Depends(require_admin)])
def unlink_device(pid: str, device_id: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    ok = icu.unlink_device(p["id"], device_id)
    if not ok:
        raise HTTPException(404, "未找到该患者-设备绑定")
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
    hub.broadcast_threadsafe({
        "type": "vital", "patient_id": p["id"], "pid": pid,
        "ts": ts, "source": body.source,
    })
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
    return {"patient_id": p["id"], "count": len(rows), "points": rows}


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
    return {"ok": True, "order_id": oid}


@app.get("/api/patients/{pid}/orders")
def get_orders(pid: str, start: Optional[str] = None, end: Optional[str] = None):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return {"orders": icu.orders_for_patient(p["id"], start, end)}


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
    return {"ok": True, "lab_id": lid}


@app.get("/api/patients/{pid}/lab")
def get_lab(pid: str, start: Optional[str] = None, end: Optional[str] = None):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return {"results": icu.lab_results_for_patient(p["id"], start, end)}


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
    return {"ok": True, "io_id": rid}


@app.get("/api/patients/{pid}/io")
def list_io(pid: str, hours: int = 72):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return {"entries": icu.list_io_log(p["id"], hours)}


@app.get("/api/patients/{pid}/io/balance")
def io_balance(pid: str, hours: int = 24):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return icu.io_balance(p["id"], hours)


@app.get("/api/monitor/sessions", dependencies=[Depends(require_user)])
def list_monitor_sessions(patient_id: Optional[int] = Query(None),
                          device_id: Optional[str] = Query(None),
                          start: Optional[str] = Query(None),
                          end: Optional[str] = Query(None),
                          limit: int = Query(200, ge=1, le=1000)):
    """查询监护记录列表（支持按患者/设备/日期范围过滤）。"""
    sessions = icu.list_monitor_sessions(
        patient_id=patient_id, device_id=device_id,
        start=start, end=end, limit=limit,
    )
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
    """查询某设备当前活跃的监护记录对应的患者（用于切换设备时同步监护页患者）。"""
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
def assess(pid: str, hours: int = 24, ai: bool = Query(False)):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    if ai:
        return icu.assess_with_ai(p["id"], hours)
    return icu.assess_patient(p["id"], hours)


# ---------- 备份 ----------
@app.post("/api/backup", dependencies=[Depends(require_admin)])
def trigger_backup():
    info = icu.do_backup()
    return {"ok": True, **info}


@app.get("/api/backup", dependencies=[Depends(require_admin)])
def list_backups(limit: int = Query(20, ge=1, le=100)):
    return {"backups": icu.list_backups(limit)}


# ================================================================ ICU 重症监护路由组
# ---------- 医生档案 ----------
@app.get("/api/doctors", dependencies=[Depends(require_user)])
def list_doctors(limit: int = Query(200, ge=1, le=1000)):
    return {"doctors": db.doctor_list(limit)}


@app.post("/api/doctors", dependencies=[Depends(require_admin)])
def create_doctor(body: DoctorCreateIn):
    did = db.doctor_create(
        body.name, body.title, body.department, body.contact, body.note,
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
