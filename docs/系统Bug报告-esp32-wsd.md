# ICU 重症监护环境监测系统（esp32-wsd）Bug 测试报告

| 项目 | 内容 |
|------|------|
| 被测系统 | ICU EnvMon 重症监护环境监测系统（`gitee.com/hotyuo/esp32-wsd`） |
| 被测版本 | commit `431d895`（fix(test): 修复4个系统Bug，v7.45 之后） |
| 测试类型 | 功能测试 + 接口测试 + 前端 UI 测试 + 安全测试 + 代码审查 |
| 测试环境 | Python 3.12.5 + FastAPI 0.110.3 + SQLite（本机启动，端口 12091）；Chromium 1243 headless |
| 测试数据 | 全新数据库，admin/admin123 引导登录；测试设备 TESTDEV001、患者 P001 |
| 报告日期 | 2026-09-15 |
| 报告作者 | 测试工程师（自动化 + 人工审查） |

---

## 一、测试结论

**系统总体可用，核心业务主流程（登录鉴权、设备/患者管理、体征上报、医嘱/检验/出入量、报警、AI 评估、备份）基本跑通，基础安全机制（会话鉴权、角色权限、登录限流、XSS 转义、报警去重）有效。**

但发现 **1 个 P0 级系统瘫痪缺陷**（已复现 2 次、有完整日志证据）、**4 个安全泄露/数据可信度缺陷**、**3 个并发正确性缺陷**，以及若干功能与性能问题。

| 等级 | 数量 | 说明 |
|------|------|------|
| P0 严重 | 1 | 触发后全系统 ICU 业务持续 500，直至进程重启 |
| P1 高 | 7 | 医疗数据越权泄露、伪造数据、并发竞态 |
| P2 中 | 10 | 时间格式、数据源标记、性能劣化、错误语义 |
| P3 建议 | 2 | favicon 404、观察项 |

---

## 二、Bug 清单总览

| 编号 | 等级 | 模块 | 标题 | 验证方式 |
|------|------|------|------|----------|
| BUG-001 | P0 | 后端/icu | 监护页轮询关闭共享数据库连接，触发全系统持续 500 | 已复现 ×2 + 日志 |
| BUG-002 | P1 | 后端/安全 | `/api/dashboard` 无鉴权，患者档案（PHI）越权泄露 | 已复现 |
| BUG-003 | P1 | 后端/安全 | `/api/patients/{pid}/vitals` 无鉴权，患者体征越权泄露 | 已复现 |
| BUG-004 | P1 | 后端/安全 | `/api/tts/status` 无鉴权，内部服务配置泄露 | 已复现 |
| BUG-005 | P1 | 后端/安全 | 设备 HTTP 上报端点无身份认证，可伪造患者体征数据 | 已复现 |
| BUG-006 | P1 | 后端/db | OTA 上传绕过全局锁，事务拆散 + 竞态可致 is_latest 全部丢失 | 代码确认 |
| BUG-007 | P1 | 后端/db | `_locked_scope` 异常路径锁泄漏 → 全站数据库死锁 | 代码确认 |
| BUG-008 | P1 | 后端/db | `query_locked` 系列未持锁操作共享连接，并发未定义行为 | 代码确认 |
| BUG-009 | P2 | 后端/db | `first_seen` 使用 `DATETIME('now')`，时间格式不一致且相差 8 小时 | 已复现 |
| BUG-010 | P2 | 后端/main | `/api/vitals` 数据源硬编码 `esp8266`，ESP32 上报溯源错误 | 已复现 |
| BUG-011 | P2 | 后端/main | `or` 链取值吞掉合法 `0` 值（温度 0°C、心率 0） | 代码确认 |
| BUG-012 | P2 | 后端/main | `ip_addr` 赋值运算符优先级错误，反向代理下 IP 丢失 | 代码确认 |
| BUG-013 | P2 | 后端/db | `message_clear` 返回 lastrowid 而非删除行数 | 代码确认 |
| BUG-014 | P2 | 后端/tts | TTS 合成全程无超时，Piper 假死时请求永久悬挂 | 代码确认 |
| BUG-015 | P2 | 后端/tts | `IncompleteReadError` 未归一化，服务不可用误报 500 | 代码确认 |
| BUG-016 | P2 | 后端/db | `delete_user` 检查与删除非原子（TOCTOU），并发可删光管理员 | 代码确认 |
| BUG-017 | P2 | 后端/agg | 聚合器每 ~15s 全量轮询 + 清理全表扫描（缺单列索引） | 代码确认 |
| BUG-018 | P2 | 后端/db | `localnow()` 硬编码 UTC+8，与注释承诺矛盾 | 代码确认 |
| BUG-019 | P3 | 前端 | favicon.ico 404 | 已复现 |
| BUG-020 | P3 | 观察 | 服务器异常态下首屏偶发 `SyntaxError`（未稳定复现） | 观察项 |

---

## 三、Bug 详情

### BUG-001【P0】监护页轮询关闭共享数据库连接，触发全系统 ICU 业务持续 500

- **位置**：
  - `server/app/main.py:3521`（`latest_signs()` 末尾 `conn.close()`）
  - `server/app/main.py:3584-3594`（`get_vitals()` 回退2 分支 `_conn.close()`）
  - `server/app/icu.py:28-49`（`_get_conn()` 线程局部连接 + `threading.local` 缓存）
- **复现步骤**（100% 复现）：
  1. 启动服务，创建患者 P001 并绑定设备；
  2. `GET /api/patients/P001/latest-signs` → 200（此请求在 finally 中关闭了该工作线程的线程局部连接）；
  3. 紧接着任意 ICU 接口（`GET /api/patients/P001/vitals`、`POST .../orders`、`POST .../io`、`GET .../assessment` 等）→ **500**，且此后所有落回该线程池线程的 ICU 请求持续 500，直至进程重启。
- **服务器日志证据**：
  ```
  GET /api/patients/PXSS1/latest-signs → 200
  GET /api/patients/PXSS1/vitals?hours=24 → 500
  sqlite3.ProgrammingError: Cannot operate on a closed database. (icu.py:49 c.commit())
  ```
- **根因**：`icu._get_conn()` 返回的连接是 **线程池工作线程的线程局部缓存连接**（`threading.local`）。uvicorn 线程池线程长期复用，`latest_signs` 与 `get_vitals` 回退分支把这份"公共"连接 `close()` 后，连接对象仍缓存在 `local.conn`（`is not None` 判定通过），后续请求拿到 **已关闭连接** → `ProgrammingError`。
- **业务影响**：前端实时监护页按设计轮询 `latest-signs` 与 `vitals`（README v7.30/v7.31 修复记录），**正常运行一段时间后必然触发**；触发后患者查询、医嘱、出入量、AI 评估、监护会话等全部瘫痪，属于医疗监护场景的可用性致命缺陷。
- **修复建议**：
  1. 删除 `main.py:3521` 与 `main.py:3594` 的 `conn.close()`（连接由线程局部管理器持有，不应关闭）；
  2. `icu._get_conn()` 中增加健康检查：`if local.conn is None or self._is_closed(local.conn): 重建`；
  3. 更彻底的方案：`icu.py` 改为每次请求短连接或连接池（如 `sqlite3.connect(..., uri=...)` + `contextmanager` 内 `with closing(...)`）。

---

### BUG-002【P1】`/api/dashboard` 无鉴权，患者档案越权泄露

- **位置**：`server/app/main.py:2149`（`@app.get("/api/dashboard")` 无 `Depends(require_user)`）
- **复现**：`curl http://127.0.0.1:12091/api/dashboard`（无任何 token）→ **200**，返回全部患者列表（pid、姓名、性别、床号、诊断、入院时间等 PHI 敏感数据）。
- **实测响应**：
  ```json
  {"patients":[{"id":2,"pid":"PXSS1","name":"...","gender":"M",...,"diagnosis":"..."}],...}
  ```
- **影响**：医疗数据（个人健康信息）未认证可读，违反最小权限原则；内网部署也需防横向越权。
- **修复建议**：补 `dependencies=[Depends(require_user)]`。

### BUG-003【P1】`/api/patients/{pid}/vitals` 无鉴权，患者体征越权泄露

- **位置**：`server/app/main.py:3559`
- **复现**：`curl http://127.0.0.1:12091/api/patients/P001/vitals` → **200**，返回患者全部体征时间序列（血氧、心率、体温等）。
- **修复建议**：同上补鉴权；如设备固件需要读取，应使用设备级凭据而非裸奔。

### BUG-004【P1】`/api/tts/status` 无鉴权，内部服务配置泄露

- **位置**：`server/app/main.py:3085`
- **复现**：`curl http://127.0.0.1:12091/api/tts/status` → **200**：
  ```json
  {"enabled":true,"host":"piper","port":10200,"voice":"zh_CN-huayan-medium"}
  ```
- **影响**：泄露内网服务拓扑（主机名/端口），为下一步攻击提供侦察信息。
- **修复建议**：补 `require_user`。

### BUG-005【P1】设备 HTTP 上报端点无身份认证，可伪造患者体征数据

- **位置**：`server/app/main.py:2483`（`POST /api/telemetry`）、`main.py:2559`（`POST /api/vitals`）
- **复现**：任何人可 `POST /api/vitals {"device_id":"TESTDEV001","sp_o2":60,"pr_hr":30}` → 200，数据直接入库、触发报警并推送到监护界面；`device_id` 可任意伪造。
- **影响**：ICU 场景下体征数据被伪造可能误导临床判断；同时可无限伪造遥测灌库。MQTT 通道有用户名/密码（envmon/envmon-secret），REST 通道完全裸奔，双通道防护不对称。
- **修复建议**：至少复用 MQTT 凭据做 HTTP Basic/HMAC 校验，或按设备下发 token；并对单设备上报频率限流。

### BUG-006【P1】OTA 上传绕过全局锁，事务拆散 + 竞态

- **位置**：`server/app/db.py:962-977`
- **问题**：
  1. `execute("UPDATE ota_images SET is_latest=0")`（持锁 + 独立提交）与后续 `get_conn().execute(INSERT ...)` + `get_conn().commit()`（**未持锁**）之间事务边界失控；MQTT/聚合线程同时持锁写同一连接时，语句交错、他线程 commit 会把本线程半成品事务一并提交；
  2. 两步非原子：若 INSERT 失败或进程中断，**全部固件版本 `is_latest` 已被清 0**，OTA 从此无"最新版本"。
- **修复建议**：整个函数包进 `with _lock:`，UPDATE + INSERT 同一事务完成后一次 commit。

### BUG-007【P1】`_locked_scope` 异常路径锁泄漏 → 全站死锁

- **位置**：`server/app/db.py:24-33`
- **问题**：`finally` 中先 `get_conn().commit()` 再 `_lock.release()`；若 yield 体内或 commit 抛异常且发生在连接首次建立失败时，`release()` 被跳过。`threading.Lock` 不可重入 → 此后**所有数据库操作永久阻塞**。
- **修复建议**：`release()` 放入独立 try/finally；异常时先 `rollback()` 再 release。

### BUG-008【P1】`query_locked` 系列未持锁操作共享连接

- **位置**：`server/app/db.py:423-433`；调用点 `main.py:2832、3297、3805、3807、3878、3880`
- **问题**：`query_locked/query_one_locked/query_locked_bool` 直接 `get_conn().execute(...)` 不加锁，契约要求"调用方已持锁"，但 main.py 的同步路由（线程池多线程并发）并未持锁调用。与 MQTT/aggregator 线程的写事务并发交错，读事务可能被他线程 `commit()` 拦腰截断（`check_same_thread=False` 只解除线程检查，不提供串行化）。
- **修复建议**：函数内部改为 `with _lock:`；或更名 `_unsafe` 并修复全部调用点。

### BUG-009【P2】`first_seen` 时间格式不一致且相差 8 小时

- **位置**：`server/app/db.py:665-669`（`register_device` INSERT 分支）
- **复现**：手动注册设备后 `GET /api/devices`，新设备 `first_seen` 为 `2026-09-15 16:27:37`（空格分隔、无 Z、UTC 值），而其余时间戳均为 `2026-09-15T16:27:37Z` 格式。
- **影响**：① 同列两种格式，字符串排序错序；② 前端 `new Date("2026-09-15 16:27:37")` 按本地时区（东八区）解析 UTC 值，**显示时间快 8 小时**。
- **修复建议**：改用参数绑定的 `utcnow()`。

### BUG-010【P2】`/api/vitals` 数据源硬编码 `esp8266`

- **位置**：`server/app/main.py:2657、2673`
- **复现**：任何设备（包括 ESP32-S3）经 `/api/vitals` 上报，`vitals.source` 与 WS 推送 `source` 一律为 `esp8266`。
- **影响**：体征数据来源标记错误，医疗数据溯源失真。
- **修复建议**：按 `data.get("fw")` 或请求 UA/平台字段判定，或增加可选 `source` 字段。

### BUG-011【P2】`or` 链取值吞掉合法 `0` 值

- **位置**：`server/app/main.py:2516-2518`（telemetry 的 t/h/p）、`main.py:2597`（hr）
- **问题**：`data.get("t") or data.get("temp_c") or ...` 中，`0.0` 为 falsy：温度 0.0°C 会被忽略并尝试取下一个键；心率 `pr_hr=0` 同理。
- **修复建议**：改用显式判空：`v = data.get("t"); if v is None: v = data.get("temp_c")`。

### BUG-012【P2】`ip_addr` 赋值运算符优先级错误

- **位置**：`server/app/main.py:2509`
  ```python
  ip_addr = data.get("ip") or data.get("ip_addr") or request.client.host if request.client else None
  ```
- **问题**：实际解析为 `(... or request.client.host) if request.client else None`。当 `request.client` 为 None（单元测试/某些代理场景）时，即使载荷携带 `ip` 也会存 None。
- **修复建议**：加括号明确优先级。

### BUG-013【P2】`message_clear` 返回 lastrowid 而非删除行数

- **位置**：`server/app/db.py:477-482`（`execute()` 返回 `cur.lastrowid`）、`db.py:1095-1098`
- **问题**：Python 3.12 中 DELETE 后 `lastrowid` 保留的是该连接上一次 INSERT 的 rowid，并非删除行数。`DELETE /api/messages` 返回的 `cleared` 字段语义错误——当"最后插入消息 id ≠ 实际删除条数"时（如删除过消息后再清空），返回值必然错误；前端"已清除 N 条"提示不可信。同理 `db.execute` 包装 UPDATE/DELETE 的布尔判断（`bool(lastrowid)`）依赖旧行为，属脆弱实现。
- **修复建议**：`execute()` 返回 `cur.rowcount`；插入类单独提供返回 lastrowid 的方法。

### BUG-014【P2】TTS 合成全程无超时

- **位置**：`server/app/tts.py:122-195`
- **问题**：`asyncio.open_connection` 与 Wyoming 消息读取均无 `asyncio.wait_for` 包裹。Piper 容器假死/网络黑洞时，`/api/tts/speak` 请求永久悬挂，设备拉取报警语音反复堆积连接。
- **修复建议**：连接 5s、单条消息读取 10s 超时。

### BUG-015【P2】`IncompleteReadError` 未归一化，误报 500

- **位置**：`server/app/tts.py:83、92`；`main.py:3126-3129`
- **问题**：连接半路关闭时 `readexactly` 抛 `asyncio.IncompleteReadError`（继承 `EOFError`），不落入 `ConnectionError` 分支，被兜底 `Exception` 捕获返回 500"合成失败"，而非 503"服务不可用"，干扰监控与重试语义。
- **修复建议**：在 `_wyoming_synthesize` 中捕获并归一化为 `ConnectionError`。

### BUG-016【P2】`delete_user` 检查与删除非原子（TOCTOU）

- **位置**：`server/app/db.py:891-899`
- **问题**："最后一个管理员"检查与 DELETE 分处不同临界区，两个 admin 并发互删可把管理员全部删光；users 与 sessions 删除非同事务，中途失败留孤儿会话。
- **修复建议**：单条原子 SQL（子查询计数 + rowcount 判断），两删除同事务。

### BUG-017【P2】聚合器 15s 全量轮询 + 清理全表扫描

- **位置**：`server/app/aggregator.py:42-53`（`min(wait, 15)`）、`aggregator.py:99-110`（`_cleanup`）
- **问题**：每 ~15 秒执行一轮完整任务（设计为每分钟一次），同一分钟重复聚合靠 `INSERT OR IGNORE` 幂等兜底但白耗 CPU；`_cleanup` 的 `DELETE ... WHERE ts < ?` 依赖 `(device_id, ts)` 复合索引，单列 ts 过滤无法走索引，每轮全表扫描，7 天原始数据下 IO/WAL 放大约 4 倍/分钟。
- **修复建议**：按 minute_key 去重触发；清理节流至每 60s；为 `telemetry(ts)`、`alarms(ts)` 建单列索引。

### BUG-018【P2】`localnow()` 硬编码 UTC+8

- **位置**：`server/app/db.py:41-43`
- **问题**：docstring 声称"系统 TZ 若已设置则以其为准"，实现写死 `timezone(timedelta(hours=8))`。非东八区部署时 UI 展示时间与实际不符。
- **修复建议**：`ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))` 或修正注释。

### BUG-019【P3】favicon.ico 404

- 每次页面加载浏览器控制台报 `GET /favicon.ico 404`。建议补充图标文件或路由。

### BUG-020【P3·观察项】服务器异常态首屏偶发 `SyntaxError`

- 在服务器处于 BUG-001 瘫痪状态期间首次进行 UI 测试时，页面报 `Uncaught SyntaxError: Unexpected token '{'`；干净重启后 3 个 script 块均通过 `node --check` 与浏览器内 `new Function` 校验，未再复现。建议排查静态资源服务在异常态下的响应一致性，持续观察。

---

## 四、测试通过项（验证有效的能力）

| 用例 | 结果 |
|------|------|
| admin/admin123 登录、错误密码 401、连续错误触发 429 限流 | ✅ |
| 伪造 token / 过期会话 401 拒绝 | ✅ |
| 观察者角色越权（创建患者/删除患者/存阈值/用户管理）均 403 | ✅ |
| 患者创建/重复 pid 409/非法 pid 422 校验 | ✅ |
| 设备注册/重命名/列表；阈值 min≤max 校验 422 | ✅ |
| REST 体征上报入库、监护会话自动创建、设备自动绑定 | ✅ |
| 体征超阈值触发报警（LV1/LV2）+ 报警去重（连续 3 次同类仅 1 条） | ✅ |
| 医嘱创建/停止、检验、检查录入与查询 | ✅ |
| 出入量录入与 24h 结算（in 500 / out 300 / net +200） | ✅ |
| AI 评估（规则引擎分级输出） | ✅ |
| WebSocket 实时推送（alarm/vital/telemetry 消息） | ✅ |
| 备份创建/列表/下载；恢复接口参数前后端一致（文件上传） | ✅ |
| XSS 存储型攻击被前端 `esc()` 转义（`<script>alert(1)</script>` 原样显示不执行） | ✅ |
| 前端登录/13 个视图切换/实时监护轮询 UI 流程 | ✅ |

---

## 五、修复优先级建议

1. **立即修复**：BUG-001（P0，一行 `close()` 引发全系统瘫痪）；BUG-002/003/004（补鉴权，各一行）。
2. **本迭代修复**：BUG-005（设备上报认证）、BUG-006/007/008（数据库并发三连）。
3. **计划修复**：BUG-009 ~ BUG-018（数据质量、性能与语义）。
4. **低优先级**：BUG-019、BUG-020。

---

## 附录：测试产物

- API 测试脚本：`bugtest/api_test.py`、`bugtest/api_test2.py`
- UI 测试脚本：`bugtest/ui_test.js`（Playwright）
- 服务器日志：`bugtest/server.log`、`bugtest/server2.log`、`bugtest/server3.log`（含 BUG-001 完整堆栈）
- UI 截图：`bugtest/shots/`（登录、主界面、各视图、XSS 验证）
- 测试数据库：`bugtest/test_envmon.db`
