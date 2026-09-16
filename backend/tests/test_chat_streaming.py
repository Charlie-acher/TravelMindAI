"""测试层：检查流式公开字段、模型分片、自然引导和保存后的完成事件。"""

import json
from datetime import date
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import Settings
from app.llm.client import DeepSeekClient, ModelClientError
from app.schemas.document.search import SearchResult
from app.schemas.requirement.chat import RequirementMessage
from app.services.chat.events import draft_text, event_sink, public_answer
from app.services.chat.rag import ground_chat_response
from app.services.chat.service import build_requirement_response
from tests.helpers import FakeModel, answer, evidence

"""公开范围测试函数：只展示有本轮引用的回答，不能透出抽取内容、推理或错误引用。"""

def test_partial_json_only_exposes_public_answer() -> None:
    raw = '{"status":"answered","reasoning_content":"不公开", "points":['
    raw += '{"source_ids":[1],"text":"杭州可以'
    assert draft_text(raw) == ""
    with public_answer({1}):
        assert draft_text(raw) == "杭州可以"
        assert draft_text(raw.replace('[1]', '[99]')) == ""
        # text先出现时引用数组可能还没读完，不能把[1或[1,25]的前缀当有效引用。
        assert draft_text('{"status":"answered","points":[{"text":"先写的文字",'
                          '"source_ids":[1') == ""
        assert draft_text('{"status":"answered","points":[{"text":"先写的文字",'
                          '"source_ids":[1,25]}]}') == ""
        assert draft_text('{"extraction":{"destination":"杭州"}}') == ""
    assert draft_text(raw) == ""


"""真实分片测试函数：模型尚未结束时已推送公开草稿，最终 JSON 仍完整返回。"""

def test_model_streams_before_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    received = []
    raw = '{"status":"answered","points":[{"source_ids":[1],"text":"' + "杭州适合慢慢游览。" * 4

    def chunks(self, messages, **kwargs):
        yield AIMessageChunk(content=raw, additional_kwargs={"reasoning_content": "保密推理"})
        assert received and received[-1][0] == "draft"
        yield AIMessageChunk(content='"}]}', response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ChatOpenAI, "stream", chunks)
    token = event_sink.set(lambda event, data: received.append((event, data)))
    try:
        with httpx.Client() as http, public_answer({1}):
            model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
            result = model.generate_json([])
        assert json.loads(result)["points"]
        assert "保密推理" not in str(received)
    finally:
        event_sink.reset(token)


"""截断测试函数：收到草稿不等于生成成功，缺少 stop 时仍抛出错误。"""

def test_truncated_stream_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ChatOpenAI, "stream", lambda *args, **kwargs: iter([
        AIMessageChunk(content="{}", response_metadata={"finish_reason": "length"}),
    ]))
    token = event_sink.set(lambda *args: None)
    try:
        with httpx.Client() as http, pytest.raises(ModelClientError, match="截断"):
            DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http).generate_json([])
    finally:
        event_sink.reset(token)


"""目的地引导测试函数：先简介再询问缺项，不重复索要用户已提供的人数。"""

def test_destination_gets_overview_then_natural_questions() -> None:
    response = build_requirement_response(
        RequirementMessage(message="我们两个人想去杭州", reference_date=date(2026, 9, 16)),
        FakeModel([answer(destination="杭州", travelers=2)]), "test",
    )
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    model.generate_json.return_value = json.dumps({"status": "answered", "points": [
        {"text": "杭州湖滨步行街依傍西湖，适合散步。", "source_ids": [1]},
    ]})
    result = ground_chat_response(response, search, model, "杭州")
    assert result.reply.startswith("杭州湖滨")
    assert "几天" in result.reply and "预算" in result.reply
    assert "几个人" not in result.reply and "请一起补充" not in result.reply
    payload = json.loads(model.generate_json.call_args.args[0][1]["content"])
    assert "简介" in payload["question"]


"""条件登记测试函数：只登记需求不应附加无关的无法确认结论。"""

def test_personal_conditions_do_not_get_failure_boilerplate() -> None:
    response = build_requirement_response(
        RequirementMessage(message="想去杭州", reference_date=date(2026, 9, 16)),
        FakeModel([answer(destination="杭州")]), "test",
    )
    search = Mock()
    search.search.return_value = SearchResult(items=[])
    result = ground_chat_response(response, search, Mock(), "杭州")
    assert "无法确认" not in result.reply
    assert "几个人" in result.reply and "几天" in result.reply and "预算" in result.reply


"""SSE拆包函数：测试真实HTTP输出中的事件顺序与结构。"""

def frames(text: str) -> list[tuple[str, dict]]:
    return [(block.splitlines()[0].removeprefix("event: "),
             json.loads(block.splitlines()[1].removeprefix("data: ")))
            for block in text.split("\n\n") if block.startswith("event:")]


"""流接口持久化测试函数：完成事件只能在保存后出现，重试和409仍遵循原契约。"""

def test_sse_save_retry_and_conflict(store_engine, monkeypatch) -> None:
    from uuid import uuid4

    from app.api.requirement import history as routes
    from app.main import create_app
    from app.services.requirement.history import RequirementHistoryService
    from app.services.trip_service import TripService
    from tests.helpers import TEST_USER_ID, authenticated_client

    service = RequirementHistoryService(store_engine)
    session = TripService(store_engine).create_session("流式验收", user_id=TEST_USER_ID)
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[routes.get_history_service] = lambda: service
    model = FakeModel([answer(destination="杭州")])
    monkeypatch.setattr(routes, "DeepSeekClient", lambda *args: model)
    search = Mock()
    search.search.return_value = SearchResult(items=[])

    def searches(request):
        yield search

    monkeypatch.setattr(routes, "get_search_service", searches)
    url = f"/api/v1/sessions/{session.id}/requirement-messages/stream"
    payload = {"message": "想去杭州", "message_id": str(uuid4()), "expected_revision": 0}
    with authenticated_client(app) as client:
        result = client.post(url, json=payload)
        assert result.headers["content-type"].startswith("text/event-stream")
        events = frames(result.text)
        assert events[0][0] == "progress" and events[-1][0] == "done"
        saved = service.read(session.id)
        assert events[-1][1] == saved.turns[0].model_dump(mode="json")
        assert frames(client.post(url, json=payload).text)[-1] == events[-1]
        conflict = frames(client.post(url, json=payload | {"message_id": str(uuid4())}).text)
        assert conflict[-1][0] == "error" and conflict[-1][1]["status"] == 409
        assert service.read(session.id).revision == 1


"""权限测试函数：未登录、跨用户和跨站写入在发送SSE头之前拒绝。"""

def test_sse_authentication_and_owner(store_engine) -> None:
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.services.auth import AuthService
    from app.services.trip_service import TripService

    auth = AuthService(store_engine)
    alice = auth.create_user("stream-alice", "test123", "user")
    auth.create_user("stream-bob", "test123", "user")
    session = TripService(store_engine).create_session("仅Alice", user_id=alice.id)
    settings = Settings(database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False),
    ))
    payload = {"message": "想去杭州", "message_id": str(uuid4()), "expected_revision": 0}
    url = f"/api/v1/sessions/{session.id}/requirement-messages/stream"
    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        assert client.post(url, json=payload).status_code == 401
        headers = {"X-Requested-With": "TravelMindAI"}
        assert client.post("/api/v1/auth/login", json={
            "username": "stream-bob", "password": "test123",
        }, headers=headers).status_code == 200
        assert client.post(url, json=payload, headers=headers).status_code == 404
        assert client.post(url, json=payload, headers=headers | {
            "Origin": "https://bad.example",
        }).status_code == 403


"""错误与断线测试函数：浏览器离开后工作继续关闭资源，底层错误不透出异常原文。"""

def test_stream_disconnect_keeps_worker_alive(monkeypatch) -> None:
    import asyncio
    from threading import Event
    from types import SimpleNamespace
    from uuid import uuid4

    from app.api.requirement import history as routes
    from app.schemas.requirement.history import SavedRequirementMessage
    from app.services.chat.events import progress

    release, finished = Event(), Event()

    def work(*args):
        progress("test", "已经开始")
        assert release.wait(3)
        finished.set()
        return SimpleNamespace(model_dump=lambda **kwargs: {"revision": 1})

    monkeypatch.setattr(routes, "DeepSeekClient", Mock())
    monkeypatch.setattr(routes, "process_saved_message", work)

    async def scenario():
        request = SimpleNamespace(state=SimpleNamespace(request_id="test"),
                                  app=SimpleNamespace(state=SimpleNamespace(
                                      settings=Settings(), chat_workers=set(),
                                  )))
        response = await routes.stream_saved_message(
            uuid4(), SavedRequirementMessage(
                message="杭州", message_id=uuid4(), expected_revision=0,
            ),
            request, Mock(),
        )
        iterator = response.body_iterator
        assert "received" in await anext(iterator)
        assert "已经开始" in await anext(iterator)
        await iterator.aclose()
        assert not finished.is_set()
        release.set()
        await asyncio.gather(*list(request.app.state.chat_workers))
        assert finished.is_set()

    asyncio.run(scenario())


"""停服测试函数：释放数据库前等待属于当前应用的断线任务完成。"""

def test_shutdown_waits_for_disconnected_worker() -> None:
    import asyncio

    from app.main import create_app

    async def scenario():
        app = create_app(Settings(database_url=None))
        release, finishing = asyncio.Event(), asyncio.Event()

        async def work():
            await release.wait()
            finishing.set()

        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        worker = asyncio.create_task(work())
        app.state.chat_workers.add(worker)
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        await shutdown
        assert finishing.is_set()

    asyncio.run(scenario())


"""错误脱敏测试函数：流开始后的失败用error结束，不泄露底层异常也不假装保存。"""

@pytest.mark.parametrize("error,status", [
    (ModelClientError("模型请求超时"), 503), (RuntimeError("provider-secret-key"), 500),
])
def test_stream_error_is_sanitized_and_never_done(monkeypatch, error, status) -> None:
    from uuid import uuid4

    from app.api.requirement import history as routes
    from app.main import create_app
    from tests.helpers import authenticated_client

    app = create_app(Settings(database_url=None))
    service = Mock()
    app.dependency_overrides[routes.get_history_service] = lambda: service
    monkeypatch.setattr(routes, "DeepSeekClient", Mock(side_effect=error))
    with authenticated_client(app) as client:
        result = client.post(f"/api/v1/sessions/{uuid4()}/requirement-messages/stream", json={
            "message": "想去杭州", "message_id": str(uuid4()), "expected_revision": 0,
        })
        events = frames(result.text)
        assert events[0][0] == "progress" and events[-1][0] == "error"
        assert events[-1][1]["status"] == status
        assert "provider-secret-key" not in result.text
        assert all(event != "done" for event, _ in events)
        service.append.assert_not_called()
