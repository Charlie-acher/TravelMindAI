"""测试层：验证会话用量隔离、上下文口径和公开过程恢复。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.services.auth import AuthService
from app.services.chat.events import event_sink, traced_tool
from app.services.chat.metrics import measure_run
from app.services.trip_service import TripService
from app.services.usage import session_usage, usage_call, usage_scope
from app.services.usage_store import save_usage
from app.services.workspace_status import read_workspace_status

"""统计测试函数：只汇总指定用户会话，标题不替代最近上下文，缓存不重复相加。"""

def test_workspace_status_owner_and_latest_context(store_engine):
    user = AuthService(store_engine).create_user("workspace-owner", "test-pass123", "user")
    other = AuthService(store_engine).create_user("workspace-other", "test-pass123", "admin")
    service = TripService(store_engine)
    session = service.create_session("新建对话", user_id=user.id)
    foreign = service.create_session("另一段", user_id=other.id)
    for who, trip, purpose, amount in (
        (user, session, "conversation", 1200), (user, session, "title", 100),
        (other, foreign, "conversation", 9000),
    ):
        with usage_scope(str(uuid4()), lambda call: save_usage(store_engine, call)):
            with session_usage(who.id, trip.id, uuid4(), purpose=purpose):
                with usage_call("deepseek", "custom-model", "api.deepseek.com", "text") as call:
                    call.update(input_tokens=amount, output_tokens=20,
                                total_tokens=amount + 20, cache_read_tokens=amount // 2)
    settings = Settings(model_context_windows={"custom-model": 4000})
    report = read_workspace_status(store_engine, user.id, session.id, settings)
    assert report["total_calls"] == 2
    assert report["known_total_tokens"] == 1340
    assert report["cache_hit_ratio"] == .5
    assert report["latest_context"]["input_tokens"] == 1200
    assert report["latest_context"]["ratio"] == .3
    assert report["models"][0]["input_tokens"] == 1300
    assert "endpoint" not in str(report)
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False)))
    with TestClient(create_app(settings)) as client:
        client.post("/api/v1/auth/login", json={"username": "workspace-other",
                    "password": "test-pass123"}, headers={"X-Requested-With": "TravelMindAI"})
        assert client.get(f"/api/v1/sessions/{session.id}/workspace-status").status_code == 404
        assert client.get(f"/api/v1/sessions/{foreign.id}/workspace-status").status_code == 200


"""过程测试函数：真实工具开始结束对应同一编号，失败公开消息不暴露异常正文。"""

def test_tool_process_records_real_outcomes():
    published = []
    token = event_sink.set(lambda event, data: published.append((event, data)))

    @traced_tool("knowledge", "检索知识库")
    def search(ok):
        if not ok:
            raise ValueError("private secret")
        return "ok"

    try:
        with measure_run("process") as run:
            assert search(True) == "ok"
            try:
                search(False)
            except ValueError:
                pass
            process = run.process_snapshot()
        assert len(process["steps"]) == 2
        assert [step["status"] for step in process["steps"]] == ["completed", "failed"]
        assert len({step["call_id"] for step in process["steps"]}) == 2
        assert published[0][1]["call_id"] == published[1][1]["call_id"]
        assert "private secret" not in str(published)
    finally:
        event_sink.reset(token)


"""最新上下文测试函数：原生工具模型参与统计，最新未知输入不回退旧数字，地图不稀释缓存比例。"""

def test_tool_model_context_and_unknown_latest(store_engine):
    from tests.helpers import TEST_USER_ID
    trip = TripService(store_engine).create_session("工具上下文", user_id=TEST_USER_ID)
    settings = Settings(model_context_windows={"tool-model": 4000})
    with usage_scope("tool-context", lambda call: save_usage(store_engine, call)), \
            session_usage(TEST_USER_ID, trip.id, uuid4()):
        with usage_call("deepseek", "tool-model", "api.deepseek.com", "tools") as call:
            call.update(input_tokens=2000, output_tokens=20, total_tokens=2020,
                        cache_read_tokens=500)
        with usage_call("baidu", "map", "mcp.map.baidu.com", "map"):
            pass
        report = read_workspace_status(store_engine, TEST_USER_ID, trip.id, settings)
        assert report["latest_context"]["ratio"] == .5
        assert report["cache_hit_ratio"] == .25
        with usage_call("deepseek", "tool-model", "api.deepseek.com", "tools"):
            pass
    report = read_workspace_status(store_engine, TEST_USER_ID, trip.id, settings)
    assert report["latest_context"]["input_tokens"] is None
    assert report["latest_context"]["ratio"] is None
    assert report["cache_hit_ratio"] is None


"""标题搜索测试函数：全文历史服务端筛选，特殊字符按文字匹配且不串账号。"""

def test_session_search_scoped_and_literal(store_engine):
    from tests.helpers import TEST_USER_ID
    service = TripService(store_engine)
    target = service.create_session("苏州100%轻松游", user_id=TEST_USER_ID)
    service.create_session("苏州其他行程", user_id=TEST_USER_ID)
    other = AuthService(store_engine).create_user("history-search-other", "test-pass123")
    service.create_session("苏州100%轻松游", user_id=other.id)
    assert [row.id for row in service.list_sessions(TEST_USER_ID, 30, q="100%")] == [target.id]
