# 系统测试审查报告 —— 专业测试员视角

> 角色：IC 测试工程师
> 方法：白盒 + 黑盒混合，覆盖 API 契约 / 安全 / 数据一致性 / 边界 / 文档对齐
> 时间：2026-08-18（v2.2, commit 0be632c）
> 覆盖：42 endpoints / 15 张表 / 前端 5 视图 / 固件 alarm 阈值

---

## 一、发现列表（按严重程度）

### 🔴 Critical（生产必须修）

**C1. `POST /api/backup` 未鉴权 —— 任意匿名可触发备份 + 泄露 DB 路径**
- 实测：`curl http://127.0.0.1:12090/api/backup` 返回 `200 OK`，`{"path":..., "size":..., "sha256":...}`
- 风险：攻击者通过备份路径枚举可确认 DB 位置，配合其他漏洞可放大
- 定位：`main.py:827` 缺失 `dependencies=[Depends(require_admin)]`
- 修复：加 `require_admin` 依赖

**C2. `GET /api/backup` 同样未鉴权**
- 实测：未带 token 直接 GET 返回备份文件列表
- 风险：泄露备份命名、大小、时间戳 → 推断数据量级

**C3. 默认密码 `admin / admin123` 写死，生产环境无强制改密机制**
- 容器启动即创建该账号，无 `FIRST_BOOT_CHANGE_PASSWORD` 标记
- `sessions` 表使用 MD5 哈希（`sessions.token`）—— 弱哈希，可被彩虹表碰撞
- 修复建议：bcrypt 哈希 session token；首次启动强制改密或注入环境变量覆写

**C4. 无速率限制 / 无 CSRF —— 登录爆破零成本**
- `/api/login` 无 throttling，`POST` 无 CSRF token（虽用 token 认证，但登录入口裸露）
- 修复建议：登录接口限流（例如 5 次/min/IP）

---

### 🟠 High

**H1. `.env.example` 与 `.env` 里 `WEB_PORT=8627`（旧端口）**
- 实测：`grep WEB_PORT .env*` → `8627`
- 但 `docker-compose.yml` 已经改为 `${WEB_PORT:-12090}:12090`
- 影响：用户按 `.env` 部署会误以为端口 8627
- 修复：`.env.example` 和 `.env` 同步改为 `WEB_PORT=12090`

**H2. `GET /api/ota/image` 返回 404 而非 401/403（信息泄露）**
- 实测：未鉴权访问返回 `404`
- 这符合"不暴露固件存在"的安全策略（✅ 优点）
- **但** `POST /api/ota/upload` 未鉴权直接 `{"detail":"未登录"}`，端点存在性暴露
- 一致性建议：上传也应返回 404 伪装

**H3. AI `assessment` 参数 `hours=0` 或负数 → 崩溃风险**
- 代码：`icu.py:324 cutoff = now - timedelta(hours=max(hours, 1))` 有防护（✅）
- 但 `io_balance` 里除 hours 做除数：`urine_hr_ml = out_ml / hours` → 若 hours=0 触发 ZeroDivision
- 实测 `max(hours, 1)` 兜底 → 未触发，但代码审查视角仍有隐忧
- 建议：API 层显式 `Query(ge=1)` 约束

**H4. `POST /api/patients` 无 Pydantic 模型 —— 任意字段透传**
- 代码：`main.py:675 body: Dict[str, Any]`
- 影响：可传 `admin_token`、`role` 等未白名单字段（当前 DB INSERT 忽略，无实际注入风险，但契约不严）
- 建议：使用 `PatientCreate` Pydantic 模型

**H5. `unique_id` 字段声明为"上游系统 ID 去重"但无 DB 唯一约束**
- `io_log.unique_id`, `lab_results.item_code` 均无 UNIQUE 索引
- 实测：同一 `unique_id` 重复 POST 会插入多条
- 修复：`UNIQUE(patient_id, unique_id)` 索引

---

### 🟡 Medium

**M1. 危急值自动计算存在边界歧义**
- 实测：pH 7.28（ref 7.35–7.45）→ `critical=1` ✅
- 但极端情况：pH 6.0（远超正常）→ 同样走 same formula → 也 `critical=1`（正确）
- 但 pH 7.40（正常值）→ `critical=0`（正确）
- **未发现的 bug**：如果 `ref_max < ref_min`（用户误填）→ 计算异常
- 建议：API 层校验 `ref_min <= ref_max`

**M2. `GET /api/assessment` 依赖 8 张表查询，24h 无 vitals 时返回 `low` —— 无法区分"无数据"与"确实正常"**
- 修复建议：`assess_patient` 在 `vitals_count==0` 时返回 `overall_risk="unknown"` + `actions` 提醒录入

**M3. WebSocket `/ws?token=...` 使用 URL 参数 —— 会写入代理日志**
- 实测前端代码使用 URL 参数传 token
- 建议：改为 query `?t=...` 缩短或改用 header（部分浏览器限制）
- 或：登录后通过 cookie 携带（需 `ws` 支持）

**M4. `envmon-v1.0/` 目录未清理，v2.2 无发布包**
- 当前无 `envmon-v2.2/` 目录，`scripts/` 目录内容需检查是否与 v2.2 匹配

**M5. 文档中 `<服务器IP>:12090` 与 `<服务器IP>:8627` 并存**
- `02-服务器部署.md` 提到 8627（标注"已废弃"） —— ✅ 有说明
- 但 `README.md` 未明确说明
- 建议 README 增加"端口历史"章节

---

### 🔵 Low

**L1. `envmon.db-shm` 和 `envmon.db-wal` 在 `data/` 下暴露给外部卷挂载**
- SQLite WAL 模式正常行为，但需确保 `data/` 卷不在 web 静态目录

**L2. API 文档中 `GET /api/backup` 描述为管理员权限，实际未鉴权**
- `06-API-接口文档.md` 需要更新（当前文档描述与实现不一致）

**L3. `07-临床-数据字典.md` 中 `io_log` 净平衡计算规则与代码 `amount_ml + amount_g` 一致**
- ✅ 对齐

**L4. 固件 alarm 阈值（`alarm.cpp`）与服务器 AI 阈值（`icu.py`）存在不一致**

| 参数 | 固件 alarm.cpp | 服务器 icu.py AI | 一致性 |
|------|---------------|------------------|--------|
| SpO₂ LV2 阈值 | 未分 LV，band(95,100) → <95 即报警 | LV1<95, LV2<90 | ⚠️ 固件更严 |
| HR LV2 阈值 | band(60,100) → <55 或 >105 报警 | LV2<50 或 >130 | ⚠️ 服务器更宽 |
| RR LV2 阈值 | band(12,25) → <7 或 >30 | LV2<8 或 >35 | ⚠️ 不一致 |
| Glucose LV2 | band(3.9,6.1) → <2.8 或 >7.2 | LV2<3.0 或 >16 | ⚠️ 严重不一致 |

- 影响：固件端可能已报警但服务器显示 LV0，患者看不到告警
- 建议：统一阈值表或明确分场景（终端 vs 临床）

**L5. `GET /api/history` 返回 `points` 数组但未文档化格式**
- API 文档未写

---

## 二、测试覆盖统计

| 层级 | 已覆盖 | 未覆盖 | 建议 |
|------|--------|--------|------|
| 端点黑盒 | 42/42（含 422/404/401 边界） | — | — |
| 鉴权矩阵 | 50%（admin vs viewer 部分未测） | viewer 角色端到端 | 加 CI |
| 数据一致性 | 部分（vitals 时间序、IO 净平衡、危急值） | HIS/LIS 真实对接 | 需 mock 上游 |
| 性能 | 未测 | 2000 患者并发、5000 体征插入 | 加 benchmark |
| 固件 alarm 触发 | 未测（无真实设备接入） | ECG/PPG 信号注入 | 加测试夹具 |
| OTA 端到端 | 未回归（端口变更后） | 服务器 → 设备全链路 | 端口变更后需重跑 |
| UI 前端 | 41/41 单元级（元素存在 + API 响应） | 浏览器交互 | 建议 Playwright |

---

## 三、修复优先级建议

1. **立即**：C1/C2 备份端点鉴权、C3 改密机制
2. **本周**：H1 `.env` 端口同步、H4 `PatientCreate` 模型、H5 unique_id 索引
3. **本月**：M2 `unknown` 状态、L4 阈值对齐
4. **长期**：CSRF / rate limiting / 性能基准