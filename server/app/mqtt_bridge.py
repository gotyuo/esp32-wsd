"""MQTT 接入桥：paho-mqtt 后台线程。

订阅主题:
    envmon/+/telemetry   设备遥测数据
    envmon/+/status      设备在线状态 (LWT 遗嘱)
    envmon/+/config/req  设备请求下发配置
    envmon/+/config/ack  设备配置回执

发布主题:
    envmon/{id}/config   下发阈值/参数配置
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from . import db

log = logging.getLogger("envmon.mqtt")

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "18830"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")


class MqttBridge:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        # 由 main 注入的回调
        self.on_telemetry: Optional[Callable] = None
        self.on_status: Optional[Callable] = None
        self.on_ack: Optional[Callable] = None
        self.on_vitals: Optional[Callable] = None
        self.on_order: Optional[Callable] = None
        self.on_lab: Optional[Callable] = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.client = mqtt.Client(client_id="envmon-server", clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        # 异步连接：内部自动重连
        try:
            self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
            log.info("MQTT bridge started -> %s:%s", MQTT_HOST, MQTT_PORT)
        except Exception as e:  # noqa: BLE001
            log.error("MQTT bridge failed to start: %s", e)

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    # ------------------------------------------------------------ callbacks
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            client.subscribe("envmon/+/telemetry", qos=1)
            client.subscribe("envmon/+/status", qos=1)
            client.subscribe("envmon/+/config/req", qos=1)
            client.subscribe("envmon/+/config/ack", qos=1)
            log.info("MQTT connected, topics subscribed")
        else:
            log.warning("MQTT connect rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            log.warning("MQTT unexpected disconnect rc=%s, auto-reconnecting...", rc)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[0] != "envmon":
            return
        device_id, kind = parts[1], parts[2]
        try:
            if kind == "telemetry":
                payload = json.loads(msg.payload.decode("utf-8", "ignore"))
                if self.on_telemetry:
                    self.on_telemetry(device_id, payload)
            elif kind == "status":
                online = msg.payload.decode("utf-8", "ignore").strip().lower() == "online"
                if self.on_status:
                    self.on_status(device_id, online)
            elif kind == "config/req":
                self.push_config(device_id)
            elif kind == "config/ack":
                log.info("config ack from %s", device_id)
                if self.on_ack:
                    self.on_ack(device_id)
            elif kind == "vitals":
                payload = json.loads(msg.payload.decode("utf-8", "ignore"))
                if self.on_vitals:
                    self.on_vitals(device_id, payload)
            elif kind == "order":
                payload = json.loads(msg.payload.decode("utf-8", "ignore"))
                if self.on_order:
                    self.on_order(device_id, payload)
            elif kind == "lab":
                payload = json.loads(msg.payload.decode("utf-8", "ignore"))
                if self.on_lab:
                    self.on_lab(device_id, payload)
        except Exception as e:  # noqa: BLE001
            log.exception("handle %s from %s failed: %s", kind, device_id, e)

    # ------------------------------------------------------------ publish
    def push_config(self, device_id: str) -> bool:
        """把数据库中该设备的有效阈值下发给设备。"""
        if not self.client or not self.connected:
            log.warning("push_config skipped (MQTT offline) for %s", device_id)
            return False
        th = db.get_thresholds(device_id)
        if not th:
            return False
        payload = {
            "temp_min": th["temp_min"],
            "temp_max": th["temp_max"],
            "hum_min": th["hum_min"],
            "hum_max": th["hum_max"],
            "pres_min": th["pres_min"],
            "pres_max": th["pres_max"],
            "report_interval": th["report_interval"],
            "alarm_enabled": bool(th["alarm_enabled"]),
            "alarm_sound": bool(th["alarm_sound"]),
            # 设备 HTTP 拉取语音用的 Web 端口（与 MQTT 端口不同）
            "web_port": int(os.environ.get("WEB_PORT", "12090")),
        }
        topic = f"envmon/{device_id}/config"
        res = self.client.publish(topic, json.dumps(payload), qos=1)
        log.info("config pushed to %s rc=%s", topic, res.rc)
        return res.rc == mqtt.MQTT_ERR_SUCCESS
