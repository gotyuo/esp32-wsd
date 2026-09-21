# RELEASE v7.55 — AI 模型设置/患者评估修复

## 修复：AI 设置不生效 / 患者管理提示 AI 失败

### 1. 手动触发 AI 分析（POST /api/ai/analyses）永远返回"AI 未启用"

**根因**：调用 `ai_client.call_model()` 前，代码先把设置扁平化成了
`_read_settings()` 的结果（key 变成 `enabled/provider/...`），而
`call_model()` 内部会**再次** `_read_settings()` 去查 `ai.enabled`——扁平
dict 里没有这个键，读取为空，于是无论配置如何都误判"AI 未启用"。
这正是 `ai_client.test_connection()` 里注释警告过的经典坑，
修复为直接传原始 `list_settings_raw()`，由 `call_model()` 自己解析。

### 2. 手动触发 AI 分析的落库写入不存在的表

**根因**：插入代码用的是裸 SQL `INSERT INTO ai_analyses(...)`，但系统实际
表名是 `alarm_ai_analyses`（字段结构也不同），导致分析成功后写库即报
`no such table: ai_analyses`。
修复为复用 `db.insert_ai_analysis()`（与 `db.list_ai_analyses()` 同表），
前端"AI 分析历史"能正常查到记录。

## 验证

- 患者管理「AI 评估」路径（GET /api/patients/{pid}/assessment?ai=true）：
  配置正确时正常返回 AI 解读，配置错误时返回中文可读错误
  （连接拒绝/鉴权/模型404/超时等，均经 friendly_error 翻译）
- `POST /api/ai/analyses`：正常返回内容并落库
- `POST /api/ai/test`：正常返回连通性测试结果
- 历史查询 `GET /api/ai/analyses`：能读回本次分析记录
- 回归：test_integration 6 个、test_ai_client 13 个全部通过

## 备注

若患者管理仍提示 AI 失败，请查看具体错误文案区分原因：
- "AI 服务响应超时" → 网络/模型慢，调大 ai.timeout
- "API Key 无效或无权访问" → 检查 ai.api_key
- "模型不存在或无访问权限" → 检查 ai.model 与 provider 是否匹配
- "无法连接 AI 服务（连接被拒绝）" → 检查 base_url 与 AI 服务是否可达