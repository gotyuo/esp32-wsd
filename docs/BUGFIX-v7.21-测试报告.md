# 测试工程师 Bug 修复报告 v7.21

> 测试人：自动化测试工程师（Agent）
> 时间：2026-09-14
> 基线：v7.20 (commit 6f9a77b)
> 修复分支：fix/tester-bugs-v2 → 已合并 main
> 提交：4269c35

---

## 一、修复清单

### 🔴 Bug 1 [Critical] — MAX30102 心率计算公式错误

**现象**：MAX30102 血氧传感器的心率(Pr)几乎永远返回 NAN，设备无法上报脉率数据。

**根因**：`computeHR()` 函数中心率公式写反：
```cpp
// 错误代码
float hr = 3000.0f / bestD / bestN;   // = 3000 / (bestD × bestN)

// 正确公式
float hr = 3000.0f * bestN / bestD;   // = 3000 × bestN / bestD
```

**推导**：
- 采样率 50Hz，峰值间距 d 个样本
- 心率 = 60秒/分钟 × 50样本/秒 ÷ d样本 = 3000/d
- 多组间距：avg_d = bestD / bestN
- 心率 = 3000 / (bestD/bestN) = **3000 × bestN / bestD**

**影响范围**：3 个活跃固件变体
- `firmware/src_esp32_4oled/max30102.cpp`
- `firmware/src_esp32_6oled/max30102.cpp`
- `firmware/src_esp8266_4oled/max30102.cpp`

**验证**：以 HR=75bpm 为例
- 50Hz 采样，峰间距 = 40 样本，5 组峰值 → bestD=200, bestN=5
- 旧公式：3000/200/5 = **3 bpm** → 被 30~220 范围过滤 → NAN ❌
- 新公式：3000×5/200 = **75 bpm** ✅

---

### 🔴 Bug 2 [Critical] — 评估 API hours 参数无下限校验

**位置**：`server/app/main.py` — `GET /api/patients/{pid}/assessment`

**现象**：传入 `hours=0` 或 `hours=-5` 不报错，但导致：
- 尿量计算 `io_bal["out_ml"] / hours` → 负数或除零
- 时间窗口 `timedelta(hours=max(hours, 1))` 虽有兜底但返回的 `hours` 字段为原值
- AI 评估摘要显示负数小时

**修复**：
```python
# 旧
def assess(pid: str, hours: int = 24, ...):
# 新
def assess(pid: str, hours: int = Query(24, ge=1, le=720), ...):
```

---

### 🟠 Bug 3 [High] — IO 平衡 API hours 参数无下限校验

**位置**：`server/app/main.py` — `GET /api/patients/{pid}/io/balance`

**现象**：同 Bug 2，`hours` 参数无校验。

**修复**：
```python
def io_balance(pid: str, hours: int = Query(24, ge=1, le=720)):
```

---

### 🟠 Bug 4 [High] — 端口 8627→12090 不一致

**现象**：v7.20 已将 Web 端口从 8627 改为 12090，但多处文件仍残留旧端口。

**修复文件**：
| 文件 | 修改内容 |
|------|---------|
| `scripts/run_local.sh` | 启动端口 8627→12090 |
| `server/README.md` | 访问地址、端口表、环境变量表 |
| `server/docker-compose.yml` | 注释 "Web 对外 8627"→12090 |
| `firmware/src_esp32_4oled/ota_mgr.cpp` | 注释 8627→12090 |
| `firmware/src_esp32_6oled/ota_mgr.cpp` | 注释 8627→12090 |

---

### 🟡 Bug 5 [Medium] — 检验 critical 标记逻辑过激

**位置**：`server/app/icu.py` — `lab_result_insert()`

**现象**：旧逻辑将任何超出参考范围(ref_min~ref_max)的检验值标记为 `critical=1`（危急值）。临床上，轻度偏离正常区间不等于危急值。

**示例**：
- 钾 K=5.5，参考范围 3.5~5.3 → 旧逻辑标 critical ❌（仅轻度偏高）
- 钾 K=6.6，参考范围 3.5~5.3 → 新逻辑标 critical ✅（超出 50% 区间）

**修复**：
```python
# 旧逻辑
if value < ref_min or value > ref_max:
    critical = 1

# 新逻辑：超出参考范围 50% 以上才标危急
span = ref_max - ref_min
if value < ref_min - span * 0.5 or value > ref_max + span * 0.5:
    critical = 1
```

---

## 二、版本控制

| 操作 | 说明 |
|------|------|
| 基线 | main @ 6f9a77b (v7.20) |
| 分支 | `fix/tester-bugs-v2` |
| 提交 | 4269c35 — 5 项 bug 修复 |
| 合并 | `--no-ff` merge to main |
| 推送 | main @ 5982508 → Gitee |
| PR 链接 | https://gitee.com/hotyuo/esp32-wsd/pull/new/hotyuo:fix/tester-bugs-v2...hotyuo:main |

---

## 三、未修复项（需后续跟进）

| # | 严重度 | 描述 | 原因 |
|---|--------|------|------|
| 1 | 🟡 Medium | WebSocket token 通过 URL 参数传递 | 需前端配合改造，影响面大 |
| 2 | 🟡 Medium | 固件 alarm 阈值与服务器 AI 阈值不一致 | 需产品确认统一阈值表 |
| 3 | 🔵 Low | 无速率限制/CSRF 防护 | 需引入中间件，架构改动 |
| 4 | 🔵 Low | io_log/lab_results 无 UNIQUE 约束 | 需 DB 迁移，影响生产数据 |
| 5 | 🔵 Low | 报警无确认/消警流程 | 需新增 API + UI，功能开发 |

---

## 四、测试建议

1. **固件验证**：烧录修复后固件，用指尖放入 MAX30102 传感器，确认 Pr 值在 60~100 范围内正常显示
2. **API 验证**：
   - `curl '.../api/patients/P001/assessment?hours=0'` → 应返回 422
   - `curl '.../api/patients/P001/assessment?hours=-5'` → 应返回 422
   - `curl '.../api/patients/P001/io/balance?hours=0'` → 应返回 422
3. **端口验证**：`./scripts/run_local.sh` 启动后访问 `http://127.0.0.1:12090`
4. **检验验证**：POST lab result with K=5.5, ref 3.5~5.3 → critical=0；K=6.6 → critical=1
