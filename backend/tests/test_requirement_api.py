"""对话HTTP测试：走真实FastAPI与合并服务，只有模型被替换为离线答案。

验证浏览器连续发消息时保留信息、各页面互不串话、错误格式一致。
这一步采用前端携带上一轮需求，不写数据库或创建服务端全局会话。
"""


import pytest
from fastapi.testclient import TestClient

from app.api.requirement.routes import get_requirement_model
from app.config import Settings
from app.llm.client import ModelClientError
from app.main import create_app
from tests.helpers import FakeModel, answer

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


"""空会话取消限制时，温和引导先说目的地，不用技术校验错误让用户猜原因。"""

def test_missing_profile_guides_user_to_destination() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    # 只给一个答案：缺少历史不是模型输出错误，不应再花一次调用尝试删除。
    model = FakeModel([answer(
        intent="modify_trip", remove_items={"hard_constraints": ["不爬山"]},
    )])
    app.dependency_overrides[get_requirement_model] = lambda: model
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requirements/messages", json={"message": "取消不爬山的限制"},
        )
    assert response.status_code == 502
    notice = response.json()["error"]["message"]
    assert "还没有建立" in notice and "旅行档案" in notice
    assert "目的地" in notice and "我想去杭州" in notice
    assert "未通过" not in notice


"""已有旅行需求的失败不能误报没有档案，必须说明旧需求保留。"""

def test_existing_profile_failure_keeps_context_message() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    app.dependency_overrides[get_requirement_model] = lambda: FakeModel([{}, {}])
    with TestClient(app) as client:
        response = client.post("/api/v1/requirements/messages", json={
            "message": "取消限制", "previous": answer(destination="杭州"),
        })
    notice = response.json()["error"]["message"]
    assert "已保留" in notice and "还没有建立" not in notice


"""模型只填了空删除列表时没有实际删除动作，不能挡住正常的首次需求。"""

def test_empty_removal_list_does_not_require_profile() -> None:
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    app.dependency_overrides[get_requirement_model] = lambda: FakeModel([answer(
        destination="杭州", remove_items={"hard_constraints": []},
    )])
    with TestClient(app) as client:
        response = client.post("/api/v1/requirements/messages", json={"message": "想去杭州"})
    assert response.status_code == 200
    assert response.json()["result"]["extraction"]["destination"] == "杭州"


"""超范围请求应给出可调整的范围；已有旅行不能被截成五天或覆盖日期。"""

@pytest.mark.parametrize("has_previous", [False, True])
def test_unsupported_duration_explains_limits(has_previous: bool) -> None:
    previous = answer(
        destination="杭州", start_date="2026-10-01", end_date="2026-10-03", days=3,
    )
    app = create_app(Settings(database_url=None, deepseek_api_key=None))
    # 模拟遵守提示词的模型：保留原始跨度的文字依据，不伪造范围内的days。
    app.dependency_overrides[get_requirement_model] = lambda: FakeModel([answer(
        intent="other", destination="杭州", start_date="2026-10-01",
        assumptions=["用户要求10月1日至6日，共6天，超出当前2至5天的支持范围。"],
    )])
    payload: dict[str, object] = {"message": "去杭州，10月1日到6日"}
    if has_previous:
        payload["previous"] = previous
    with TestClient(app) as client:
        response = client.post("/api/v1/requirements/messages", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unsupported" and body["changed_fields"] == []
    assert "2～5天" in body["reply"] and "调整" in body["reply"]
    assert "未通过" not in body["reply"]
    if has_previous:
        assert body["result"]["extraction"] == previous
    else:
        assert body["result"]["extraction"]["days"] is None
        assert body["result"]["extraction"]["end_date"] is None


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
