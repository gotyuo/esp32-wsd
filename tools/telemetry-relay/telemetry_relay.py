#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telemetry_relay.py  —  EnvMon 固件 HTTP→HTTPS 上报转发器
版本: 20261006-v1.0

作用
----
EnvMon 固件(v2.0.0)的上报地址是硬编码明文 http://<host>:<hport>/api/telemetry，
不支持 https。若服务器只提供 https(如 fnOS 穿透域名 :443)，直接填域名必然失败。
本程序在一台与 ESP32 同网、长时间开机的机器上运行：
    ESP32 --http--> 本转发器 --https--> 真实服务器
固件侧无需任何改动，配网页面照常填本机 IP 与监听端口即可。

用法
----
    python telemetry_relay.py                       # 默认监听 12090, 转发到内置默认地址
    python telemetry_relay.py --port 12090 --target https://your.domain
    python telemetry_relay.py --target https://your.domain/api/telemetry --fixed-path

依赖: 仅 Python 标准库(3.7+)，无需 pip install。
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TARGET = "https://8ee1630c307b-0.hotyuo161.fnos.net"
TIMEOUT = 15

# 逐跳头部，不能转发给上游
HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "transfer-encoding", "upgrade",
}


class RelayHandler(BaseHTTPRequestHandler):
    target = DEFAULT_TARGET
    fixed_path = None
    verbose = True

    protocol_version = "HTTP/1.1"

    def _relay(self):
        # 读取请求体
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        # 组装上游 URL：默认"透传路径"，可用 --fixed-path 锁定为固定路径
        if self.fixed_path:
            upstream = self.target.rstrip("/") + self.fixed_path
        else:
            parsed = urllib.parse.urlsplit(self.path)
            upstream = self.target.rstrip("/") + (parsed.path or "/")
            if parsed.query:
                upstream += "?" + parsed.query

        # 构造转发请求
        req = urllib.request.Request(upstream, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP:
                req.add_header(k, v)
        req.add_header("User-Agent", "telemetry-relay/20261006-v1.0")

        t0 = time.time()
        status, resp_body, ctype = 502, b"", "text/plain"
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                status = r.status
                resp_body = r.read()
                ctype = r.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            # 上游有响应(4xx/5xx) —— 说明链路是通的，只是服务端拒绝/路径不对
            status = e.code
            resp_body = e.read()
            ctype = e.headers.get("Content-Type", "text/plain") if e.headers else "text/plain"
        except Exception as e:
            resp_body = ("relay upstream error: %s: %s" % (type(e).__name__, e)).encode("utf-8")

        elapsed = (time.time() - t0) * 1000

        if self.verbose:
            preview = (body or b"")[:220]
            print("[relay] %s %s -> %s | upstream=%s | code=%d | %.0fms"
                  % (self.command, self.path, self.target, upstream, status, elapsed), flush=True)
            if preview:
                print("        body: %s" % preview.decode("utf-8", "ignore"), flush=True)
            if status >= 400:
                print("        resp: %s" % resp_body[:220].decode("utf-8", "ignore"), flush=True)

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        if resp_body:
            self.wfile.write(resp_body)

    do_GET = _relay
    do_POST = _relay
    do_PUT = _relay

    def log_message(self, fmt, *args):
        pass  # 自定义日志已在 _relay 打印，屏蔽默认访问日志


def main():
    ap = argparse.ArgumentParser(description="EnvMon HTTP->HTTPS telemetry relay 20261006-v1.0")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    ap.add_argument("--port", type=int, default=12090, help="监听端口，默认 12090（固件 hport 默认值）")
    ap.add_argument("--target", default=DEFAULT_TARGET, help="上游 https 基地址")
    ap.add_argument("--fixed-path", default=None,
                    help="强制转发到固定路径(如 /api/telemetry)，默认按请求路径透传")
    ap.add_argument("--quiet", action="store_true", help="不打印每条转发详情")
    args = ap.parse_args()

    RelayHandler.target = args.target
    RelayHandler.fixed_path = args.fixed_path
    RelayHandler.verbose = not args.quiet

    print("=" * 66)
    print(" telemetry_relay  20261006-v1.0")
    print("=" * 66)
    print(" 监听      : http://%s:%d/" % (args.host, args.port))
    print(" 上游      : %s" % args.target)
    print(" 路径模式  : %s" % ("固定 %s" % args.fixed_path if args.fixed_path else "透传(跟随请求路径)"))
    print(" 固件配置  : 配网页 服务器地址=本机IP, HTTP端口=%d" % args.port)
    print(" 超时      : %ds" % TIMEOUT)
    print("=" * 66)
    print(" Ctrl+C 停止\n", flush=True)

    srv = ThreadingHTTPServer((args.host, args.port), RelayHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
