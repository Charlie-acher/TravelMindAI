"""网关业务适配测试层：经过真实SDK验证能力路由、HTTP降级和SSE兼容。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import GatewaySettings, ProviderSettings, Settings
from app.llm.client import ModelClientError
from app.llm.contracts import capability_scope
from app.llm.gateway import GatewayState
from app.llm.gateway_client import GatewayClient
from app.services.chat.events import event_sink

"""业务客户端应走显式能力路由，并保留原来的文本与JSON字符串接口。"""

def test_business_json_uses_capability_and_fallback():
    seen = []
    events = []

    """模拟HTTP函数：主提供方503，备用返回合法的业务JSON。"""

    def respond(request):
        seen.append((request.url.host, json.loads(request.content)))
        if request.url.host == "api.deepseek.com":
            return httpx.Response(503, text="private-key")
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "qwen-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok":true}'},
                         "finish_reason": "stop"}],
        })

    settings = Settings(deepseek_api_key=SecretStr("fake"), model_providers={
        "qwen": ProviderSettings(api_key=SecretStr("fake"), model="qwen-test",
                                 base_url="https://qwen.example/v1"),
    }, model_gateway=GatewaySettings(
        routes={"review": {"deepseek": 1, "qwen": 1}}, retry_delay_seconds=0,
    ))
    state = GatewayState(settings.model_gateway, random_value=lambda: 0)
    token = event_sink.set(lambda name, data: events.append((name, data)))
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = GatewayClient(settings, http, state)
            with capability_scope("review"):
                answer = client.generate_json([{"role": "user", "content": "JSON审查"}])
                assert answer == '{"ok":true}'
            with pytest.raises(ModelClientError):
                client.generate_json([])  # extract未授权任何候选。
    finally:
        event_sink.reset(token)
    assert [host for host, _ in seen] == ["api.deepseek.com", "api.deepseek.com", "qwen.example"]
    assert any(event == "fallback" and data["to_alias"] == "qwen" for event, data in events)
    assert state.snapshot()["fallback_count"] == 1


"""模型错误不会透传私有响应正文，新旧业务协议仍抛ModelClientError。"""

def test_gateway_client_error_is_sanitized():
    settings = Settings(deepseek_api_key=SecretStr("private-key"))
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, text="private-key sensitive-text"),
    )) as http:
        with pytest.raises(ModelClientError) as caught:
            GatewayClient(settings, http, GatewayState(settings.model_gateway)).generate_text([])
    assert "private-key" not in str(caught.value) and "sensitive-text" not in str(caught.value)


"""未授权的提供方配置不完整，也不能拖垮已授权DeepSeek路径。"""

def test_unused_incomplete_provider_does_not_block_client():
    settings = Settings(deepseek_api_key=SecretStr("fake"), model_providers={
        "kimi": ProviderSettings(api_key=SecretStr("fake")),
    })
    with httpx.Client() as http:
        client = GatewayClient(settings, http, GatewayState(settings.model_gateway))
        assert set(client.gateway.adapters) == {"deepseek"}


"""管理员统计接口保持认证边界，普通用户不能取得网关配置或健康信息。"""

def test_gateway_status_authorization():
    from fastapi.testclient import TestClient

    from app.api.auth import get_current_user
    from app.main import create_app
    from app.models.auth import User

    app = create_app(Settings())
    with TestClient(app) as client:
        assert client.get("/api/v1/requirements/gateway-status").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: User(
        username="reader", role="user", is_active=True,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/requirements/gateway-status").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: User(
        username="manager", role="admin", is_active=True,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/requirements/gateway-status")
    assert response.status_code == 200
    assert response.json()["scope"] == "process_model_calls"
    assert "api_key" not in response.text and "base_url" not in response.text


"""普通状态接口反映已授权的备用提取模型，而非硬编码只认DeepSeek。"""

def test_status_recognizes_authorized_alternative():
    from app.main import create_app
    from tests.helpers import authenticated_client

    settings = Settings(deepseek_api_key=None, model_providers={
        "qwen": ProviderSettings(api_key=SecretStr("fake"), model="qwen-test",
                                 base_url="https://qwen.example/v1"),
    }, model_gateway=GatewaySettings(routes={"extract": {"qwen": 1}}))
    with authenticated_client(create_app(settings)) as client:
        result = client.get("/api/v1/requirements/status").json()
    assert result["configured"] is True and result["model"] == "qwen-test"
