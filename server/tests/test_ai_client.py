"""ai_client 单元测试（v7.53 修复验证）。

覆盖：
  - _read_settings：ai.system_prompt/ai.prompt 兼容、timeout/max_tokens 钳制
  - call_model：瞬时故障自动重试、配置类错误不重试、重试耗尽
  - friendly_error：超时/断连/鉴权/模型404/限流/5xx 中文映射
说明：全部 mock urllib，不发真实网络请求。
"""
import json
import socket
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, "app")  # server/tests 目录下执行

import ai_client  # noqa: E402

SETTINGS = {
    "ai.enabled": "true",
    "ai.provider": "custom",
    "ai.base_url": "http://mock.test/v1",
    "ai.model": "test-model",
    "ai.api_key": "sk-test",
    "ai.timeout": "30",
    "ai.max_tokens": "512",
    "ai.system_prompt": "",
}


class FakeResp:
    """模拟 urlopen 成功响应（可做上下文管理器）。"""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


def _ok_payload(content="好的"):
    return {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _http_error(code):
    return urllib.error.HTTPError(
        "http://mock.test/v1/chat/completions", code, "err", {}, None)


class ReadSettingsTest(unittest.TestCase):
    def test_system_prompt_fallback_to_prompt(self):
        cfg = ai_client._read_settings(
            {"ai.system_prompt": "", "ai.prompt": "旧版提示词", "ai.enabled": "true"})
        self.assertEqual(cfg["system_prompt"], "旧版提示词")

    def test_timeout_clamped(self):
        cfg = ai_client._read_settings({"ai.timeout": "9999", "ai.enabled": "true"})
        self.assertEqual(cfg["timeout"], "300")
        cfg = ai_client._read_settings({"ai.timeout": "1", "ai.enabled": "true"})
        self.assertEqual(cfg["timeout"], "5")

    def test_max_tokens_clamped(self):
        cfg = ai_client._read_settings({"ai.max_tokens": "abc", "ai.enabled": "true"})
        self.assertEqual(cfg["max_tokens"], "512")


class CallModelTest(unittest.TestCase):
    def test_disabled(self):
        s = dict(SETTINGS, **{"ai.enabled": "false"})
        content, err, usage = ai_client.call_model(s, [])
        self.assertEqual(content, "")
        self.assertIn("未启用", err)

    def test_missing_api_key(self):
        s = dict(SETTINGS, **{"ai.api_key": ""})
        content, err, _ = ai_client.call_model(s, [{"role": "user", "content": "hi"}])
        self.assertEqual(content, "")
        self.assertIn("api_key", err)

    def test_timeout_then_success_retries_once(self):
        """瞬时读超时 -> 自动重试 -> 成功。"""
        with mock.patch("ai_client.urllib.request.urlopen",
                        side_effect=[socket.timeout("timed out"),
                                     FakeResp(_ok_payload("分析结果"))]) as m:
            content, err, usage = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=1)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(content, "分析结果")
        self.assertEqual(err, "")
        self.assertEqual(usage["completion_tokens"], 5)

    def test_http503_then_success_retries(self):
        with mock.patch("ai_client.urllib.request.urlopen",
                        side_effect=[_http_error(503),
                                     FakeResp(_ok_payload("ok"))]) as m:
            content, err, _ = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=1)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(content, "ok")

    def test_no_retry_on_401(self):
        """鉴权错误是配置问题，不应重试掩盖。"""
        with mock.patch("ai_client.urllib.request.urlopen",
                        side_effect=_http_error(401)) as m:
            content, err, _ = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=2)
        self.assertEqual(m.call_count, 1)
        self.assertIn("HTTP 401", err)

    def test_no_retry_on_404_model(self):
        with mock.patch("ai_client.urllib.request.urlopen",
                        side_effect=_http_error(404)) as m:
            content, err, _ = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=2)
        self.assertEqual(m.call_count, 1)
        self.assertIn("HTTP 404", err)

    def test_retries_exhausted(self):
        with mock.patch("ai_client.urllib.request.urlopen",
                        side_effect=socket.timeout("timed out")) as m:
            content, err, _ = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=1, retry_backoff=0)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(content, "")
        self.assertIn("连接失败", err)

    def test_success_first_try_no_retry(self):
        with mock.patch("ai_client.urllib.request.urlopen",
                        return_value=FakeResp(_ok_payload("直接成功"))) as m:
            content, err, _ = ai_client.call_model(
                SETTINGS, [{"role": "user", "content": "hi"}], retries=2)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(content, "直接成功")
        self.assertEqual(err, "")


class FriendlyErrorTest(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "HTTPSConnectionPool(host='api.siliconflow.cn', port=443): Read timed out. (read timeout=25)":
                "超时",
            "连接失败: [Errno 111] Connection refused": "连接被拒绝",
            "连接失败: [Errno 104] Connection reset by peer": "连接被重置",
            "连接失败: [Errno -2] Name or service not known": "域名无法解析",
            "HTTP 401: invalid api key": "API Key 无效",
            "HTTP 403: forbidden": "API Key 无效",
            "HTTP 404: model not found": "模型不存在",
            "HTTP 429: rate limit": "限流",
            "HTTP 502: bad gateway": "服务暂时不可用",
            "某未知错误": "某未知错误",
        }
        for raw, expect_fragment in cases.items():
            out = ai_client.friendly_error(raw)
            self.assertIn(expect_fragment, out, f"raw={raw} -> {out}")

    def test_empty(self):
        self.assertEqual(ai_client.friendly_error(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)