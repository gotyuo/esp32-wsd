#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beacon_server.py  —  EnvMon 设备发现 / 握手应答服务端
版本: 20261006-v1.1   （v1.1 新增：--interface 指定出口网卡、日志时间戳、配置校验）

作用
----
EnvMon 固件在未指定服务器时，会向多播组 239.255.1.1:12091 每 4 秒广播探针，
等待服务端应答： {"ip":..,"port":18830,"user":"envmon","pass":"envmon"}
设备收到后写入 NVS、重启并按该配置上报；45 秒无应答则退回 AP 配网门户。

本程序就是那个"应答者"，并解决**多项目共存**问题：
  - v2.0.0 固件裸探针 "ENVMON?"      → 按 default 应答（单项目可用）
  - v2.1.0 固件带 pid 的 JSON 探针   → 按 pid 路由，**只答登记过的项目**，其余静默

用法
----
    python beacon_server.py                          # 用同目录 projects.json
    python beacon_server.py --config my.json
    python beacon_server.py --interface 192.168.2.220   # 多网卡机器指定出口
    python beacon_server.py --probe pid=clinic-a did=xxx # 自测：模拟设备探一次

纯标准库，无需 pip 安装。
Docker 必须 `--network host`（或 compose `network_mode: host`），否则收不到多播。
"""

import argparse
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 12091
VER = "20261006-v1.1"


def log(msg=""):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def load_config(path):
    if not os.path.exists(path):
        log("配置 %s 不存在，用内置示例（仅演示，请改成你的实际参数）" % path)
        return {
            "projects": {
                "clinic-a": {"ip": "192.168.2.220", "port": 18830,
                             "user": "envmon", "pass": "envmon"}
            },
            "default": None,
        }
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 配置校验：早失败好过运行期静默不答
    projects = cfg.get("projects") or {}
    for pid, srv in projects.items():
        if not isinstance(srv, dict) or "ip" not in srv:
            log("配置错误：项目 %s 缺少 ip 字段" % pid)
            sys.exit(2)
    d = cfg.get("default")
    if d is not None and not isinstance(d, dict):
        log("配置错误：default 必须是对象或 null")
        sys.exit(2)
    return cfg


def pick_server(cfg, req):
    """按请求里的项目标识挑选配置；返回 None 表示不应答。"""
    projects = cfg.get("projects", {}) or {}

    # ---- v2.1.0 增强探针：{"probe":"EnvMon","pid":"clinic-a","did":"..."} ----
    pid = req.get("pid") or req.get("project") or req.get("project_id")
    if pid:
        srv = projects.get(pid)
        if srv:
            return dict(srv, _pid=pid)
        for key, srv in projects.items():
            if key.endswith("*") and pid.startswith(key[:-1]):
                return dict(srv, _pid=key)
        log("    └ 未登记的项目 pid=%s -> 不应答" % pid)
        return None

    # ---- v2.0.0 裸探针：无身份 ----
    d = cfg.get("default")
    if d:
        return dict(d, _pid="(default)")
    log("    └ 裸探针无 pid，且 default 为 null -> 不应答")
    return None


def reply_for(srv):
    return {
        "ip": srv.get("ip", "127.0.0.1"),
        "port": int(srv.get("port", 18830)),
        "user": srv.get("user", "envmon"),
        "pass": srv.get("pass", "envmon"),
    }


def serve(cfg, iface=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass

    if iface:
        # 多网卡机器：指定多播出口，避免从错误的网卡发出
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(iface))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

    sock.bind(("", MCAST_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GRP), socket.inet_pton(socket.AF_INET, "0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)

    projects = cfg.get("projects") or {}
    log("=" * 60)
    log(" EnvMon beacon / 握手应答服务端  %s" % VER)
    log("=" * 60)
    log(" 监听      : %s:%d (UDP 多播)" % (MCAST_GRP, MCAST_PORT))
    log(" 出口网卡  : %s" % (iface or "(系统默认路由)"))
    log(" 已登记项目: %s" % (", ".join(projects.keys()) or "（无）"))
    log(" default   : %s" % ("启用（任何设备都可接入）" if cfg.get("default") else "关闭（裸探针不答）"))
    log("=" * 60)
    log(" Ctrl+C 停止")
    log("")

    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            log("已停止")
            return

        raw = data.decode("utf-8", "ignore").strip()
        log("[beacon] %s:%d -> %s" % (addr[0], addr[1], raw))

        req = {}
        if raw.startswith("{"):
            try:
                req = json.loads(raw)
            except Exception:
                req = {}
        else:
            req = {"_raw": raw}

        did = req.get("did") or req.get("device_id") or "-"
        log("    └ did=%s" % did)

        srv = pick_server(cfg, req)
        if not srv:
            continue

        body = reply_for(srv)
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            sock.sendto(payload, (addr[0], addr[1]))
            log("    └ 应答 project=%s -> %s:%d" % (srv.get("_pid"), body["ip"], body["port"]))
            log("")
        except OSError as e:
            log("    └ 发送失败: %s" % e)


def probe(pid=None, did="esp32-test", iface=None):
    msg = json.dumps({"probe": "EnvMon", "pid": pid, "did": did}) if pid else "ENVMON?"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    if iface:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(6)
    sock.sendto(msg.encode("utf-8"), (MCAST_GRP, MCAST_PORT))
    log("[probe] 已发送: %s" % msg)
    try:
        data, addr = sock.recvfrom(2048)
        log("[probe] 收到应答 %s:%d" % (addr[0], addr[1]))
        log("        %s" % data.decode("utf-8", "ignore"))
        return 0
    except socket.timeout:
        log("[probe] 6 秒内无应答")
        log("        排查：服务端是否运行 / Docker 是否 --network host / 防火墙 UDP 12091 / 网卡是否选对")
        return 1


def main():
    ap = argparse.ArgumentParser(description="EnvMon beacon server %s" % VER)
    ap.add_argument("--config", default="projects.json", help="项目路由配置")
    ap.add_argument("--interface", default=None,
                    help="多播出口网卡 IP（多网卡机器必填，如 192.168.2.220）")
    ap.add_argument("--probe", nargs="*", default=None,
                    help="自测模式，例: --probe pid=clinic-a did=esp32-4d3f54")
    args = ap.parse_args()

    if args.probe is not None:
        kv = dict(x.split("=", 1) for x in args.probe if "=" in x)
        sys.exit(probe(kv.get("pid"), kv.get("did", "esp32-test"), args.interface))

    serve(load_config(args.config), args.interface)


if __name__ == "__main__":
    main()
