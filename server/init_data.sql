-- ============================================================
-- 初始化数据：插入全局默认阈值（device_id='*'）
-- 首次部署执行: sqlite3 envmon.db < init_data.sql
-- （服务器启动时也会自动补齐，可选手动执行）
-- ============================================================
INSERT OR IGNORE INTO thresholds (
    device_id, temp_min, temp_max, hum_min, hum_max,
    pres_min, pres_max, report_interval, alarm_enabled, alarm_sound, updated_at
) VALUES (
    '*', 5.0, 40.0, 20.0, 90.0,
    950.0, 1050.0, 10, 1, 1,
    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
);
