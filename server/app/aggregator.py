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

# 原始遥测（telemetry）保留期。2026-09-22 按需求从 7 天改为 2 天：
# 遥测是 10 秒/条的高频数据（3 台设备 ~7 万行/7天），只留最近 2 天足够趋势回看；
# 长期趋势靠 telemetry_1m（MINUTE_RETENTION_DAYS=400）分钟聚合，不丢细节。
# 改此值后需手动清理一次历史，之后由 Aggregator 每轮自动按此值删。
RAW_RETENTION_DAYS = int(os.environ.get("RAW_RETENTION_DAYS", "2"))
MINUTE_RETENTION_DAYS = int(os.environ.get("MINUTE_RETENTION_DAYS", "400"))
OFFLINE_TIMEOUT_S = int(os.environ.get("OFFLINE_TIMEOUT_S", "120"))


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
        # BUG-017: 原 min(wait, 15) 导致每 ~15s 全量轮询，改为 min(wait, 60)
        _last_cleanup = 0.0
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            next_min = (now + timedelta(minutes=1)).replace(second=1, microsecond=0)
            wait = (next_min - now).total_seconds()
            if self._stop.wait(min(wait, 60)):
                break
            try:
                self._aggregate_last_minute()
                self._mark_offline_devices()
                # BUG-017: 清理节流至每 60s 一次，避免全表扫描过于频繁
                t = time.time()
                if t - _last_cleanup >= 60:
                    self._cleanup()
                    _last_cleanup = t
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
        # telemetry.ts 由服务器写入时带毫秒（如 2026-09-22T15:40:07.713Z），
        # 而 cutoff 历史上用 %H:%M:%SZ 不带毫秒。SQLite 按字符串比较，
        # '.'(ASCII 46) < 'Z'(ASCII 90)，于是 "07.713Z" < "07Z" 恒成立，
        # 导致 cutoff 那一秒内新写入的行被误删（每轮清理随机丢 0-1 条）。
        # 修法：cutoff 补上 .000Z 与 ts 位数对齐，"07.713Z" > "07.000Z" 成立，不再误删。
        raw_cut = (datetime.now(timezone.utc) - timedelta(days=RAW_RETENTION_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")
        db.execute("DELETE FROM telemetry WHERE ts < ?", (raw_cut,))
        # telemetry_1m.ts_minute 形如 2026-09-22T15:37（无秒无毫秒），原格式正确，勿改。
        min_cut = (datetime.now(timezone.utc) - timedelta(days=MINUTE_RETENTION_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M")
        db.execute("DELETE FROM telemetry_1m WHERE ts_minute < ?", (min_cut,))
        # alarms.ts 为秒级不带毫秒，原格式与之对齐，无需改动。
        db.execute(
            "DELETE FROM alarms WHERE ts < ?",
            ((datetime.now(timezone.utc) - timedelta(days=MINUTE_RETENTION_DAYS))
             .strftime("%Y-%m-%dT%H:%M:%SZ"),),
        )
