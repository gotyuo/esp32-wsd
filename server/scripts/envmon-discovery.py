#!/usr/bin/env python3
"""
envmon-discovery.py — 局域网设备自动发现应答器
版本: 20261006-v2.0   （v2.0 新增：JSON 探针 + 按项目 pid 路由）

用途：在 docker-compose 中作为独立服务启动，监听多播组 239.255.1.1:12091。
     设备上电广播探针，本服务回复「服务器地址 + MQTT 凭据」，设备据此自动保存配置并重启。

支持的两种探针（v2.0 起同时兼容）
--------------------------------
    v2.0.0 旧固件：  "ENVMON?"                                        无身份
    v2.1.x 新固件：  {"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"}

应答 JSON（固件只解析这四个字段）：
    {"ip":"172.22.22.83","port":18830,"user":"envmon","pass":"envmon"}

多项目路由（v2.0 核心能力）
--------------------------
原来的实现只认裸探针字符串，碰到新固件的 JSON 探针会直接丢弃；且无法区分项目，
同一网段多个项目时所有服务器都会应答，设备会连错。

现在引入 DISC_PROJECTS（JSON）按 pid 路由：

    DISC_PROJECTS='{"clinic-a":{"ip":"172.22.22.83","port":18830,"user":"envmon","pass":"envmon"},
                    "ward-*":{"ip":"172.22.22.84","port":18830,"user":"ward","pass":"ward"}}'

    - 命中的 pid -> 用该项目专属配置应答（支持 `前缀*` 通配）
    - 未登记的 pid -> **不应答**（设备继续找别的服务器，天然隔离项目）
    - DISC_ALLOW_LEGACY=0 时，裸探针也不应答（多项目共存建议设为 0）

配置项
------
    MQTT_HOST / MQTT_PORT / MQTT_USER / MQTT_PASS   默认应答的 MQTT 接入信息
    DISC_IP          回复给设备的"服务器地址"（默认：多播接口自身 IP，即本机 LAN IP）
    DISC_PORT        监听端口（默认 12091）
    DISC_MCAST       多播组（默认 239.255.1.1）
    DISC_IFACE       绑定网络接口（如 eth0；留空则用 INADDR_ANY）
    DISC_PROJECTS    按 pid 的项目路由表（JSON 字符串，见上）
    DISC_ALLOW_LEGACY 1/0，是否应答旧固件裸探针（默认 1）

部署：容器需 network_mode: host（推荐，便于获取真实 LAN IP 并跨网段广播）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import socket
import struct
import threading
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("disc")

VERSION = "20261006-v2.0"
MCAST_DEFAULT = "239.255.1.1"
PORT_DEFAULT = 12091
REQ_DEFAULT = "ENVMON?"
MCAST_TTL = 2  # 允许跨一个子网
RECV_BUF = 512  # 原 256；JSON 探针更长，放大避免截断

# ---------- 配置 ----------
MCAST_GROUP = os.environ.get("DISC_MCAST", MCAST_DEFAULT)
DISC_PORT = int(os.environ.get("DISC_PORT", str(PORT_DEFAULT)))
REQ = os.environ.get("DISC_REQ", REQ_DEFAULT).encode("ascii")

# 回复给设备的 MQTT 接入信息（与后端保持一致）
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "18830"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
DISC_IFACE = os.environ.get("DISC_IFACE", "") or None  # eth0 / wlan0

# 回复给设备的"服务器地址"：优先 DISC_IP 环境变量，否则探测本机多播接口 IP
DISC_IP = os.environ.get("DISC_IP", "") or None

# 多项目路由表：{"pid": {"ip":..,"port":..,"user":..,"pass":..}}，key 可用 `前缀*` 通配
DISC_PROJECTS: dict = {}
_raw_projects = os.environ.get("DISC_PROJECTS", "").strip()
if _raw_projects:
    try:
        DISC_PROJECTS = json.loads(_raw_projects)
    except Exception as exc:  # noqa: BLE001
        log.warning("DISC_PROJECTS 不是合法 JSON，已忽略: %s", exc)
        DISC_PROJECTS = {}

# 是否应答旧固件的裸探针（多项目共存时建议 0，避免未登记设备被误认领）
DISC_ALLOW_LEGACY = os.environ.get("DISC_ALLOW_LEGACY", "1").strip() not in (
    "0", "false", "False", "no")


def _iface_ipv4(iface: str) -> Optional[str]:
    """用 ioctl SIOCGIFADDR 读取指定网卡的 IPv4 地址。失败返回 None。"""
    try:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            raw = fcntl.ioctl(
                s.fileno(), 0x8915,  # SIOCGIFADDR
                struct.pack("256s", iface[:15].encode()),
            )
            ip = socket.inet_ntoa(raw[20:24])
            return ip if ip != "127.0.0.1" else None
    except OSError:
        return None


def _lan_ip() -> str:
    """探测要回复给设备的服务器地址。

    优先级：
      1. DISC_IP 环境变量显式指定
      2. DISC_IFACE 指定的接口（用 ioctl SIOCGIFADDR 读该网卡 IPv4）
      3. socket.connect(多播组) 探测默认多播路由接口
      4. 常见接口名遍历兜底

    注意：不要用 socket.connect() 作为唯一手段——宿主机上默认多播路由
    常指向 docker0（172.x），设备无法访问，会导致设备连不上服务器。
    """
    if DISC_IP:
        return DISC_IP

    if DISC_IFACE:
        ip = _iface_ipv4(DISC_IFACE)
        if ip:
            return ip

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((MCAST_GROUP, DISC_PORT))
            return s.getsockname()[0]
    except Exception:
        pass

    for iface in ("eth0", "wlan0", "enp2s0", "enp1s0", "ens33", "wlp1s0"):
        ip = _iface_ipv4(iface)
        if ip:
            return ip
    return "127.0.0.1"


def _build_reply(srv: Optional[dict] = None) -> bytes:
    """构造应答。srv 为 None 时使用全局默认配置。"""
    srv = srv or {}
    ip = srv.get("ip") or _lan_ip()
    try:
        port = int(srv.get("port") or MQTT_PORT)
    except (TypeError, ValueError):
        port = MQTT_PORT
    payload = {"ip": ip, "port": port}
    user = srv.get("user") or MQTT_USER
    psw = srv.get("pass") or MQTT_PASS
    if user:
        payload["user"] = user
    if psw:
        payload["pass"] = psw
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _parse_probe(data: bytes) -> tuple[str, dict]:
    """解析探针。返回 (kind, req)，kind ∈ {'json','legacy','unknown'}。"""
    raw = data.decode("utf-8", "ignore").strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return "json", obj
        except Exception:  # noqa: BLE001
            pass
        return "unknown", {}
    if data.strip() == REQ:
        return "legacy", {}
    return "unknown", {}


def _pick_server(kind: str, req: dict) -> tuple[Optional[dict], str, bool]:
    """挑选应答配置。

    返回 (srv, label, ok)：
        ok=False            -> 不应答
        srv=None, ok=True   -> 用全局默认配置应答
        srv=dict            -> 用该项目的配置应答
    """
    if kind == "json":
        pid = req.get("pid") or req.get("project") or req.get("project_id")
        did = req.get("did") or req.get("device_id") or "-"
        if pid:
            srv = DISC_PROJECTS.get(pid)
            if srv is None:
                for key, val in DISC_PROJECTS.items():
                    if key.endswith("*") and pid.startswith(key[:-1]):
                        srv = val
                        break
            if srv is None:
                log.info("未登记的项目 pid=%s did=%s -> 不应答（避免跨项目误认领）", pid, did)
                return None, f"pid={pid}", False
            return srv, f"pid={pid} did={did}", True
        # 是 JSON 但没有 pid：退化为按 legacy 规则处理
        log.debug("JSON 探针缺 pid，按 legacy 规则处理: %s", req)

    if kind == "legacy":
        if DISC_ALLOW_LEGACY:
            return None, "(legacy 裸探针)", True
        log.info("收到裸探针但 DISC_ALLOW_LEGACY=0 -> 不应答")
        return None, "(legacy)", False

    return None, "(unknown)", False


class MulticastResponder:
    def __init__(self, mcast: str, port: int, ttl: int = MCAST_TTL) -> None:
        self.mcast = mcast
        self.port = port
        self.ttl = ttl
        self._socket: Optional[socket.socket] = None
        # 注意：Event 默认 unset，必须显式 set()，否则 while is_set() 立即退出
        self._alive = threading.Event()
        self._alive.set()
        self.stats = {"recv": 0, "replied": 0, "ignored": 0}

    def _mksocket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.settimeout(2.0)

        if DISC_IFACE:
            try:
                # 网卡名不是主机名，gethostbyname 会失败；用 ioctl 取该网卡 IPv4
                iface_ip = _iface_ipv4(DISC_IFACE)
                if iface_ip:
                    s.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(iface_ip),
                    )
                else:
                    log.warning("cannot resolve IP of %s", DISC_IFACE)
            except OSError as e:
                log.warning("IP_MULTICAST_IF %s failed: %s", DISC_IFACE, e)
            # SO_BINDTODEVICE 需要 CAP_NET_RAW；缺该权限时降级为仅绑定接口地址
            try:
                s.setsockopt(
                    socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                    DISC_IFACE.encode("utf-8") + b"\0",
                )
            except (OSError, AttributeError) as e:
                log.warning(
                    "SO_BINDTODEVICE %s denied (%s) — falling back to IP_MULTICAST_IF only",
                    DISC_IFACE, e,
                )

        mreq = socket.inet_aton(self.mcast) + socket.inet_aton("0.0.0.0")
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.ttl)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

        try:
            s.bind((self.mcast, self.port))
        except OSError as e:
            # Windows 不允许 bind 到多播地址（WinError 10022），Linux 可以。
            # 回退为监听所有地址；多播成员资格已由上面的 IP_ADD_MEMBERSHIP 声明，
            # 功能等价，仅便于在 Windows 上本地验证。
            log.warning("bind(%s:%d) failed (%s) — falling back to 0.0.0.0:%d",
                        self.mcast, self.port, e, self.port)
            s.bind(("", self.port))
        return s

    def run(self) -> None:
        self._socket = self._mksocket()
        host = _lan_ip()
        log.info(
            "envmon-discovery %s | listening %s:%d (iface=%s reply_ip=%s)",
            VERSION, self.mcast, self.port, DISC_IFACE or "auto", host,
        )
        log.info(
            "  legacy(裸探针)应答=%s | 已登记项目=%s",
            "on" if DISC_ALLOW_LEGACY else "off",
            ", ".join(DISC_PROJECTS.keys()) if DISC_PROJECTS else "(无，仅默认应答)",
        )

        while self._alive.is_set():
            try:
                data, addr = self._socket.recvfrom(RECV_BUF)
            except socket.timeout:
                continue
            except OSError as e:
                if self._alive.is_set():
                    log.error("recvfrom error: %s", e)
                break

            self.stats["recv"] += 1
            peer_ip, peer_port = addr[:2]

            kind, req = _parse_probe(data)
            if kind == "unknown":
                log.debug("ignoring non-matching probe from %s: %r", peer_ip, data)
                continue

            srv, label, ok = _pick_server(kind, req)
            if not ok:
                self.stats["ignored"] += 1
                continue

            reply = _build_reply(srv)
            try:
                self._socket.sendto(reply, addr)
                self.stats["replied"] += 1
            except OSError as e:
                log.error("sendto %s:%d failed: %s", peer_ip, peer_port, e)
                continue

            log.info(
                "beacon %s:%d %s -> replied %s",
                peer_ip, peer_port, label,
                json.loads(reply.decode("utf-8")),
            )

    def stop(self) -> None:
        self._alive.clear()
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass


def _graceful(responder: MulticastResponder) -> None:
    import signal

    def handler(signum: int, _frame: object) -> None:
        log.info(
            "signal %d received, shutting down (recv=%d replied=%d ignored=%d)",
            signum, responder.stats["recv"], responder.stats["replied"],
            responder.stats["ignored"],
        )
        responder.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def main() -> None:
    resp = MulticastResponder(MCAST_GROUP, DISC_PORT)
    _graceful(resp)
    resp.run()


if __name__ == "__main__":
    main()
