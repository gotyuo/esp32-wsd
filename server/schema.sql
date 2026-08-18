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

-- ============================================================
-- ICU 重症监护数据模型（v2.1）
-- 患者 / 患者-设备关联 / 多源生命体征 / 医嘱 / 检验
-- ============================================================

CREATE TABLE IF NOT EXISTS patients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pid         TEXT NOT NULL UNIQUE,      -- 患者编号 (如 PACU-0001)
    name        TEXT,                      -- 姓名
    gender      TEXT,                      -- M/F
    age         INTEGER,
    bed_no      TEXT,                      -- 床号
    admit_ts    TEXT NOT NULL,             -- 入院/入 ICU 时间
    diagnosis   TEXT,                      -- 诊断
    doctor      TEXT,                      -- 主管医生
    phone       TEXT,                      -- 联系电话
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patients_pid ON patients(pid);

-- 患者-设备关联（多对多）
CREATE TABLE IF NOT EXISTS patient_devices (
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL,
    role        TEXT DEFAULT 'primary',    -- primary / secondary
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (patient_id, device_id)
);

-- 生命体征原始数据（支持 ESP32 + 上游系统多源录入）
-- source: esp32 / his / ecg / ventilator / lab / manual
-- 参数字段用通用列 + value 覆盖，便于曲线图
CREATE TABLE IF NOT EXISTS vitals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    ts              TEXT NOT NULL,         -- ISO8601 UTC
    source          TEXT NOT NULL DEFAULT 'esp32',
    source_device   TEXT,                  -- 设备 id 或仪器编号
    -- 通用指标
    sp_o2           REAL,                  -- 血氧 %
    pr_hr           REAL,                  -- 脉率 bpm
    ecg_hr          REAL,                  -- 心电图心率 bpm
    ecg_st          REAL,                  -- ST 段偏移 mV
    rr_bpm          REAL,                  -- 呼吸频率 rpm
    etco2           REAL,                  -- 呼气末 CO2 mmHg
    sbp             REAL,                  -- 收缩压 mmHg
    dbp             REAL,                  -- 舒张压 mmHg
    map_bp          REAL,                  -- 平均动脉压 mmHg
    ibp             REAL,                  -- 有创血压 mmHg
    temp_c          REAL,                  -- 体温
    glucose         REAL,                  -- 血糖 mmol/L
    -- 环境指标 (ESP32)
    hum_pct         REAL,
    pres_hpa        REAL,
    -- 检验
    k_mmol          REAL,
    na_mmol         REAL,
    cl_mmol         REAL,
    ca_mmol         REAL,
    glucose_lab     REAL,                  -- 血气/检验血糖
    lactate         REAL,                  -- 乳酸 mmol/L
    ph              REAL,                  -- pH
    pco2            REAL,
    po2             REAL,
    hco3            REAL,
    be              REAL,
    -- 报警标记
    alarm_flag      INTEGER NOT NULL DEFAULT 0,  -- 1=预警 2=报警
    alarm_reason    TEXT,
    -- 扩展 JSON
    extra           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vitals_patient_ts ON vitals(patient_id, ts);
CREATE INDEX IF NOT EXISTS idx_vitals_patient_source ON vitals(patient_id, source);

-- 医嘱 / 用药
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    source      TEXT DEFAULT 'his',         -- his / manual / lis
    order_no    TEXT,                       -- 医嘱编号
    drug_name   TEXT,                       -- 药品名
    dosage      TEXT,                       -- 剂量
    route       TEXT,                       -- 给药途径 (iv/im/sc/oral/pump)
    start_ts    TEXT NOT NULL,
    end_ts      TEXT,
    rate_mlph   REAL,                       -- 泵速 mL/h
    status      TEXT DEFAULT 'active',      -- active / stopped / completed
    operator    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_patient_ts ON orders(patient_id, start_ts);

-- LIS 检验结果
CREATE TABLE IF NOT EXISTS lab_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    source      TEXT DEFAULT 'lis',         -- lis / blood_gas / manual
    item_code   TEXT,
    item_name   TEXT,
    value       REAL,
    unit        TEXT,
    ref_min     REAL,
    ref_max     REAL,
    result_ts   TEXT NOT NULL,
    critical    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lab_patient_ts ON lab_results(patient_id, result_ts);

-- ============================================================
-- 出入量记录（Input / Output）
-- ============================================================
CREATE TABLE IF NOT EXISTS io_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    direction   TEXT NOT NULL CHECK(direction IN ('in','out')),
    kind        TEXT NOT NULL,            -- fluid/urine/stool/drain/suction/emesis/other
    sub_type    TEXT DEFAULT NULL,        -- 具体：生理盐水/白蛋白/血/尿/胃液/痰
    amount_ml   REAL DEFAULT 0,           -- mL 体积
    amount_g    REAL DEFAULT 0,           -- g 重量
    route       TEXT DEFAULT NULL,        -- iv/oral/po/drain/np/rectum/urine
    note        TEXT DEFAULT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    operator    TEXT DEFAULT NULL,
    unique_id   TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_io_patient_ts ON io_log(patient_id, ts);

-- 备份日志
CREATE TABLE IF NOT EXISTS backup_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
