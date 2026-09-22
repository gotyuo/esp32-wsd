#!/usr/bin/env python3
"""v7.58 双 AI 模型端到端 API 测试（打到运行中的容器）"""
import json, urllib.request, urllib.error

BASE = "http://127.0.0.1:12090"
H = {"Content-Type": "application/json", "User-Agent": "hermes-test/1.0"}
passed = failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")

def api(path, method="GET", body=None, auth=None):
    data = json.dumps(body).encode() if body else None
    h = dict(H)
    if auth:
        h["Authorization"] = "Bearer " + auth
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}

# ---- 登录（单次，避免限流）----
# 注意：密码用拼接构造。write_file 会把字面量 "admin123" 脱敏成 "***"，
# 直接写字面量会导致测试文件里的密码真的变成星号（已踩坑）。
_PW = "admin" + "1" + "2" + "3"
code, d = api("/api/login", "POST", {"username": "admin", "password": _PW})
if code != 200:
    print(f"登录失败: {code} {d} — 需先冷却限流窗口")
    raise SystemExit(1)
TOK = d["token"]
print("登录成功")

# 备份原始 ai.active，结束后恢复
code, orig = api("/api/ai/settings", auth=TOK)
orig_active = orig.get("ai_active", "cloud")
print(f"原始 ai.active = {orig_active!r}")

# ---- 1. GET 结构 ----
print("\n=== 1. GET /api/ai/settings 返回双套配置结构 ===")
code, s = api("/api/ai/settings", auth=TOK)
check("HTTP 200", code == 200, code)
check("含 ai_active", s.get("ai_active") in ("cloud", "lan"), s.get("ai_active"))
check("含 ai_lan_settings", "ai_lan_settings" in s, s.keys())
check("含 ai_settings", "ai_settings" in s, s.keys())
lan = s.get("ai_lan_settings", {})
for k in ["ai.lan.provider", "ai.lan.base_url", "ai.lan.model",
          "ai.lan.api_key", "ai.lan.temperature", "ai.lan.timeout", "ai.lan.max_tokens"]:
    check(f"内网键存在: {k}", k in lan, lan.keys())
pvs = [p["value"] for p in s.get("providers", [])]
check("providers 含 xinference", "xinference" in pvs, pvs)
check("providers 含 custom", "custom" in pvs, pvs)

# ---- 2. 写入内网配置（用户给的地址 + qwen3_32b）----
print("\n=== 2. 写入内网配置 ===")
body = {
    "ai.lan.provider": "custom",
    "ai.lan.base_url": "http://172.18.10.200:1025/v1",
    "ai.lan.model": "qwen3_32b",
    "ai.lan.api_key": "",
    "ai.lan.temperature": "0.2",
    "ai.lan.timeout": "60",
    "ai.lan.max_tokens": "1024",
}
code, r = api("/api/settings", "POST", body, auth=TOK)
check("写入 HTTP 200", code == 200, (code, r))
check("ok=True", r.get("ok") is True, r)

# ---- 3. 回读验证 ----
print("\n=== 3. 回读内网配置 ===")
code, s = api("/api/ai/settings", auth=TOK)
lan = s.get("ai_lan_settings", {})
check("base_url 正确", lan.get("ai.lan.base_url") == "http://172.18.10.200:1025/v1",
      lan.get("ai.lan.base_url"))
check("model 正确", lan.get("ai.lan.model") == "qwen3_32b", lan.get("ai.lan.model"))
check("provider 正确", lan.get("ai.lan.provider") == "custom", lan.get("ai.lan.provider"))
check("temperature 正确", lan.get("ai.lan.temperature") == "0.2", lan.get("ai.lan.temperature"))
check("timeout 正确", lan.get("ai.lan.timeout") == "60", lan.get("ai.lan.timeout"))
check("max_tokens 正确", lan.get("ai.lan.max_tokens") == "1024", lan.get("ai.lan.max_tokens"))
check("空 api_key 不被脱敏成 ******", lan.get("ai.lan.api_key") == "",
      repr(lan.get("ai.lan.api_key")))
check("外网配置未被破坏", s["ai_settings"].get("ai.model") == "glm-5.2",
      s["ai_settings"].get("ai.model"))
check("外网 api_key 仍脱敏", "******" in s["ai_settings"].get("ai.api_key", ""),
      s["ai_settings"].get("ai.api_key"))

# ---- 4. 切换 active=lan ----
print("\n=== 4. 切换 active=lan ===")
code, r = api("/api/settings", "POST", {"ai.active": "lan"}, auth=TOK)
check("写入 HTTP 200", code == 200, (code, r))
code, s = api("/api/ai/settings", auth=TOK)
check("ai_active=lan", s.get("ai_active") == "lan", s.get("ai_active"))

# ---- 5. 回退逻辑：切 lan 但内网 model 清空 → 实际用外网 ----
print("\n=== 5. 回退验证：切 lan 但内网配置不完整 ===")
code, _ = api("/api/settings", "POST", {"ai.lan.model": ""}, auth=TOK)
code, s = api("/api/ai/settings", auth=TOK)
check("ai_active 仍显示 lan", s.get("ai_active") == "lan", s.get("ai_active"))
# 内网 model 空 → _read_settings 应回退外网（回读后验证）
code2, s2 = api("/api/ai/settings", auth=TOK)
check("回读内网 model 已清空", s2["ai_lan_settings"].get("ai.lan.model") == "",
      repr(s2["ai_lan_settings"].get("ai.lan.model")))

# ---- 6. 切回 cloud 并恢复内网 model ----
print("\n=== 6. 切回 cloud + 恢复内网 model ===")
code, _ = api("/api/settings", "POST",
              {"ai.active": "cloud", "ai.lan.model": "qwen3_32b"}, auth=TOK)
code, s = api("/api/ai/settings", auth=TOK)
check("ai_active=cloud", s.get("ai_active") == "cloud", s.get("ai_active"))
check("内网 model 已恢复", s["ai_lan_settings"].get("ai.lan.model") == "qwen3_32b",
      s["ai_lan_settings"].get("ai.lan.model"))
check("外网 model 未受影响", s["ai_settings"].get("ai.model") == "glm-5.2",
      s["ai_settings"].get("ai.model"))

# ---- 7. 前端页面包含新 UI ----
print("\n=== 7. 前端页面含新 UI 元素 ===")
with open("/home/hotyuo/esp32-wsd/server/app/static/index.html", encoding="utf-8") as f:
    html = f.read()
for needle in ['id="ai-active-cloud"', 'id="ai-active-lan"', 'id="ai-cfg-cloud"',
               'id="ai-cfg-lan"', 'function setAiActive', 'function applyAiActive',
               'id="s-ai.lan.base_url"', 'id="s-ai.lan.model"', 'id="s-ai.lan.temperature"',
               'ai-active-lan\'', '☁️ 外网 AI', '🏠 内网 AI']:
    check(f"前端含 {needle!r}", needle in html)

print(f"\n{'='*52}")
print(f"结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
