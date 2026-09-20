# Release v7.53 — 修复「AI 解读暂不可用：Read timed out」& 统一 AI 调用链路

> **发布日期**: 2026-09-20
> **项目**: 重症监护环境监测系统 (ICU EnvMon)
> **架构**: ESP32-S3 / ESP8266 边缘节点 + Docker 后端 + Web 监护台
> **基于**: commit `fed77f3`（v7.52）

---

## ✨ 变更汇总

实时监护界面「AI 评估」反复报 `AI 解读调用失败：HTTPSConnectionPool(host='api.siliconflow.cn', port=443): Read timed out. (read timeout=25)`，
且「AI 模型已配置好但实时界面仍报错」。以测试工程师视角排查，定位 3 个根因 + 若干设计问题，统一修复：

### 1. 实时评估路径硬编码 25s 读超时、无重试、无视 ai.timeout（核心根因）
- **根因**：`icu.assess_with_ai()` 绕过统一的 `ai_client`，另用 `requests.post(timeout=(3,25))`。
  SiliconFlow 等模型生成稍慢（>25s）就整体失败；失败后把底层异常原样拼给前端，且每次点
  「AI 评估」都全量调一次 LLM，超时概率被反复点击放大。
- **修复**：
  - 实时评估统一走 `ai_client.call_model()`——**超时完全由设置里的 `ai.timeout` 驱动**（5~300s，默认 30，可调）；
  - `call_model` 新增**自动重试**：瞬时故障（读超时 / 连接重置 / 5xx / 429 限流）自动补跑一次（可配 retries），
    配置类错误（401/403/404）**不重试**，避免把配错掩盖成慢请求；
  - 成功摘要 **60s 短缓存**（按患者），规则评估部分始终最新，只复用 AI 文本，减少重复调用与超时概率。

### 2. 两套并行 AI 调用代码，配置口径不一（「配好了还报错」的元凶）
- **根因**：`icu.py` 读 `ai.prompt` + deepseek 默认值，`ai_client.py` 读 `ai.system_prompt` + provider 默认值，
  同一功能两套逻辑，设置与实时界面行为可能不一致。
- **修复**：`assess_with_ai` 删除自带的 requests 实现，改为唯一入口 `ai_client`；
  `_read_settings` 兼容 `ai.system_prompt` / `ai.prompt` 双键（旧版只写 `ai.prompt` 的配置自动生效）。

### 3. `/api/ai/analyses`（AI 分析按钮）必然失败
- **根因**：`main.py` 把 `ai_client._read_settings(icu)` 传成了**模块对象**（非配置源），
  调用即抛 `AttributeError: module 'icu' has no attribute 'get'`，被外层 except 吞掉后报「AI 分析失败」。
- **修复**：改为 `ai_client._read_settings(icu.list_settings_raw())`。

### 4. 原始异常直接暴露给用户（P1）
- **修复**：新增 `ai_client.friendly_error()`，把超时 / 连接拒绝 / 连接重置 / DNS /
  Key 无效 / 模型 404 / 限流 / 5xx 统一翻译成中文提示；`/api/ai/test`、实时评估、AI 分析全部复用。
  用户看到的将是：「AI 服务响应超时（网络不稳定或模型生成较慢），已自动重试；仍失败可调大 ai.timeout 后重试」。

### 5. 前端无重试入口（P2）
- **修复**：AI 解读失败横幅上增加「重试」按钮；按钮文案区分「AI 评估中… / 评估中…」。

## 🔧 改动文件

| 文件 | 说明 |
|------|------|
| `server/app/ai_client.py` | call_model 重试机制、超时/位数钳制、friendly_error、prompt 双键兼容、空 api_key 早退 |
| `server/app/icu.py` | assess_with_ai 统一走 ai_client；成功摘要 60s 缓存；错误中文化 |
| `server/app/main.py` | 修复 `_read_settings(icu)` 模块对象 bug；/api/ai/test 复用 friendly_error；报警后台线程尊重 ai.timeout + 重试一次 |
| `server/app/static/index.html` | AI 解读失败增加「重试」按钮；评估按钮文案细分 |
| `server/tests/test_ai_client.py` | 新增单元测试 13 例（重试/不重试/超时/错误映射/配置兼容） |
| `.rollback/20260920-ai-timeout-retry/` | 修复前快照（保留版本，可回滚） |

## 📋 升级指引

1. 重启 backend 容器（`docker compose restart backend` 或 `docker compose up -d --build`）。
2. 若使用 SiliconFlow / 生成较慢的模型，建议在设置页把 `ai.timeout` 调到 **60**；
   不再需要为实时评估单独配置任何东西——`ai.provider / ai.base_url / ai.model / ai.api_key` 即全链路生效。
3. 点「AI 评估」仍失败时，错误提示已是中文可读信息：超时可重试/调大 timeout；
   提示 Key 无效或模型 404 时检查设置页配置。
4. 回滚：将 `.rollback/20260920-ai-timeout-retry/` 下 4 个文件覆盖回原路径即可，或 `git checkout fed77f3 -- <file>`。