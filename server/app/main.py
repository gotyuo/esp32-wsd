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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .aggregator import Aggregator
from .models import IngestIn, ThresholdsIn, LoginIn, UserCreate, PasswordChangeIn, SoundPrefIn, RegisterDeviceIn
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
    log.info("bootstrap admin created: %s", ADMIN_USER)


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


# ================================================================ lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    bootstrap_admin()          # 首次启动创建管理员
    db.cleanup_expired_sessions()
    hub.attach_loop(asyncio.get_running_loop())
    bridge.on_telemetry = handle_telemetry
    bridge.on_status = handle_status
    bridge.start()
    aggregator.start()
    log.info("EnvMon backend started")
    yield
    aggregator.stop()
    bridge.stop()


app = FastAPI(title="EnvMon Backend", version="2.0.0", lifespan=lifespan)


# ================================================================ 页面
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ================================================================ 认证 API
@app.post("/api/login")
def login(body: LoginIn, request: Request):
    user = db.get_user_by_name(body.username.strip())
    if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    db.create_session(token, user["id"], ttl_hours=SESSION_TTL_HOURS,
                      ip_addr=request.client.host if request.client else None,
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
            await ws.receive_text()   # 客户端心跳/消息（当前无需处理）
    except WebSocketDisconnect:
        hub.discard(ws)
