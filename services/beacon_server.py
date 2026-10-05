#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beacon_server.py  —  EnvMon 设备发现 / 握手应答服务端
版本: 20261006-v1.0

作用
----
EnvMon 固件(v2.0.0)在上电且未指定服务器时，会向多播组 239.255.1.1:12091
每 4 秒广播一次探针（固件原版是裸字符串 "ENVMON?"），等待服务端应答：
    {"ip":"192.168.1.100","port":18830,"user":"envmon","pass":"envmon"}
设备收到后写入 NVS、重启，随后按该配置上报。45 秒内没应答则退回 AP 配网。

本程序就是那个缺失的"服务端应答者"，并解决**多项目共存**问题：
  - 兼容原版裸 "ENVMON?" 探针（回 default 配置）
  - 支持增强探针（携带 pid/did），**按 project 路由下发该项目专属配置**
    不匹配的项目一律不答，设备自然连到"自己家"的服务器

用法
----
    python beacon_server.py                       # 用同目录 projects.json
    python beacon_server.py --config my.json
    python beacon_server.py --probe pid=clinic-a did=esp32-4d3f54   # 自测：模拟设备探一次

纯标准库，无需 pip 安装。

Docker 注意：多播需要宿主机网络 —— 用 `docker run --network host`，
或 compose 里 `network_mode: host`，否则容器收不到多播包。
"""

import argparse
import json
import os
import socket
import struct
import sys
import time

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 12091
VER = "20261006-v1.0"


def load_config(path):
    if not os.path.exists(path):
        print("[配置] 找不到 %s，用内置示例（仅演示，请改成你的实际参数）" % path)
        return {
            "projects": {
                "clinic-a": {"ip": "192.168.2.220", "port": 18830,
                             "user": "envmon", "pass": "envmon"}
            },
            "default": None,     # 设为 dict 才会对裸 "ENVMON?" 应答
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pick_server(cfg, req):
    """按请求里的 project 标识挑选该项目专属配置。
    返回 dict 或 None（None = 不应答，设备继续找别的服务器）。"""
    projects = cfg.get("projects", {}) or {}

    # ---- 增强探针：{"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"} ----
    pid = req.get("pid") or req.get("project") or req.get("project_id")
    if pid:
        srv = projects.get(pid)
        if srv:
            return dict(srv, _pid=pid)
        # 支持 pid 前缀匹配（如 clinic-* ）
        for key, srv in projects.items():
            if key.endswith("*") and pid.startswith(key[:-1]):
                return dict(srv, _pid=key)
        print("        └ 未登记的项目 pid=%s -> 不应答" % pid)
        return None

    # ---- 兼容：固件原版裸 "ENVMON?"，无身份信息 ----
    d = cfg.get("default")
    if d:
        return dict(d, _pid="(default)")
    print("        └ 裸探针无 pid，且未配置 default -> 不应答")
    return None


def reply_for(srv):
    """组装固件认识的应答体（固件只解析 ip/port/user/pass）。"""
    return {
        "ip": srv.get("ip", "127.0.0.1"),
        "port": int(srv.get("port", 18830)),
        "user": srv.get("user", "envmon"),
        "pass": srv.get("pass", "envmon"),
    }


def serve(cfg):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.bind(("", MCAST_PORT))

    # 加入多播组
    mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GRP), socket.inet_pton(socket.AF_INET, "0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)

    print("=" * 68)
    print(" EnvMon beacon / 握手应答服务端  %s" % VER)
    print("=" * 68)
    print(" 监听      : %s:%d (UDP 多播)" % (MCAST_GRP, MCAST_PORT))
    print(" 已登记项目: %s" % (", ".join((cfg.get("projects") or {}).keys()) or "（无）"))
    print(" default   : %s" % ("启用" if cfg.get("default") else "关闭（裸探针不答）"))
    print("=" * 68)
    print(" Ctrl+C 停止\n", flush=True)

    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            print("\n已停止")
            return

        raw = data.decode("utf-8", "ignore").strip()
        print("[beacon] %s:%d -> %s" % (addr[0], addr[1], raw), flush=True)

        # 解析请求：优先 JSON，否则当作裸探针字符串
        req = {}
        if raw.startswith("{"):
            try:
                req = json.loads(raw)
            except Exception:
                req = {}
        else:
            req = {"_raw": raw}

        did = req.get("did") or req.get("device_id") or "-"
        print("        └ did=%s" % did, flush=True)

        srv = pick_server(cfg, req)
        if not srv:
            continue

        body = reply_for(srv)
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            sock.sendto(payload, (addr[0], addr[1]))   # 单播回复设备的源端口
            print("        └ 应答 project=%s -> %s:%d\n"
                  % (srv.get("_pid"), body["ip"], body["port"]), flush=True)
        except OSError as e:
            print("        └ 发送失败: %s" % e, flush=True)


def probe(pid=None, did="esp32-test"):
    """自测：模拟设备发一次探针，看服务端是否应答。"""
    msg = json.dumps({"probe": "EnvMon", "pid": pid, "did": did}) if pid else "ENVMON?"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(6)
    sock.sendto(msg.encode("utf-8"), (MCAST_GRP, MCAST_PORT))
    print("[probe] 已发送: %s" % msg)
    try:
        data, addr = sock.recvfrom(2048)
        print("[probe] 收到应答 %s:%d" % (addr[0], addr[1]))
        print("        ", data.decode("utf-8", "ignore"))
        return 0
    except socket.timeout:
        print("[probe] 6 秒内无应答 —— 检查服务端是否运行 / Docker 是否 --network host / 防火墙")
        return 1


def main():
    ap = argparse.ArgumentParser(description="EnvMon beacon server %s" % VER)
    ap.add_argument("--config", default="projects.json", help="项目配置文件")
    ap.add_argument("--probe", nargs="*", default=None,
                    help="自测模式，例: --probe pid=clinic-a did=esp32-4d3f54")
    args = ap.parse_args()

    if args.probe is not None:
        kv = dict(x.split("=", 1) for x in args.probe if "=" in x)
        sys.exit(probe(kv.get("pid"), kv.get("did", "esp32-test")))

    cfg = load_config(args.config)
    serve(cfg)


if __name__ == "__main__":
    main()
