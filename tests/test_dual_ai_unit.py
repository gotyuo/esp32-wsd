#!/usr/bin/env python3
"""v7.58 双 AI 模型逻辑单元测试（本地直接调 _read_settings，不走 API，不受限流影响）"""
import sys, importlib, json

sys.path.insert(0, '/home/hotyuo/esp32-wsd/server/app')
import ai_client
importlib.reload(ai_client)

read = ai_client._read_settings
passed = failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")

# ---- 场景 1: active 未设置 → 完全走外网（向后兼容）----
print("场景 1: ai.active 未设置 → 走外网配置（旧版行为兼容）")
r = read({"ai.enabled": "1", "ai.provider": "openai",
          "ai.base_url": "https://api.openai.com/v1", "ai.model": "gpt-4o",
          "ai.api_key": "sk-1", "ai.timeout": "30", "ai.max_tokens": "512",
          "ai.system_prompt": "你是ICU医生"})
check("active 默认 cloud", r["active"] == "cloud", r["active"])
check("外网 model 生效", r["model"] == "gpt-4o", r["model"])
check("外网 base_url 生效", r["base_url"] == "https://api.openai.com/v1", r["base_url"])
check("temperature 回退 0.3", r["temperature"] == "0.3", r["temperature"])

# ---- 场景 2: active=lan + 内网齐全 → 走内网 ----
print("\n场景 2: ai.active=lan 且内网配置齐全 → 走内网")
r = read({"ai.enabled": "1", "ai.provider": "openai",
          "ai.base_url": "https://token.sensenova.cn/v1", "ai.model": "glm-5.2",
          "ai.api_key": "sk-xxx", "ai.timeout": "30", "ai.max_tokens": "512",
          "ai.system_prompt": "你是ICU医生",
          "ai.active": "lan",
          "ai.lan.provider": "custom",
          "ai.lan.base_url": "http://172.18.10.200:1025/v1",
          "ai.lan.model": "qwen3_32b", "ai.lan.api_key": "",
          "ai.lan.temperature": "0.2", "ai.lan.timeout": "60",
          "ai.lan.max_tokens": "1024"})
check("active=lan", r["active"] == "lan", r["active"])
check("内网 model 生效", r["model"] == "qwen3_32b", r["model"])
check("内网 base_url 生效", r["base_url"] == "http://172.18.10.200:1025/v1", r["base_url"])
check("内网 temperature 生效", r["temperature"] == "0.2", r["temperature"])
check("内网 timeout 生效", r["timeout"] == "60", r["timeout"])
check("内网 max_tokens 生效", r["max_tokens"] == "1024", r["max_tokens"])
check("system_prompt 共用", r["system_prompt"] == "你是ICU医生")
check("内网 api_key 空值安全", r["api_key"] == "", repr(r["api_key"]))

# ---- 场景 3: active=lan 但内网 base_url 为空 → 自动回退外网 ----
print("\n场景 3: ai.active=lan 但内网 base_url 未填 → 自动回退外网（防误配不可用）")
r = read({"ai.enabled": "1", "ai.provider": "openai",
          "ai.base_url": "https://api.siliconflow.cn/v1", "ai.model": "Qwen/Qwen3.5-4B",
          "ai.api_key": "sk-abc", "ai.timeout": "30", "ai.max_tokens": "512",
          "ai.system_prompt": "你是ICU医生",
          "ai.active": "lan", "ai.lan.model": ""})
check("回退外网 model", r["model"] == "Qwen/Qwen3.5-4B", r["model"])
check("回退外网 base_url", r["base_url"] == "https://api.siliconflow.cn/v1", r["base_url"])
check("回退后 active 仍记为 lan（仅日志）", r["active"] == "lan", r["active"])

# ---- 场景 4: active=lan 但内网 model 为空 → 回退外网 ----
print("\n场景 4: ai.active=lan 但内网 model 未填 → 自动回退外网")
r = read({"ai.enabled": "1", "ai.provider": "deepseek",
          "ai.base_url": "https://api.deepseek.com/v1", "ai.model": "deepseek-chat",
          "ai.api_key": "sk-d", "ai.timeout": "30", "ai.max_tokens": "512",
          "ai.system_prompt": "SP",
          "ai.active": "lan", "ai.lan.base_url": "http://10.0.0.1:11434/v1",
          "ai.lan.model": ""})
check("回退外网 model", r["model"] == "deepseek-chat", r["model"])
check("回退外网 base_url", r["base_url"] == "https://api.deepseek.com/v1", r["base_url"])

# ---- 场景 5: 空配置（_read_settings 只负责取键，默认 URL 由 _resolve_base_url 提供）----
print("\n场景 5: 完全空配置 → 不抛异常，键存在")
r = read({})
check("enabled 空", r["enabled"] == "", repr(r["enabled"]))
check("provider 默认 openai", r["provider"] == "openai", r["provider"])
check("base_url 空（默认 URL 由 _resolve_base_url 解析）", r["base_url"] == "", repr(r["base_url"]))
check("model 空（默认 model 由 _resolve_base_url 解析）", r["model"] == "", repr(r["model"]))
check("timeout 默认 30", r["timeout"] == "30", r["timeout"])
check("max_tokens 默认 512", r["max_tokens"] == "512", r["max_tokens"])

# 验证 _resolve_base_url 确实提供默认 URL
check("resolve(openai,'') 返回默认 URL",
      ai_client._resolve_base_url("openai", "").startswith("https://api.openai.com"),
      ai_client._resolve_base_url("openai", ""))

# ---- 场景 6: 非法数值 clamp（timeout/max_tokens 在 _read_settings；temperature 在 call_model）----
print("\n场景 6: 非法/越界数值安全 clamp")
r = read({"ai.enabled": "1", "ai.active": "lan",
          "ai.lan.base_url": "http://10.0.0.1/v1", "ai.lan.model": "m",
          "ai.lan.timeout": "abc", "ai.lan.max_tokens": "999999",
          "ai.lan.temperature": "99"})
check("timeout 非法回退 30", r["timeout"] == "30", r["timeout"])
check("max_tokens 越界 clamp 32768", r["max_tokens"] == "32768", r["max_tokens"])
# temperature 的 clamp 在 call_model 执行（L209: max(0.0, min(2.0, float(_t)))），此处验证该表达式
for raw, expect in [("99", 2.0), ("0", 0.0), ("-5", 0.0), ("1.5", 1.5), ("abc", 0.3), ("0.2", 0.2)]:
    try:
        got = max(0.0, min(2.0, float(raw)))
    except (TypeError, ValueError):
        got = 0.3
    check(f"temperature {raw!r} -> {expect}", got == expect, f"got {got}")

# ---- 场景 7: max_tokens 上限放宽后，51200 不再被压到 8192 ----
print("\n场景 7: _MAX_TOKENS_MAX 放宽到 32768（支持 32B 长上下文）")
check("常量=32768", ai_client._MAX_TOKENS_MAX == 32768, str(ai_client._MAX_TOKENS_MAX))
r = read({"ai.enabled": "1", "ai.max_tokens": "51200"})
check("51200 仍 clamp 到 32768（不超模型上限）", r["max_tokens"] == "32768", r["max_tokens"])
r = read({"ai.enabled": "1", "ai.max_tokens": "16000"})
check("16000 原样保留（旧版会压到 8192）", r["max_tokens"] == "16000", r["max_tokens"])

# ---- 场景 8: call_model 实际发出的 payload（mock urlopen，无需网络）----
print("\n场景 8: call_model 实际 payload（monkeypatch urlopen 捕获）")
import urllib.request as _ur

captured = {}
def fake_urlopen(req, data=None, timeout=None):
    captured["url"] = req.full_url
    captured["headers"] = dict(req.header_items())
    captured["payload"] = json.loads(data.decode("utf-8"))
    captured["timeout"] = timeout
    class _R:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "OK"},
                                            "finish_reason": "stop"}],
                               "usage": {"total_tokens": 5}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _R()

_orig = ai_client.urllib.request.urlopen
ai_client.urllib.request.urlopen = fake_urlopen
try:
    # 内网配置 + temperature 越界
    settings_lan = {"ai.enabled": "1", "ai.active": "lan",
                    "ai.lan.provider": "custom",
                    "ai.lan.base_url": "http://172.18.10.200:1025/v1",
                    "ai.lan.model": "qwen3_32b", "ai.lan.api_key": "",
                    "ai.lan.temperature": "99", "ai.lan.timeout": "12",
                    "ai.lan.max_tokens": "1024", "ai.system_prompt": "SP"}
    content, err, usage = ai_client.call_model(settings_lan,
        [{"role": "user", "content": "你好"}], timeout_s=None, retries=0)
    pl = captured["payload"]
    check("返回内容 OK", content == "OK", repr(content))
    check("无错误", err == "", repr(err))
    check("url 拼 /chat/completions",
          captured["url"] == "http://172.18.10.200:1025/v1/chat/completions",
          captured["url"])
    check("model=qwen3_32b", pl.get("model") == "qwen3_32b", pl.get("model"))
    check("stream=False 显式写入", pl.get("stream") is False, repr(pl.get("stream")))
    check("temperature 越界 99 -> clamp 2.0", pl.get("temperature") == 2.0, pl.get("temperature"))
    check("max_tokens=1024", pl.get("max_tokens") == 1024, pl.get("max_tokens"))
    check("timeout 用配置 12s", captured["timeout"] == 12, captured["timeout"])
    check("messages 含 system+user",
          [m["role"] for m in pl["messages"]] == ["system", "user"],
          pl["messages"])
    check("usage 返回", usage is not None)
finally:
    ai_client.urllib.request.urlopen = _orig

# ---- 场景 9: 内网 api_key 为空 → 不发送 Authorization 头（内网服务通常免 key）----
print("\n场景 9: 内网 api_key 为空 → 不带 Authorization 头")
ai_client.urllib.request.urlopen = fake_urlopen
try:
    ai_client.call_model(settings_lan, [{"role": "user", "content": "x"}], retries=0)
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    check("无 Authorization 头", "authorization" not in hdrs, str(captured["headers"]))
    check("Content-Type 正确", "application/json" in hdrs.get("content-type", ""),
          hdrs.get("content-type"))
finally:
    ai_client.urllib.request.urlopen = _orig

# ---- 场景 10: 外网 api_key 非空 → 带 Bearer Authorization ----
print("\n场景 10: 外网 api_key 非空 → 带 Bearer 头")
ai_client.urllib.request.urlopen = fake_urlopen
try:
    ai_client.call_model({"ai.enabled": "1", "ai.active": "cloud",
                          "ai.provider": "openai",
                          "ai.base_url": "https://api.openai.com/v1",
                          "ai.model": "gpt-4o", "ai.api_key": "sk-test123",
                          "ai.temperature": "0.2"},
                         [{"role": "user", "content": "x"}], retries=0)
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    check("Bearer 头存在", hdrs.get("authorization") == "Bearer sk-test123",
          hdrs.get("authorization"))
finally:
    ai_client.urllib.request.urlopen = _orig

# ---- 场景 11: 外网缺 api_key 仍严格拒绝（安全边界）----
print("\n场景 11: 外网缺 api_key 仍拒绝（安全边界未放宽）")
_, err11, _ = ai_client.call_model({"ai.enabled": "1", "ai.active": "cloud",
    "ai.provider": "openai", "ai.base_url": "https://api.openai.com/v1",
    "ai.model": "gpt-4o", "ai.api_key": ""},
    [{"role": "user", "content": "x"}], retries=0)
check("外网缺 key 拒绝", "未配置 ai.api_key" in err11, repr(err11))

# custom/ollama/xinference provider 即使 active=cloud 也允许无 key（本地部署场景）
# 需 mock urlopen，否则会真实连接 localhost:11434
ai_client.urllib.request.urlopen = fake_urlopen
_, err12, _ = ai_client.call_model({"ai.enabled": "1", "ai.active": "cloud",
    "ai.provider": "ollama", "ai.base_url": "http://localhost:11434/v1",
    "ai.model": "qwen2.5", "ai.api_key": ""},
    [{"role": "user", "content": "x"}], retries=0)
ai_client.urllib.request.urlopen = _orig
check("ollama 无 key 允许", err12 == "", repr(err12))

print(f"结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
