"""单模型客户端测试：检查LangChain消息转换，并在本机模拟HTTP响应。

这里验证请求参数、超时配置和错误脱敏；不能用这些结果声称真实模型已接通。
"""

import json
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.config import Settings, load_settings
from app.llm.client import DeepSeekClient, ModelClientError

"""LangChain返回AIMessage，客户端应把其中的文本交还给原来的需求服务。

临时替换invoke方法，确认调用确实经过LangChain；HTTP模拟器拒绝所有实际请求。
这也保证切换实现后，业务服务仍然收到JSON字符串，无需跟着修改。
"""

def test_langchain_message_becomes_plain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [{"role": "user", "content": "去杭州玩三天，请输出JSON"}]
    received: list[object] = []

    """模拟LangChain统一的消息对象，不需要自己拼DeepSeek的choices外层。"""

    def invoke(self: ChatOpenAI, input: object, **kwargs: object) -> AIMessage:
        received.append(input)
        return AIMessage(content='{"days":3}', response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ChatOpenAI, "invoke", invoke)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        assert model.generate_json(messages) == '{"days":3}'
    assert received == [messages]


"""客户端必须显式拿到密钥；不配置密钥时普通Settings仍然可以创建。"""

def test_missing_key_does_not_send_request() -> None:
    with httpx.Client() as http:
        with pytest.raises(ModelClientError, match="未配置"):
            DeepSeekClient(Settings(deepseek_api_key=None), http)


"""读取指定文件，也兼容已有学习示例的DS_API_KEY；打印Settings保持脱敏。"""

@pytest.mark.parametrize("variable", ["TRAVELMIND_DEEPSEEK_API_KEY", "DS_API_KEY"])
def test_explicit_key_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    monkeypatch.delenv("TRAVELMIND_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DS_API_KEY", raising=False)
    config = tmp_path / ".env"
    config.write_text(f"{variable}=fake-private-key\n", encoding="utf-8")
    settings = load_settings(config)
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "fake-private-key"
    assert "fake-private-key" not in repr(settings)


"""超时必须为有限正数，避免意外设置成无限等待。"""

@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_is_validated(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(deepseek_timeout_seconds=timeout)


"""检查真实客户端生成的HTTP请求，而不是只测手写的假模型方法。"""

def test_json_request_contract() -> None:
    requests: list[httpx.Request] = []

    """记录请求并返回符合DeepSeek外层格式的响应；content内层才是需求JSON。"""

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                # SDK会校验标准响应字段，模拟响应也应按真实协议提供完整数据。
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"days":3}'},
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        model = DeepSeekClient(
            Settings(
                deepseek_api_key=SecretStr("fake-private-key"),
                deepseek_model="deepseek-v4-pro",
                deepseek_timeout_seconds=12,
            ),
            http,
        )
        assert model.generate_json([{"role": "user", "content": "旅行需求JSON"}]) == '{"days":3}'
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == "Bearer fake-private-key"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    # DeepSeek仍使用max_tokens；不能让ChatOpenAI默认改名后丢失这个上限。
    assert payload["max_tokens"] == 2048
    assert "max_completion_tokens" not in payload
    assert all(value == 12 for value in request.extensions["timeout"].values())


"""提供方错误内容可能含用户文本；对外只显示分类，不透传原响应。"""

@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_http_errors_are_sanitized(status: int) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="fake-private-key provider-private-body")
        )
    ) as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake-private-key")), http)
        with pytest.raises(ModelClientError) as caught:
            model.generate_json([])
    assert "fake-private-key" not in str(caught.value)
    assert "provider-private-body" not in str(caught.value)
    assert str(status) in str(caught.value)


"""HTTP成功也可能给错外层格式或因token上限截断，不能当作正常答案。"""

@pytest.mark.parametrize(
    "response_body",
    [
        "not-json",
        "[]",
        "{}",
        '{"choices":[]}',
        '{"choices":[{"finish_reason":"stop","message":{"content":null}}]}',
        '{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}',
    ],
)
def test_bad_response_envelope_is_rejected(response_body: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=response_body))
    ) as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        with pytest.raises(ModelClientError):
            model.generate_json([])


"""超时只产生一次请求；格式修复不应把网络故障变成重复收费请求。"""

def test_timeout_has_no_automatic_retry() -> None:
    requests: list[httpx.Request] = []

    """人为制造读取超时，错误文本故意带敏感占位词以验证脱敏。"""

    def timeout(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("fake-private-key", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        with pytest.raises(ModelClientError, match="超时") as caught:
            model.generate_json([])
    assert len(requests) == 1
    assert "fake-private-key" not in str(caught.value)
