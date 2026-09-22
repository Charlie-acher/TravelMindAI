"""费用测试层：验证缓存折扣、未知用量和失败调用仍入账。"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.usage import price_call, usage_call, usage_scope

"""计费测试函数：输入总量包含缓存，不能重复计费；未知缓存不得假装精确。"""

def test_cached_tokens_and_unknown_cost():
    call = {"provider": "kimi", "model": "kimi-k2.6", "endpoint": "api.moonshot.cn",
            "input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 800,
            "started_at": "2026-09-21T02:00:00+00:00", "kind": "text"}
    cost = price_call(call)
    assert Decimal(cost["amount"]) == Decimal("0.00504")
    assert cost["currency"] == "CNY" and cost["status"] == "estimated"
    call["cache_read_tokens"] = None
    assert price_call(call)["amount"] is None
    call["cache_read_tokens"] = 1001
    assert price_call(call)["amount"] is None
    call["endpoint"] = "unverified.example"
    assert price_call(call)["amount"] is None


"""时段测试函数：高峰与非高峰费率不同，未来未维护的日历不猜价格。"""

def test_deepseek_peak_and_offpeak():
    call = {"provider": "deepseek", "model": "deepseek-flash", "endpoint": "api.deepseek.com",
            "input_tokens": 1000000, "output_tokens": 0, "cache_read_tokens": 0,
            "kind": "text", "started_at": "2026-09-21T02:00:00+00:00"}
    assert Decimal(price_call(call)["amount"]) == Decimal("2")
    call["started_at"] = "2026-09-21T12:00:00+00:00"
    assert Decimal(price_call(call)["amount"]) == Decimal("1")
    call["started_at"] = "2027-09-21T02:00:00+00:00"
    assert price_call(call)["amount"] is None


"""失败记录测试函数：已消耗用量在抛错后保留，原异常不被替换。"""

def test_failed_attempt_is_recorded():
    rows = []
    with usage_scope("test-request", rows.append):
        with pytest.raises(ValueError, match="invalid output"):
            with usage_call("kimi", "kimi-k2.6", "api.moonshot.cn", "text") as call:
                call.update(input_tokens=1000, output_tokens=100, cache_read_tokens=800)
                raise ValueError("invalid output")
    assert len(rows) == 2 and rows[1]["success"] is False
    assert rows[0]["state"] == "running" and rows[0]["id"] == rows[1]["id"]
    assert Decimal(rows[1]["cost"]["amount"]) == Decimal("0.00504")
    assert "invalid output" not in str(rows)
    assert datetime.fromisoformat(rows[0]["started_at"]).tzinfo == timezone.utc


"""限流回归函数：保留状态码与异常类别供排查，不保存供应商原文或账户标识。"""

def test_provider_failure_records_safe_diagnostics():
    import httpx
    from openai import RateLimitError

    from app.llm.contracts import ErrorCode, ProviderError

    response = httpx.Response(429, request=httpx.Request("POST", "https://api.example/v1"))
    errors = [RateLimitError("private-account request reached max RPM: 3",
                            response=response, body={"secret": "private-key"}),
              ProviderError("kimi", ErrorCode.INVALID_OUTPUT)]
    rows = []
    with usage_scope("failure-diagnostics", rows.append):
        for error in errors:
            with pytest.raises(type(error)):
                with usage_call("kimi", "kimi-k2.6", "api.moonshot.cn", "tools"):
                    raise error
    assert rows[1]["http_status"] == 429
    assert rows[1]["error_type"] == "RateLimitError"
    assert rows[3]["error_category"] == "invalid_output"
    assert "private-account" not in str(rows) and "private-key" not in str(rows)


"""地域测试函数：业务空间北京端点沿用北京原价，其他地区与订阅端点不猜价。"""

def test_embedding_region_rates():
    call = {"provider": "qwen", "model": "text-embedding-v4", "kind": "embedding",
            "endpoint": "ws-example.cn-beijing.maas.aliyuncs.com", "input_tokens": 1000}
    assert Decimal(price_call(call)["amount"]) == Decimal("0.0005")
    for host in ("ws-example.ap-southeast-1.maas.aliyuncs.com",
                 "token-plan.cn-beijing.maas.aliyuncs.com",
                 "ws-example.cn-beijing.maas.aliyuncs.com.evil.example"):
        call["endpoint"] = host
        assert price_call(call)["amount"] is None


"""真实SDK测试函数：响应JSON无效也记录实际用量，不能只记成功业务结果。"""

def test_adapter_invalid_json_still_has_usage():
    import httpx
    from pydantic import SecretStr

    from app.config import ProviderSettings
    from app.llm.contracts import ModelRequest, ProviderError
    from app.llm.providers import ProviderAdapter

    body = {"id": "usage", "object": "chat.completion", "created": 0, "model": "kimi-k2.6",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "bad json"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
                      "prompt_tokens_details": {"cached_tokens": 800}}}
    rows = []
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body))) as http:
        adapter = ProviderAdapter("kimi", ProviderSettings(model="kimi-k2.6",
            base_url="https://api.moonshot.cn/v1", api_key=SecretStr("test")), http)
        with usage_scope("invalid-json", rows.append), pytest.raises(ProviderError):
            adapter.complete(ModelRequest(messages=[{"role": "user", "content": "JSON"}],
                                          json_object=True))
    assert rows[-1]["success"] is False
    assert Decimal(rows[-1]["cost"]["amount"]) == Decimal("0.00504")
