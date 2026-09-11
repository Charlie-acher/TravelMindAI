"""对话HTTP测试：走真实FastAPI与合并服务，只有模型被替换为离线答案。

验证浏览器连续发消息时保留信息、各页面互不串话、错误格式一致。
这一步采用前端携带上一轮需求，不写数据库或创建服务端全局会话。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.requirements import get_requirement_model
from app.config import Settings
from app.llm.client import ModelClientError
from app.main import create_app

"""构造模型输出，null表示这轮未提到；kwargs按用例覆盖字段。"""

def answer(**changes: object) -> dict[str, object]:
    return {
        "intent": "plan_trip",
        "destination": None,
        "origin": None,
        "start_date": None,
        "end_date": None,
        "days": None,
        "travelers": None,
        "total_budget": None,
        "pace": None,
        "interests": [],
        "dietary": [],
        "lodging_preferences": [],
        "hard_constraints": [],
        "excluded_items": [],
        "assumptions": [],
    } | changes


class FakeModel:
    """顺序交出预设答案，用一次测试模拟同一用户的多轮对话。"""

    """保存答案迭代器，不连接任何远程服务。"""

    def __init__(self, answers: list[dict[str, object]]) -> None:
        self.answers = iter(answers)

    """参数与正式模型相同，因此可以通过依赖覆盖注入路由。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        return json.dumps(next(self.answers))


"""先说城市再补充人数预算，响应给出回复、需求、状态、变化字段和请求编号。"""

def test_conversation_and_page_isolation() -> None:
    model = FakeModel(
        [
            answer(destination="杭州"),
            answer(intent="modify_trip", travelers=3, days=3, total_budget="6000"),
            answer(destination="苏州"),
        ]
    )
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    app.dependency_overrides[get_requirement_model] = lambda: model
    with TestClient(app) as client:
        first = client.post("/api/v1/requirements/messages", json={"message": "想去杭州"})
        assert first.status_code == 200
        assert first.json()["status"] == "needs_clarification"
        previous = first.json()["result"]["extraction"]
        second = client.post(
            "/api/v1/requirements/messages",
            json={
                "message": "三个人三天六千元",
                "previous": previous,
            },
        )
        body = second.json()
        assert second.status_code == 200 and body["status"] == "complete"
        assert body["result"]["extraction"]["destination"] == "杭州"
        assert set(body["changed_fields"]) == {"travelers", "days", "total_budget"}
        assert body["request_id"] == second.headers["X-Request-ID"]
        assert "需求" in body["reply"]
        # 另一页面没有传previous，不能自动获得第一个页面的人数或预算。
        other = client.post("/api/v1/requirements/messages", json={"message": "想去苏州"})
        assert other.json()["result"]["extraction"]["travelers"] is None


"""闲聊不会把已有旅行变成新目的地，也不会误报已完成一次修改。"""

def test_unsupported_turn_is_not_applied() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    app.dependency_overrides[get_requirement_model] = lambda: FakeModel(
        [answer(intent="other", destination="北京")]
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requirements/messages",
            json={
                "message": "你好",
                "previous": answer(destination="杭州"),
            },
        )
    assert response.json()["status"] == "unsupported"
    assert response.json()["changed_fields"] == []
    assert response.json()["result"]["extraction"]["destination"] == "杭州"


"""输入校验在调用模型前执行；客户端不能提交任意字段或非法历史需求。"""

@pytest.mark.parametrize(
    "payload",
    [
        {"message": " "},
        {"message": "a" * 6001},
        {"message": "去杭州", "api_key": "secret"},
        {"message": "改人数", "previous": {"travelers": 99}},
    ],
)
def test_invalid_request(payload: dict[str, object]) -> None:
    with TestClient(create_app(Settings(database_url=None, deepseek_api_key=None))) as client:
        response = client.post("/api/v1/requirements/messages", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


"""未配置模型时页面可查询状态，发消息得到503，而预算能力仍可使用。"""

def test_model_unconfigured_is_reported_without_network() -> None:
    with TestClient(create_app(Settings(database_url=None, deepseek_api_key=None))) as client:
        status = client.get("/api/v1/requirements/status")
        assert status.status_code == 200 and status.json()["configured"] is False
        response = client.post("/api/v1/requirements/messages", json={"message": "去杭州"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
        assert client.get("/api/v1/health/live").status_code == 200


"""模型重复给无效答案时，返回502，前端保持上一轮需求并展示失败提示。"""

def test_bad_model_output_returns_unified_error() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    app.dependency_overrides[get_requirement_model] = lambda: FakeModel([{}, {}])
    with TestClient(app) as client:
        response = client.post("/api/v1/requirements/messages", json={"message": "去杭州"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "REQUIREMENT_EXTRACTION_FAILED"


"""提供方网络错误沿用统一响应，不变成框架堆栈。"""

def test_network_error_returns_unified_error() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))

    """依赖创建或调用中出现的模型错误均由应用的异常处理器转换。"""

    def unavailable() -> FakeModel:
        raise ModelClientError("DeepSeek请求超时，请稍后重试")

    app.dependency_overrides[get_requirement_model] = unavailable
    with TestClient(app) as client:
        response = client.post("/api/v1/requirements/messages", json={"message": "去杭州"})
    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
