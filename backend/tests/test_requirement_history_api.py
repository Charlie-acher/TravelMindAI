"""持久化对话接口验收：HTTP和PostgreSQL走真实实现，只替换收费模型。"""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from app.api.requirement.history import get_history_service, get_saved_model
from app.config import Settings
from app.main import create_app
from app.schemas.document.search import SearchResult
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, FakeModel, answer
from tests.helpers import authenticated_client as TestClient

"""空知识库替身函数：M2回归仍走RAG调用链，但无证据时不额外消耗假模型输出。"""


@pytest.fixture(autouse=True)
def empty_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.requirement import history as requirement_history

    search = Mock()
    search.search.return_value = SearchResult(items=[])

    """检索依赖替身函数：提供空结果，供原有需求存储回归使用。"""

    def searches(request: object):
        yield search

    monkeypatch.setattr(requirement_history, "get_search_service", searches)

"""请求只带原话及版本，客户端没有previous字段；上下文必须从数据库恢复。"""

def message(text: str, revision: int = 0) -> dict[str, object]:
    return {"message": text, "message_id": str(uuid4()), "expected_revision": revision}


"""第一轮保存后，换HTTP客户端刷新读取并续聊；重复重试不消耗下一份模型答案。"""

def test_refresh_continue_retry_and_isolation(store_engine: Engine) -> None:
    trips = TripService(store_engine)
    session = trips.create_session("刷新验收", user_id=TEST_USER_ID)
    second = trips.create_session("新会话", user_id=TEST_USER_ID)
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_history_service] = lambda: RequirementHistoryService(store_engine)
    app.dependency_overrides[get_saved_model] = lambda: model
    model = FakeModel(
        [
            # 首轮故意误标为修改，验证正式服务校正后保存，刷新不恢复成错误标签。
            answer(intent="modify_trip", destination="杭州", days=3,
                   travelers=2, total_budget="5000"),
            answer(intent="modify_trip", travelers=3),
            answer(intent="other"),
            answer(intent="modify_trip", origin="银川"),
        ]
    )
    url = f"/api/v1/sessions/{session.id}/requirement-messages"
    first = message("杭州三天两人五千")
    with TestClient(app) as client:
        saved = client.post(url, json=first)
        assert saved.status_code == 200
        assert saved.json()["response"]["result"]["message_intent"] == "plan_trip"
        assert client.post(url, json=first).json() == saved.json()
        assert client.post(url, json=message("旧页面发送")).status_code == 409
        # 更改原话后不能沿用相同的幂等编号。
        assert client.post(url, json=first | {"message": "换一句"}).status_code == 409
    with TestClient(app) as refreshed:
        history = refreshed.get(url).json()
        assert history["revision"] == 1
        assert history["turns"][0] == saved.json()
        changed = refreshed.post(url, json=message("改成三个人", 1)).json()
        # 真正存在旧档案的后续修改不能也被改成首次规划。
        assert changed["response"]["result"]["message_intent"] == "modify_trip"
        fields = changed["response"]["result"]["extraction"]
        assert fields["travelers"] == 3 and fields["destination"] == "杭州"
        assert fields["days"] == 3 and fields["total_budget"] == "5000"
        assert (
            refreshed.post(url, json=message("你好", 2)).json()["response"]["status"]
            == "knowledge"
        )
        continued = refreshed.post(url, json=message("从银川出发", 3)).json()
        assert continued["response"]["result"]["extraction"]["travelers"] == 3
        assert (
            continued["response"]["result"]["reference_date"]
            == saved.json()["response"]["result"]["reference_date"]
        )
        assert (
            refreshed.get(f"/api/v1/sessions/{second.id}/requirement-messages").json()["turns"]
            == []
        )
        assert refreshed.get(f"/api/v1/sessions/{uuid4()}/requirement-messages").status_code == 404


"""模型连续失败不写半轮对话，重试仍可基于原版本提交。"""

def test_model_failure_does_not_commit(store_engine: Engine) -> None:
    session = TripService(store_engine).create_session("失败回滚", user_id=TEST_USER_ID)
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_history_service] = lambda: RequirementHistoryService(store_engine)
    model = FakeModel([{}, {}, answer(destination="苏州")])
    app.dependency_overrides[get_saved_model] = lambda: model
    url = f"/api/v1/sessions/{session.id}/requirement-messages"
    payload = message("想去苏州")
    with TestClient(app) as client:
        assert client.post(url, json=payload).status_code == 502
        assert client.get(url).json()["revision"] == 0
        assert client.post(url, json=payload).json()["revision"] == 1


"""无数据库时返回可读错误；空消息、伪造旧需求等输入在调用模型前被拒绝。"""

@pytest.mark.parametrize("changes", [{"message": " "}, {"expected_revision": -1}, {"previous": {}}])
def test_saved_message_validation(changes: dict[str, object]) -> None:
    with TestClient(create_app(Settings(database_url=None, deepseek_api_key=None))) as client:
        url = f"/api/v1/sessions/{uuid4()}/requirement-messages"
        assert client.get(url).status_code == 503
        assert client.post(url, json=message("杭州") | changes).status_code == 422
