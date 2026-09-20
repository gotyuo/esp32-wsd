"""AI 客户端。统一 OpenAI 兼容接口，用标准库 urllib 实现（避免引入第三方依赖）。

支持 provider：
  - openai     -> https://api.openai.com
  - ollama     -> http://localhost:11434/v1（如未设 base_url）
  - deepseek   -> https://api.deepseek.com
  - qwen       -> https://dashscope.aliyuncs.com/compatible-mode/v1
  - gemini     -> https://generativelanguage.googleapis.com/v1beta/openai
  - custom     -> 完全按 base_url
  其余未识别的 provider 也走 custom 逻辑。

调用方传 system + messages；本模块负责：
  1. 从 app_settings 读配置
  2. 组装 OpenAI-兼容请求
  3. 处理响应 / 错误（含网络超时的自动重试）
  4. 返回 (content:str, error:str, usage:dict|None)

v7.53 变更：
  - call_model 支持 retries 自动重试：可重试的瞬时故障（超时/连接重置/5xx/限流）
    自动重试，配置类错误（401/403/404 等 4xx）不重试，避免把错误掩盖成慢请求。
  - 超时完全由 ai.timeout 设置驱动（5~300s，默认 30），不再在调用方硬编码。
  - 新增 friendly_error()：把底层异常翻译成面向用户的中文提示。
  - _read_settings 兼容 ai.system_prompt / ai.prompt 两个键（旧版只写 ai.prompt）。
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ai_client")

_PROVIDER_DEFAULT_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com",
    "ollama": "http://localhost:11434/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

# HTTP 状态码：命中这些才值得重试（瞬时故障）；401/403/404 等配置错误不重试
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}

# 超时/位数上限（秒）
_TIMEOUT_MIN = 5
_TIMEOUT_MAX = 300
_TIMEOUT_DEFAULT = 30
_MAX_TOKENS_MIN = 16
_MAX_TOKENS_MAX = 8192
_MAX_TOKENS_DEFAULT = 512


class SettingsSource:
    """可注入的 settings 读取器，方便测试。"""

    def get(self, key: str) -> str:
        raise NotImplementedError


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v = int(raw)
    except (ValueError, TypeError):
        v = default
    return max(lo, min(hi, v))


def _read_settings(s: SettingsSource) -> Dict[str, str]:
    # 支持两种用法：可注入的 SettingsSource 实例；或直接传一个 dict（main.py 内部调用）
    get = s.get
    return {
        "enabled": get("ai.enabled") or "",
        "provider": get("ai.provider") or "openai",
        "base_url": get("ai.base_url") or "",
        "model": get("ai.model") or "",
        "api_key": get("ai.api_key") or "",
        "timeout": str(_clamp_int(get("ai.timeout"), _TIMEOUT_DEFAULT,
                                  _TIMEOUT_MIN, _TIMEOUT_MAX)),
        "max_tokens": str(_clamp_int(get("ai.max_tokens"), _MAX_TOKENS_DEFAULT,
                                     _MAX_TOKENS_MIN, _MAX_TOKENS_MAX)),
        # 兼容旧版本：旧设置页只写 ai.prompt，新设置页同时写 ai.system_prompt
        "system_prompt": get("ai.system_prompt") or get("ai.prompt") or "",
    }


def _resolve_base_url(provider: str, explicit_base: str) -> str:
    base = explicit_base.strip()
    if base:
        return base.rstrip("/")
    return _PROVIDER_DEFAULT_URLS.get(provider, "")


def _build_prompt_messages(system: str, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        out.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    return out


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否为值得重试的瞬时故障。"""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return True
        if isinstance(reason, ConnectionError):
            return True
        msg = str(reason).lower()
        if "timed out" in msg or "timeout" in msg or "connection" in msg:
            return True
    return False


def friendly_error(err: str) -> str:
    """把底层错误翻译成面向用户的中文提示。未知错误原样返回。

    覆盖：超时 / 连接拒绝 / 连接重置 / DNS / 鉴权 / 模型不存在 / 限流 / 5xx。
    """
    if not err:
        return ""
    low = err.lower()
    if "timed out" in low or "timeout" in low or "timedout" in low:
        return "AI 服务响应超时（网络不稳定或模型生成较慢），已自动重试；仍失败可调大 ai.timeout 后重试"
    if "connection refused" in low or "errno 111" in low:
        return "无法连接 AI 服务（连接被拒绝），请检查 base_url 与 AI 服务是否已启动"
    if "connection reset" in low or "errno 104" in low or "broken pipe" in low:
        return "AI 服务连接被重置，请稍后重试"
    if ("name or service not known" in low or "getaddrinfo" in low
            or "errno -2" in low or "nodename nor servname" in low):
        return "AI 服务域名无法解析，请检查 base_url 是否正确"
    if "http 401" in low or "http 403" in low:
        return "API Key 无效或无权访问，请检查 ai.api_key"
    if "http 404" in low:
        return "模型不存在或无访问权限，请检查 ai.model 与当前 provider 是否匹配"
    if "http 429" in low:
        return "AI 服务限流（请求过于频繁），请稍后重试"
    if low.startswith("http 5"):
        return f"AI 服务暂时不可用（{err}），请稍后重试"
    return err


def call_model(
    settings: Any,  # SettingsSource | Dict[str, str]
    messages: List[Dict[str, str]],
    *,
    timeout_s: Optional[int] = None,
    retries: int = 1,
    retry_backoff: float = 1.0,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """调用大模型。返回 (content, error, usage)。error 非空表示失败；content 非空表示成功。

    - timeout_s：覆盖配置里的 ai.timeout（秒）。不传则用设置值（5~300）。
    - retries：额外重试次数。默认 1（最多 2 次尝试）。仅对瞬时故障重试。
    """
    cfg = _read_settings(settings)

    if cfg["enabled"] not in ("1", "true", "True", "yes"):
        return "", "AI 未启用 (ai.enabled 需为 1/true)", None

    model = cfg["model"].strip()
    if not model:
        return "", "未配置 ai.model", None

    api_key = cfg["api_key"]
    if not api_key:
        return "", "未配置 ai.api_key", None

    base_url = _resolve_base_url(cfg["provider"], cfg["base_url"])
    if not base_url:
        return "", f"无法解析 provider={cfg['provider']} 的 base_url（需 ai.base_url 或已知 provider）", None

    timeout = _clamp_int(timeout_s if timeout_s else cfg["timeout"], _TIMEOUT_DEFAULT,
                         _TIMEOUT_MIN, _TIMEOUT_MAX)
    max_tokens = int(cfg["max_tokens"])

    payload = {
        "model": model,
        "messages": _build_prompt_messages(cfg["system_prompt"], messages),
        "max_tokens": max_tokens,
    }
    # 温度默认 0.3，避免生成过长/发散；调用方可在 messages 后额外传 temperature
    payload.setdefault("temperature", 0.3)

    url = base_url + "/chat/completions"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)

    max_attempts = max(1, retries + 1)
    attempt = 0
    while True:
        attempt += 1
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8") or "{}")
            break  # 请求成功，跳出重试循环
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            err = f"HTTP {e.code}: {body}"
            if e.code in _RETRYABLE_HTTP and attempt < max_attempts:
                log.warning("AI model retryable HTTP %s (attempt %d/%d), retrying in %.1fs",
                            e.code, attempt, max_attempts, retry_backoff * attempt)
                time.sleep(retry_backoff * attempt)
                continue
            log.warning("AI model HTTP error %s: %s", e.code, body)
            return "", err, None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", None)
            err = f"连接失败: {reason if reason is not None else e}"
            if attempt < max_attempts and _is_retryable(e):
                log.warning("AI model retryable conn error (attempt %d/%d): %s",
                            attempt, max_attempts, e)
                time.sleep(retry_backoff * attempt)
                continue
            log.warning("AI model URL error (attempt %d/%d): %s", attempt, max_attempts, e)
            return "", err, None
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("AI model response parse error: %s", e)
            return "", f"响应解析失败: {e}", None
        except Exception as e:  # noqa: BLE001
            log.warning("AI model unexpected error: %s", e)
            return "", str(e), None

    # OpenAI 兼容响应结构：choices[0].message.content
    try:
        choices = raw.get("choices") or []
        if not choices:
            return "", "无 choices", raw
        msg = choices[0].get("message") or {}
        content = msg.get("content", "")
        usage = raw.get("usage")
        finish = choices[0].get("finish_reason")
        if finish == "stop" or content:
            return content, "", usage
        return "", f"finish_reason={finish}", usage
    except Exception as e:  # noqa: BLE001
        return "", f"解析结果异常: {e}", None


# ---------------------------------------------------------------- 便捷封装
_DEFAULT_HISTORY_RETENTION_MINUTES = 30  # 分析时默认看多久的历史


def analyze_alarm(
    settings: SettingsSource,
    *,
    device_id: str,
    patient_id: Optional[int],
    patient_name: Optional[str],
    alarm_reason: str,
    alarm_level: int,
    recent_vitals: List[Dict[str, Any]],
    thresholds: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """针对报警生成一段简短的专业分析，供前端展示 / 企微推送。"""
    cfg = _read_settings(settings)
    system = cfg["system_prompt"].strip() or (
        "你是 ICU 重症监护助理。请根据监护数据做简要的中文医学分析。"
        "输出格式：【诊断倾向】【风险等级】【处置建议】，各用一行。保持简明，不要写客套话。"
    )
    vit_lines = []
    for v in recent_vitals[-20:]:
        vit_lines.append(
            f"ts={v.get('ts','-')} | t={v.get('t','-')}℃ h={v.get('h','-')}%RH "
            f"p={v.get('p','-')}hPa hr={v.get('hr','-')}bpm spO2={v.get('sp_o2','-')}% "
            f"sbp={v.get('sbp','-')} dbp={v.get('dbp','-')} mmHg"
        )
    hist = "\n".join(vit_lines) or "（无近期 vitals 数据）"

    threshold_hint = ""
    if thresholds:
        threshold_hint = (
            f"当前阈值：temp [{thresholds.get('temp_min','-')}~{thresholds.get('temp_max','-')}]℃"
            f", hum [{thresholds.get('hum_min','-')}~{thresholds.get('hum_max','-')}]%RH"
            f", pres [{thresholds.get('pres_min','-')}~{thresholds.get('pres_max','-')}]hPa"
        )

    user = (
        f"患者：{patient_name or patient_id or '未知患者'}（patient_id={patient_id}）；"
        f"设备：{device_id}；报警等级：{alarm_level}（1=预警/2=报警）；报警原因：{alarm_reason}。"
        f"近期 vitals 序列：\n{hist}"
    )
    if threshold_hint:
        user = user + "\n" + threshold_hint
    user = user + "\n\n请给出简明分析。"
    # 后台线程调用，超时跟随 ai.timeout（可配），瞬时故障自动重试一次
    return call_model(settings, [{"role": "user", "content": user}], retries=1)


def test_connection(settings: Any) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    cfg = _read_settings(settings)
    if cfg["enabled"] not in ("1", "true", "True", "yes"):
        return "", "AI 未启用", None
    model = cfg["model"].strip()
    if not model:
        return "", "未配置 ai.model", None
    base_url = _resolve_base_url(cfg["provider"], cfg["base_url"])
    if not base_url:
        return "", "无法解析 base_url", None
    msgs = [{"role": "user", "content": "你好，请回复 OK"}]
    # 注意：不能直接把 cfg 传给 call_model——call_model 会再次 _read_settings，
    # 但 cfg 的 key 已是 "enabled" 而非 "ai.enabled"，会导致误判未启用。
    # 用一个代理 dict，把 key 反转为 ai.* 让 _read_settings 第二次取值正常。
    proxy = {"ai.enabled": cfg["enabled"], "ai.provider": cfg["provider"],
             "ai.base_url": cfg["base_url"], "ai.model": cfg["model"],
             "ai.api_key": cfg["api_key"], "ai.timeout": cfg["timeout"],
             "ai.max_tokens": cfg["max_tokens"], "ai.system_prompt": ""}
    # 测试按钮要求快速反馈：15s 单次 + 瞬时故障重试一次
    return call_model(proxy, msgs, timeout_s=15, retries=1)