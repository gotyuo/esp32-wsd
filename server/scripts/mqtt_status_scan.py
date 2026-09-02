#!/usr/bin/env python3
"""设备在线状态扫描 —— 独立验证脚本

在 broker 上读取各设备的 status 【保留消息】，判定在线/离线。
用法（在 backend 容器内）:
    docker exec envmon-backend python3 /tmp/mqtt_status_scan.py

记录了三个实测过的 paho-mqtt 1.6.1 陷阱，详见 app/main.py 中
_scan_retained_status() 的文档字符串。
"""
import time, threading, importlib.metadata
import paho.mqtt.client as mqtt

HOST, PORT, USER, PASS = "mosquitto", 1883, "envmon", "envmon"
DEVICES = ["8266-v3", "8266", "8266-2", "esp32-demo-001", "envmon-f6e380", "231"]

print("paho-mqtt", importlib.metadata.version("paho-mqtt"))


def scan_async():
    """陷阱 1 的复现: connect_async + 立即 subscribe -> 订阅静默失败, 收不到任何消息。"""
    seen = {}
    c = mqtt.Client(client_id="bad-%d" % int(time.time() * 1000), clean_session=True)
    c.username_pw_set(USER, PASS)
    c.on_message = lambda _c, _u, m: seen.__setitem__(
        m.topic.split("/")[1], m.payload.decode().strip().lower())
    c.connect_async(HOST, PORT)
    c.loop_start()
    c.subscribe("envmon/+/status", qos=1)
    time.sleep(2)
    c.loop_stop()
    c.disconnect()
    return seen


def scan_sync():
    """正确做法: 阻塞 connect 等到 CONNACK, 用 on_subscribe + Event 确认 SUBACK。"""
    seen = {}
    suback = threading.Event()
    c = mqtt.Client(client_id="good-%d" % int(time.time() * 1000), clean_session=True)
    c.username_pw_set(USER, PASS)
    c.on_message = lambda _c, _u, m: seen.__setitem__(
        m.topic.split("/")[1], m.payload.decode().strip().lower())
    c.on_subscribe = lambda _c, _u, _m, _g: suback.set()
    c.connect(HOST, PORT, keepalive=15)   # 阻塞到 CONNACK
    c.loop_start()
    c.subscribe("envmon/+/status", qos=1)
    suback.wait(2.0)                       # 等 SUBACK 确认订阅生效
    time.sleep(1.0)
    c.loop_stop()
    c.disconnect()
    return seen


a = scan_async()
b = scan_sync()
print("\nconnect_async  (错误): 收到 %d 条" % len(a))
for d in DEVICES:
    print("    %-18s => %s" % (d, a.get(d, "<MISSING>")))
print("\ncorrect blocking (正确): 收到 %d 条" % len(b))
for d in DEVICES:
    print("    %-18s => %s" % (d, b.get(d, "<MISSING>")))
