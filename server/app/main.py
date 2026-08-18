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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .aggregator import Aggregator
from .models import IngestIn, ThresholdsIn, LoginIn, UserCreate, PasswordChangeIn, SoundPrefIn, RegisterDeviceIn
from . import icu
from .models import PatientCreate, PatientUpdate, LinkDeviceIn, VitalIn, OrderIn, LabResultIn
from .mqtt_bridge import MqttBridge

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("envmon.main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
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
    else:
        if open_alarm:
            db.clear_open_alarms(device_id)
            hub.broadcast_threadsafe({"type": "alarm_cleared", "device_id": device_id})


# ================================================================ MQTT 处理器
def handle_telemetry(device_id: str, payload: dict):
    temp = payload.get("t")
    hum = payload.get("h")
    pres = payload.get("p")
    rssi = payload.get("rssi")
    free_heap = payload.get("heap")
    fw = payload.get("fw")

    level, reason = check_alarm(device_id, temp, hum, pres)

    db.upsert_device(device_id, fw_version=fw)
    db.insert_telemetry(device_id, temp, hum, pres, rssi, level, free_heap)
    record_alarm_transition(device_id, level, reason, temp, hum, pres)

    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": device_id,
        "data": {"t": temp, "h": hum, "p": pres, "rssi": rssi,
                 "alarm": level, "fw": fw},
        "ts": db.utcnow(),
    })


def handle_status(device_id: str, online: bool):
    db.upsert_device(device_id)
    db.set_device_online(device_id, online)
    hub.broadcast_threadsafe({"type": "status", "device_id": device_id, "online": online})


def handle_vitals(device_id: str, payload: dict):
    """MQTT 接收来自 ESP32/仪器的多参数生命体征。"""
    from .icu import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT p.id AS patient_id, p.pid AS pid, pd.role FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?",
        (device_id,),
    ).fetchall()
    if not rows:
        return
    target = next((r for r in rows if r["role"] == "primary"), rows[0])
    patient_id = target["patient_id"]
    pid = target["pid"]
    source = payload.get("source", "esp32")
    ts = payload.get("ts", icu._now())
    icu.insert_vital(patient_id, ts, source, source_device=device_id)
    hub.broadcast_threadsafe({"type": "vital", "patient_id": patient_id, "pid": pid, "ts": ts, "source": source})


def handle_order(device_id: str, payload: dict):
    rows = icu.query_rows(
        "SELECT p.id AS patient_id, p.pid AS pid FROM patient_devices pd "
        "JOIN patients p ON p.id=pd.patient_id WHERE pd.device_id=?", (device_id,)
    )
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
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
def health():
    return {"ok": True, "mqtt_connected": bridge.connected, "time": db.utcnow()}


@app.get("/api/devices")
def devices():
    return {"devices": db.list_devices()}


@app.post("/api/devices", dependencies=[Depends(require_admin)])
def register_device(body: RegisterDeviceIn):
    db.register_device(body.device_id, body.name)
    return {"ok": True}


@app.get("/api/devices/{device_id}")
def get_device_detail(device_id: str):
    d = db.device_detail(device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return d


@app.put("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def rename_device(device_id: str, name: str = Query(..., max_length=64),
                  _: Dict = Depends(require_admin)):
    db.rename_device(device_id, name)
    return {"ok": True}


@app.delete("/api/devices/{device_id}", dependencies=[Depends(require_admin)])
def delete_device(device_id: str, _: Dict = Depends(require_admin)):
    db.delete_device(device_id)
    return {"ok": True}


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


@app.post("/api/ingest", dependencies=[Depends(require_admin)])
def ingest(body: IngestIn):
    """HTTP 备用接入通道（无 MQTT 时测试用）。"""
    level, reason = check_alarm(body.device_id, body.temp, body.hum, body.pres)
    db.upsert_device(body.device_id)
    db.insert_telemetry(body.device_id, body.temp, body.hum, body.pres,
                        body.rssi, level, None)
    record_alarm_transition(body.device_id, level, reason,
                            body.temp, body.hum, body.pres)
    hub.broadcast_threadsafe({
        "type": "telemetry", "device_id": body.device_id,
        "data": {"t": body.temp, "h": body.hum, "p": body.pres,
                 "rssi": body.rssi, "alarm": level},
        "ts": db.utcnow(),
    })
    return {"ok": True, "alarm": level}


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
    icu.link_device(p["id"], device_id, role)
    return {"ok": True}


@app.delete("/api/patients/{pid}/unlink/{device_id}", dependencies=[Depends(require_admin)])
def unlink_device(pid: str, device_id: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    ok = icu.unlink_device(p["id"], device_id)
    return {"ok": ok}


@app.get("/api/patients/{pid}/devices")
def patient_devices(pid: str):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return {"devices": icu.devices_for_patient(p["id"])}


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


# ---------- AI 评估 ----------
@app.get("/api/patients/{pid}/assessment")
def assess(pid: str, hours: int = 24):
    p = icu.patient_by_pid(pid)
    if not p:
        raise HTTPException(404, "患者不存在")
    return icu.assess_patient(p["id"], hours)


# ---------- 备份 ----------
@app.post("/api/backup", dependencies=[Depends(require_admin)])
def trigger_backup():
    info = icu.do_backup()
    return {"ok": True, **info}


@app.get("/api/backup", dependencies=[Depends(require_admin)])
def list_backups(limit: int = Query(20, ge=1, le=100)):
    return {"backups": icu.list_backups(limit)}
