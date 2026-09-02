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
  3. 处理响应 / 错误
  4. 返回 (content:str, error:str, usage:dict|None)
"""
from __future__ import annotations

import json
import logging
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


class SettingsSource:
    """可注入的 settings 读取器，方便测试。"""

    def get(self, key: str) -> str:
        raise NotImplementedError


def _read_settings(s: SettingsSource) -> Dict[str, str]:
    # 支持两种用法：可注入的 SettingsSource 实例；或直接传一个 dict（main.py 内部调用）
    if isinstance(s, dict):
        return {
            "enabled": s.get("ai.enabled") or "",
            "provider": s.get("ai.provider") or "openai",
            "base_url": s.get("ai.base_url") or "",
            "model": s.get("ai.model") or "",
            "api_key": s.get("ai.api_key") or "",
            "timeout": s.get("ai.timeout") or "30",
            "max_tokens": s.get("ai.max_tokens") or "512",
            "system_prompt": s.get("ai.system_prompt") or "",
        }
    return {
        "enabled": s.get("ai.enabled") or "",
        "provider": s.get("ai.provider") or "openai",
        "base_url": s.get("ai.base_url") or "",
        "model": s.get("ai.model") or "",
        "api_key": s.get("ai.api_key") or "",
        "timeout": s.get("ai.timeout") or "30",
        "max_tokens": s.get("ai.max_tokens") or "512",
        "system_prompt": s.get("ai.system_prompt") or "",
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


def call_model(
    settings: Any,  # SettingsSource | Dict[str, str]
    messages: List[Dict[str, str]],
    *,
    timeout_s: Optional[int] = None,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """调用大模型。返回 (content, error, usage)。error 非空表示失败；content 非空表示成功。"""
    cfg = _read_settings(settings)

    if cfg["enabled"] not in ("1", "true", "True", "yes"):
        return "", "AI 未启用 (ai.enabled 需为 1/true)", None

    model = cfg["model"].strip()
    if not model:
        return "", "未配置 ai.model", None

    base_url = _resolve_base_url(cfg["provider"], cfg["base_url"])
    if not base_url:
        return "", f"无法解析 provider={cfg['provider']} 的 base_url（需 ai.base_url 或已知 provider）", None

    try:
        timeout = int(timeout_s) if timeout_s else int(cfg["timeout"])
    except (ValueError, TypeError):
        timeout = 30
    try:
        max_tokens = int(cfg["max_tokens"])
    except (ValueError, TypeError):
        max_tokens = 512

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
    if cfg["api_key"]:
        req.add_header("Authorization", "Bearer " + cfg["api_key"])

    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        log.warning("AI model HTTP error %s: %s", e.code, body)
        return "", f"HTTP {e.code}: {body}", None
    except urllib.error.URLError as e:
        log.warning("AI model URL error: %s", e)
        return "", f"连接失败: {e.reason}", None
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
    return call_model(settings, [{"role": "user", "content": user}], timeout_s=25)


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
    # 覆盖 system_prompt 为空（测试只关心连通性，不让默认 prompt 干扰）
    cfg["system_prompt"] = ""
    return call_model(cfg, msgs, timeout_s=15)
