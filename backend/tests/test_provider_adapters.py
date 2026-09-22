"""模型适配层测试：三家共用真实SDK和模拟HTTP，验证契约而非远程接通情况。"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import ProviderSettings, Settings, load_settings
from app.llm.contracts import ModelRequest, ProviderError
from app.llm.providers import ProviderAdapter, configured_adapters

PROVIDERS = ("deepseek", "qwen", "kimi")
REQUEST = ModelRequest(messages=[{"role": "user", "content": "返回城市杭州"}])


"""工具契约测试函数：要求工具调用却只返回正文，应进入网关已有的恢复路径。"""

def test_required_tool_plain_text_is_invalid_output():
    from langchain_core.messages import HumanMessage
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope()),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            adapter("kimi", http).native([HumanMessage(content="查询")],
                                        {"tool_choice": "required"})
    assert caught.value.code == "invalid_output"


"""模拟响应函数：使用兼容接口完整外层结构，包含标准token统计。"""

def envelope(content: str = "杭州", finish: str = "stop") -> dict:
    return {
        "id": "reply", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    }


"""适配器构造函数：密钥和模型均为假值，客户端只能访问测试传输器。"""

def adapter(provider: str, http: httpx.Client) -> ProviderAdapter:
    return ProviderAdapter(provider, ProviderSettings(
        base_url="https://provider.example/v1", model="test-model",
        api_key=SecretStr("private-test-key"), timeout_seconds=7,
    ), http)


"""三家文本与JSON均走同一契约，提供方专用参数不混传。"""

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("json_object", [False, True])
def test_complete_contract(provider: str, json_object: bool) -> None:
    captured = []
    content = '{"city":"杭州"}' if json_object else "杭州"

    """响应函数：记录真实SDK发出的参数，返回当前测试的文本或JSON。"""

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=envelope(content))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = adapter(provider, http).complete(REQUEST.model_copy(
            update={"json_object": json_object, "max_output_tokens": 321},
        ))
    assert result.text == content
    assert result.provider == provider and result.model == "test-model"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (8, 2, 10)
    assert result.elapsed_seconds >= 0
    assert len(captured) == 1
    request = captured[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://provider.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer private-test-key"
    assert all(value == 7 for value in request.extensions["timeout"].values())
    assert body["max_tokens"] == 321 and "max_completion_tokens" not in body
    assert body["model"] == "test-model"
    if provider in ("deepseek", "kimi"):
        assert body["thinking"] == {"type": "disabled"}
    elif provider == "qwen":
        assert body["enable_thinking"] is False and "thinking" not in body
    if json_object:
        assert "JSON" in str(body["messages"])
        assert body.get("response_format") == {"type": "json_object"}
    else:
        assert "response_format" not in body


"""失败类别可供后续网关判断，不包含提供方原文，也不在适配层自动重试。"""

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("status,code", [
    (400, "invalid_request"), (401, "authentication"), (403, "authentication"),
    (402, "quota_exceeded"),
    (429, "rate_limit"), (500, "unavailable"), (503, "unavailable"),
])
def test_http_errors(provider: str, status: int, code: str) -> None:
    requests = []

    """响应函数：故意让错误正文带私有占位值，验证对外分类不会透传。"""

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, text="private-test-key sensitive-user-text")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ProviderError) as caught:
            adapter(provider, http).complete(REQUEST)
    assert len(requests) == 1
    assert caught.value.code == code and caught.value.started is False
    assert "private-test-key" not in str(caught.value)
    assert "sensitive-user-text" not in repr(caught.value)


"""超时和网络断开保留不同类别；SDK内置重试必须关闭。"""

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"),
                                       (httpx.ConnectError, "network")])
def test_transport_errors(provider: str, error: type, code: str) -> None:
    requests = []

    """响应函数：在网络层抛错并累计调用次数，检查SDK没有暗中重试。"""

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error("private-test-key", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ProviderError) as caught:
            adapter(provider, http).complete(REQUEST)
    assert len(requests) == 1 and caught.value.code == code
    assert "private-test-key" not in str(caught.value)


"""空响应、截断、安全过滤和错误JSON不能冒充成功结果。"""

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("content,finish,json_object,code", [
    ("", "stop", False, "invalid_output"), ("partial", "length", False, "invalid_output"),
    ("", "content_filter", False, "refused"),
    ("not-json", "stop", True, "invalid_output"), ("[]", "stop", True, "invalid_output"),
])
def test_bad_output(provider: str, content: str, finish: str,
                    json_object: bool, code: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope(content, finish)),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            adapter(provider, http).complete(
                REQUEST.model_copy(update={"json_object": json_object}),
            )
    assert caught.value.code == code


"""流事件函数：真实SDK解析SSE，推理字段不能泄漏到回答增量。"""

def sse(delta: dict, finish: str | None = None, usage: dict | None = None) -> bytes:
    payload = {"id": "reply", "object": "chat.completion.chunk", "created": 0,
               "model": "test-model", "choices": [
                   {"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}
    return ("data: " + json.dumps(payload) + "\n\n").encode()


"""完整流得到增量及一个完成事件；末尾usage不能丢失。"""

@pytest.mark.parametrize("provider", PROVIDERS)
def test_stream_contract(provider: str) -> None:
    body = (sse({"reasoning_content": "private reasoning"}) + sse({"content": "杭"})
            + sse({"content": "州"}, "stop", envelope()["usage"]) + b"data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body,
                                      headers={"content-type": "text/event-stream"}),
    )) as http:
        events = list(adapter(provider, http).stream(REQUEST))
    assert [e.delta for e in events if e.kind == "delta"] == ["杭", "州"]
    assert events[-1].kind == "completed"
    assert events[-1].result.text == "杭州"
    assert events[-1].result.total_tokens == 10


"""正文已发后中断，明确标记started，供后续网关禁止跨模型拼接。"""

@pytest.mark.parametrize("provider", PROVIDERS)
def test_stream_failure_after_text(provider: str) -> None:
    class BrokenStream(httpx.SyncByteStream):
        """故障字节流类：在已经交付一个正文块后中断连接。"""

        """迭代方法：先发送合法SSE，再模拟底层连接中断。"""

        def __iter__(self):
            yield sse({"content": "杭"})
            raise httpx.ReadError("private-test-key")

    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=BrokenStream(),
                                      headers={"content-type": "text/event-stream"}),
    )) as http:
        stream = adapter(provider, http).stream(REQUEST)
        assert next(stream).delta == "杭"
        with pytest.raises(ProviderError) as caught:
            next(stream)
    assert caught.value.started is True and caught.value.code == "network"
    assert "private-test-key" not in str(caught.value)


"""嵌套配置只读显式文本密钥；缺少密钥的记录不进入池。"""

def test_explicit_configuration_and_pool(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "TRAVELMIND_MODEL_PROVIDERS__QWEN__API_KEY=private-test-key\n"
        "TRAVELMIND_MODEL_PROVIDERS__QWEN__BASE_URL=https://qwen.example/v1\n"
        "TRAVELMIND_MODEL_PROVIDERS__QWEN__MODEL=qwen-example\n"
        "TRAVELMIND_MODEL_PROVIDERS__KIMI__API_KEY=\n"
        "TRAVELMIND_VISION_API_KEY=vision-private\n", encoding="utf-8",
    )
    settings = load_settings(path)
    assert "private-test-key" not in repr(settings)
    with httpx.Client() as http:
        assert set(configured_adapters(settings, http)) == {"qwen"}
        assert configured_adapters(Settings(vision_api_key=SecretStr("vision")), http) == {}


"""返回的真实模型标识与配置别名可能不同，应记录响应中的标识。"""

def test_result_keeps_response_model() -> None:
    response = envelope()
    response["model"] = "actual-model-version"
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response),
    )) as http:
        assert adapter("deepseek", http).complete(REQUEST).model == "actual-model-version"


"""过滤标记与正文同块返回时，先拒绝再输出，不能先把被拦截正文发给页面。"""

@pytest.mark.parametrize("provider", PROVIDERS)
def test_stream_filter_does_not_emit_filtered_text(provider: str) -> None:
    body = sse({"content": "filtered private text"}, "content_filter")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body,
                                      headers={"content-type": "text/event-stream"}),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            next(adapter(provider, http).stream(REQUEST))
    assert caught.value.code == "refused" and caught.value.started is False


"""没有finish_reason的断流不能算成功；未报告usage不能填成零费用。"""

@pytest.mark.parametrize("provider", PROVIDERS)
def test_stream_without_finish_is_incomplete(provider: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=sse({"content": "杭"}),
                                      headers={"content-type": "text/event-stream"}),
    )) as http:
        stream = adapter(provider, http).stream(REQUEST)
        assert next(stream).delta == "杭"
        with pytest.raises(ProviderError) as caught:
            next(stream)
    assert caught.value.code == "invalid_output" and caught.value.started is True


"""非法响应外壳由SDK或统一校验拒绝，不抛出原始解析异常。"""

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("body", ["not-json", "{}", '{"choices":[]}'])
def test_bad_envelope(provider: str, body: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            adapter(provider, http).complete(REQUEST)
    assert caught.value.code == "invalid_output"


"""完整填写的空密钥记录被排除，填了密钥但缺地址型号则明确报配置错误。"""

def test_incomplete_keyed_provider_is_configuration_error() -> None:
    with httpx.Client() as http:
        with pytest.raises(ProviderError) as caught:
            configured_adapters(Settings(model_providers={
                "kimi": ProviderSettings(api_key=SecretStr("private-key")),
            }), http)
    assert caught.value.code == "configuration"




"""SDK未保留的原始流拒绝字段，也必须在发送正文前处理。"""

@pytest.mark.parametrize("provider", PROVIDERS)
def test_raw_stream_refusal(provider: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=sse({
            "content": "filtered-text", "refusal": "refused",
        }, "stop"), headers={"content-type": "text/event-stream"}),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            next(adapter(provider, http).stream(REQUEST))
    assert caught.value.code == "refused" and caught.value.started is False


"""限流等待时间由适配器保留，网关才能尊重提供方要求或跳过本次重试。"""

@pytest.mark.parametrize("header,expected", [("2", 2), ("-1", None), ("bad", None)])
def test_retry_after_is_preserved(header: str, expected: float | None) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": header}),
    )) as http:
        with pytest.raises(ProviderError) as caught:
            adapter("deepseek", http).complete(REQUEST)
    assert caught.value.retry_after_seconds == expected
