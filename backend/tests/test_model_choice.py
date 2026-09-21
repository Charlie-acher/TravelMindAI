"""模型选择测试层：通过真实SDK检查用户首选、原生工具及故障接续。"""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import SecretStr

from app.config import ProviderSettings, Settings
from app.llm.contracts import capability_scope
from app.llm.gateway import GatewayState
from app.llm.gateway_client import GatewayClient

"""配置函数：仅使用假密钥及MockTransport，禁止测试访问真实模型。"""

def settings():
    return Settings(deepseek_api_key=SecretStr("fake"), model_providers={
        name: ProviderSettings(api_key=SecretStr("fake"), model=f"{name}-test",
                               base_url=f"https://{name}.example/v1")
        for name in ("kimi", "qwen")
    })


"""响应函数：构造标准模型正文或原生工具调用。"""

def completion(*, tool=False):
    message = {"role": "assistant", "content": None if tool else '{"ok":true}'}
    if tool:
        message["tool_calls"] = [{"id": "call_lookup", "type": "function", "function": {
            "name": "lookup", "arguments": '{"city":"杭州"}',
        }}]
    return httpx.Response(200, json={"id": "test", "object": "chat.completion",
        "created": 0, "model": "tested-model",
        "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        "choices": [{"index": 0,
            "message": message, "finish_reason": "tool_calls" if tool else "stop"}]})


"""选择Qwen后所有文本能力和原生工具都使用它，不受旧DeepSeek路由限制。"""

def test_selected_provider_covers_text_json_and_native_tools():
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append((request.url.host, body))
        return completion(tool=bool(body.get("tools")))

    config = settings()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GatewayClient(config, http, GatewayState(config.model_gateway),
                               selected_provider="qwen")
        for capability in ("extract", "research", "review", "chat"):
            with capability_scope(capability):
                assert client.generate_json([{"role": "user", "content": "核对"}]) == '{"ok":true}'
        tool = {"type": "function", "function": {"name": "lookup", "description": "查询城市",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}}
        answer = client.model.bind_tools([tool]).invoke("查杭州")
        assert answer.tool_calls[0]["args"] == {"city": "杭州"}
        native = client.gateway.adapters["qwen"].native(
            [ToolMessage(content="杭州", tool_call_id="lookup-1")], {})
        assert (native.input_tokens, native.output_tokens, native.total_tokens) == (12, 6, 18)
    assert {host for host, _ in seen} == {"qwen.example"}
    assert client.gateway.used_providers == ["qwen"]


"""工具模型超时前未执行工具，可切备用；后续审核保持备用且保留工具消息。"""

def test_native_failure_switches_without_retry_and_preserves_tool_history():
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append((request.url.host, body))
        if request.url.host == "api.deepseek.com":
            return httpx.Response(503, text="secret")
        return completion()

    config = settings()
    state = GatewayState(config.model_gateway)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GatewayClient(config, http, state, selected_provider="deepseek")
        messages = [AIMessage(content="", tool_calls=[{
            "id": "lookup-1", "name": "lookup", "args": {"city": "杭州"}}]),
            ToolMessage(content="已核实西湖", tool_call_id="lookup-1")]
        assert client.model.invoke(messages).content == '{"ok":true}'
        with capability_scope("review"):
            client.generate_json([{"role": "user", "content": "审核"}])
    assert [host for host, _ in seen] == ["api.deepseek.com", "kimi.example", "kimi.example"]
    assert seen[1][1]["messages"][-1]["tool_call_id"] == "lookup-1"
    assert state.snapshot()["providers"]["deepseek"]["in_flight"] == 0


"""非法模型别名必须在HTTP模型验证时拒绝，不能把任意名称传给供应商。"""

@pytest.mark.parametrize("provider", ["minimax", "unknown", "https://evil.example"])
def test_request_rejects_unselectable_provider(provider):
    from pydantic import ValidationError

    from app.schemas.requirement.chat import RequirementMessage
    with pytest.raises(ValidationError):
        RequirementMessage(message="你好", selected_provider=provider)


"""状态接口列出三个固定选项，未配置项显式禁用且不泄露凭据。"""

def test_status_lists_three_choices():
    from app.main import create_app
    from tests.helpers import authenticated_client
    app = create_app(Settings(deepseek_api_key=SecretStr("private")))
    with authenticated_client(app) as client:
        response = client.get("/api/v1/requirements/status")
    options = response.json()["providers"]
    assert [item["id"] for item in options] == ["deepseek", "kimi", "qwen"]
    assert options[0]["configured"] and not options[1]["configured"]
    assert "private" not in response.text and "api_key" not in response.text


"""模型返回途中收到停止指令时，不交付结果，也不误记为提供方故障。"""

def test_cancelled_model_does_not_deliver_or_hold_slot():
    from threading import Event

    from app.services.chat.events import ChatCancelled, request_cancelled
    cancelled = Event()
    config = settings()
    state = GatewayState(config.model_gateway)
    def respond(request):
        cancelled.set()
        return completion()
    token = request_cancelled.set(cancelled)
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = GatewayClient(config, http, state, selected_provider="qwen")
            with pytest.raises(ChatCancelled):
                client.generate_json([{"role": "user", "content": "查询"}])
        assert state.snapshot()["providers"]["qwen"]["in_flight"] == 0
        assert state.snapshot()["providers"]["qwen"]["calls"] == 0
    finally:
        request_cancelled.reset(token)
