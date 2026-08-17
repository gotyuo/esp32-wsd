-- ============================================================
-- 物联网环境监测系统 - SQLite 数据库设计
-- 初始化: sqlite3 envmon.db < schema.sql
-- ============================================================

-- 设备表
CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,            -- 设备编号, 如 envmon-a1b2c3
    name        TEXT,                        -- 自定义名称
    fw_version  TEXT,                        -- 固件版本
    ip_addr     TEXT,                        -- 最近上报来源 IP
    first_seen  TEXT NOT NULL,               -- 首次上线 (UTC ISO8601)
    last_seen   TEXT,                        -- 最近上报时间
    online      INTEGER NOT NULL DEFAULT 0   -- 1=在线 0=离线
);

-- 原始遥测数据（设备按 report_interval 上报）
CREATE TABLE IF NOT EXISTS telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    ts          TEXT NOT NULL,               -- UTC ISO8601, 如 2026-08-11T03:20:01Z
    temp_c      REAL,                        -- 温度 ℃
    hum_pct     REAL,                        -- 湿度 %RH
    pres_hpa    REAL,                        -- 气压 hPa
    rssi        INTEGER,                     -- WiFi 信号 dBm
    alarm_level INTEGER NOT NULL DEFAULT 0,  -- 0正常 1预警 2报警
    free_heap   INTEGER,                     -- 设备剩余堆栈(诊断用)
    UNIQUE(device_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts ON telemetry(device_id, ts);

-- 每分钟聚合记录（长期保存，历史曲线数据源）
CREATE TABLE IF NOT EXISTS telemetry_1m (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    ts_minute     TEXT NOT NULL,             -- UTC 分钟粒度 2026-08-11T03:20
    temp_avg      REAL,
    temp_min      REAL,
    temp_max      REAL,
    hum_avg       REAL,
    pres_hpa_avg  REAL,
    samples       INTEGER NOT NULL DEFAULT 0,
    alarm_max     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(device_id, ts_minute)
);
CREATE INDEX IF NOT EXISTS idx_telem1m_device_ts ON telemetry_1m(device_id, ts_minute);

-- 阈值配置表（'*' 为全局默认，设备级优先）
CREATE TABLE IF NOT EXISTS thresholds (
    device_id       TEXT PRIMARY KEY,
    temp_min        REAL NOT NULL DEFAULT 5,
    temp_max        REAL NOT NULL DEFAULT 40,
    hum_min         REAL NOT NULL DEFAULT 20,
    hum_max         REAL NOT NULL DEFAULT 90,
    pres_min        REAL NOT NULL DEFAULT 950,
    pres_max        REAL NOT NULL DEFAULT 1050,
    report_interval INTEGER NOT NULL DEFAULT 10,
    alarm_enabled   INTEGER NOT NULL DEFAULT 1,
    alarm_sound     INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT
);

-- 报警事件记录
CREATE TABLE IF NOT EXISTS alarms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  TEXT NOT NULL,
    ts         TEXT NOT NULL,                -- 触发时间
    level      INTEGER NOT NULL,             -- 1预警 2报警
    reason     TEXT,                         -- 可读原因
    temp_c     REAL,
    hum_pct    REAL,
    pres_hpa   REAL,
    cleared_at TEXT                          -- 恢复时间(NULL=仍在报警)
);
CREATE INDEX IF NOT EXISTS idx_alarms_device_ts ON alarms(device_id, ts);

-- ============================================================
-- 用户与登录会话（v2.0 多用户）
-- ============================================================

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,      -- 登录名
    display_name  TEXT,                      -- 显示名
    password_hash TEXT NOT NULL,             -- PBKDF2-HMAC-SHA256
    salt          TEXT NOT NULL,             -- 每个用户独立盐
    role          TEXT NOT NULL DEFAULT 'admin',  -- admin / viewer
    sound_alarm   INTEGER NOT NULL DEFAULT 1,     -- 浏览器报警声音开关(偏好)
    created_at    TEXT NOT NULL,
    last_login    TEXT
);

-- 会话表（登录 token）
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,            -- secrets.token_urlsafe(32)
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    ip_addr     TEXT,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

-- ============================================================
-- 固件 OTA 版本表（v2.0 固件远程升级）
-- 存版本元数据 + SHA256 + bin 数据
-- ============================================================
CREATE TABLE IF NOT EXISTS ota_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    version    TEXT NOT NULL,                 -- 版本号, 如 1.7.0
    size       INTEGER NOT NULL,              -- 字节数
    sha256     TEXT NOT NULL,                 -- SHA256 校验
    uploaded   TEXT NOT NULL,                 -- UTC ISO8601
    is_latest  INTEGER NOT NULL DEFAULT 0,   -- 1=当前最新版本
    binary     BLOB NOT NULL                  -- 固件 bin
);
CREATE INDEX IF NOT EXISTS idx_ota_latest ON ota_images(is_latest);
