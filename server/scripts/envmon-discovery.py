#!/usr/bin/env python3
"""
envmon-discovery.py — 局域网设备自动发现应答器
=================================================
用途：在 docker-compose 中作为独立服务启动，监听多播组
     239.255.1.1:12091。收到设备端 "ENVMON?" 探测报文后，
     回复本机 LAN IP + MQTT 凭据 JSON，设备据此自动保存配置并重启。

应答 JSON 格式（单行）：
    {"ip":"192.168.1.100","port":18830,"user":"envmon","pass":"envmon"}

配置来源（与后端共享同一份 MQTT 环境变量，也可单独覆盖）：
    MQTT_HOST / MQTT_PORT / MQTT_USER / MQTT_PASS  -> 设备端需要填的值
    DISC_IP         -> 回复给设备的"服务器地址"（默认：多播接口自身 IP，即本机 LAN IP）
    DISC_PORT       -> 监听端口（默认 12091）
    DISC_MCAST      -> 多播组（默认 239.255.1.1）
    DISC_IFACE      -> 绑定网络接口（如 eth0；留空则用 INADDR_ANY）

部署：docker-compose.yaml 新增服务（见文件末尾 DOC），或直接用本脚本运行。
      - 容器需 net.ipv4.ip_forward 开启（多播跨子网）
      - 容器 network_mode: host（推荐，便于获取真实 LAN IP 并跨网段广播）
      - 或使用 compose 的 extra_hosts + 内网 IP 白名单
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

MCAST_DEFAULT = "239.255.1.1"
PORT_DEFAULT = 12091
REQ_DEFAULT = "ENVMON?"
MCAST_TTL = 2  # 允许跨一个子网

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


def _build_reply() -> bytes:
    payload = {
        "ip": _lan_ip(),
        "port": MQTT_PORT,
    }
    if MQTT_USER:
        payload["user"] = MQTT_USER
    if MQTT_PASS:
        payload["pass"] = MQTT_PASS
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class MulticastResponder:
    def __init__(self, mcast: str, port: int, ttl: int = MCAST_TTL) -> None:
        self.mcast = mcast
        self.port = port
        self.ttl = ttl
        self._socket: Optional[socket.socket] = None
        # 注意：Event 默认 unset，必须显式 set()，否则 while is_set() 立即退出
        self._alive = threading.Event()
        self._alive.set()

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
                        socket.SOL_IP, socket.IP_MULTICAST_IF,
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
        s.setsockopt(socket.SOL_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_TTL, self.ttl)
        s.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_LOOP, 1)

        s.bind((self.mcast, self.port))
        return s

    def run(self) -> None:
        self._socket = self._mksocket()
        reply = _build_reply()
        host = _lan_ip()
        log.info(
            "listening multicast %s:%d (iface=%s reply_ip=%s) — waiting for '%s' beacons",
            self.mcast, self.port, DISC_IFACE or "auto", host, REQ.decode(),
        )

        while self._alive.is_set():
            try:
                data, addr = self._socket.recvfrom(256)
            except socket.timeout:
                continue
            except OSError as e:
                if self._alive.is_set():
                    log.error("recvfrom error: %s", e)
                break

            peer_ip, peer_port = addr[:2]
            if data.strip() != REQ:
                log.debug("ignoring non-matching probe from %s: %r", peer_ip, data)
                continue

            try:
                self._socket.sendto(reply, addr)
            except OSError as e:
                log.error("sendto %s:%d failed: %s", peer_ip, peer_port, e)
                continue

            log.info(
                "beacon from %s:%d -> replied %s",
                peer_ip, peer_port,
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
        log.info("signal %d received, shutting down", signum)
        responder.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def main() -> None:
    resp = MulticastResponder(MCAST_GROUP, DISC_PORT)
    _graceful(resp)
    resp.run()


if __name__ == "__main__":
    main()
