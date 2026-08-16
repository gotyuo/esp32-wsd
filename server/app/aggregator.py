"""后台聚合任务（独立线程）:

1. 每分钟把上一分钟的原始遥测聚合写入 telemetry_1m（每分钟环境数据记录机制）
2. 数据保留策略: 原始数据保留 N 天，分钟数据保留 M 天
3. 设备离线检测: 超过 OFFLINE_TIMEOUT 秒无上报即标记离线
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("envmon.agg")

RAW_RETENTION_DAYS = int(os.environ.get("RAW_RETENTION_DAYS", "7"))
MINUTE_RETENTION_DAYS = int(os.environ.get("MINUTE_RETENTION_DAYS", "400"))
OFFLINE_TIMEOUT_S = int(os.environ.get("OFFLINE_TIMEOUT_S", "90"))


class Aggregator:
    def __init__(self, on_device_offline=None):
        self._stop = threading.Event()
        self._thread = None
        self._on_device_offline = on_device_offline

    def start(self):
        self._thread = threading.Thread(target=self._run, name="aggregator", daemon=True)
        self._thread.start()
        log.info("aggregator started (raw %dd, minute %dd)",
                 RAW_RETENTION_DAYS, MINUTE_RETENTION_DAYS)

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------ main loop
    def _run(self):
        # 等到下一个整分钟 +1s 再开始聚合
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            next_min = (now + timedelta(minutes=1)).replace(second=1, microsecond=0)
            wait = (next_min - now).total_seconds()
            if self._stop.wait(min(wait, 15)):
                break
            try:
                self._aggregate_last_minute()
                self._mark_offline_devices()
                self._cleanup()
            except Exception as e:  # noqa: BLE001
                log.exception("aggregator error: %s", e)

    # ------------------------------------------------------------ steps
    def _aggregate_last_minute(self):
        now = datetime.now(timezone.utc)
        target = (now - timedelta(minutes=1))
        minute_key = target.strftime("%Y-%m-%dT%H:%M")
        start = minute_key + ":00Z"
        end = (target + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00Z")

        rows = db.query(
            """
            SELECT device_id,
                   AVG(temp_c) AS temp_avg, MIN(temp_c) AS temp_min, MAX(temp_c) AS temp_max,
                   AVG(hum_pct) AS hum_avg, AVG(pres_hpa) AS pres_hpa_avg,
                   COUNT(*) AS samples, MAX(alarm_level) AS alarm_max
            FROM telemetry
            WHERE ts >= ? AND ts < ?
            GROUP BY device_id
            """,
            (start, end),
        )
        for r in rows:
            db.execute(
                """
                INSERT OR IGNORE INTO telemetry_1m
                    (device_id, ts_minute, temp_avg, temp_min, temp_max,
                     hum_avg, pres_hpa_avg, samples, alarm_max)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (r["device_id"], minute_key, r["temp_avg"], r["temp_min"], r["temp_max"],
                 r["hum_avg"], r["pres_hpa_avg"], r["samples"], r["alarm_max"]),
            )
        if rows:
            log.info("aggregated minute %s (%d devices)", minute_key, len(rows))

    def _mark_offline_devices(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_TIMEOUT_S)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = db.query("SELECT id FROM devices WHERE online=1 AND last_seen < ?", (cutoff,))
        for r in rows:
            db.set_device_online(r["id"], False)
            log.info("device %s marked offline", r["id"])
            if self._on_device_offline:
                self._on_device_offline(r["id"])

    def _cleanup(self):
        raw_cut = (datetime.now(timezone.utc) - timedelta(days=RAW_RETENTION_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("DELETE FROM telemetry WHERE ts < ?", (raw_cut,))
        min_cut = (datetime.now(timezone.utc) - timedelta(days=MINUTE_RETENTION_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M")
        db.execute("DELETE FROM telemetry_1m WHERE ts_minute < ?", (min_cut,))
        db.execute(
            "DELETE FROM alarms WHERE ts < ?",
            ((datetime.now(timezone.utc) - timedelta(days=MINUTE_RETENTION_DAYS))
             .strftime("%Y-%m-%dT%H:%M:%SZ"),),
        )
