"""测试层：验收主聊天必须检索、失败不降级，以及引用随对话保存恢复。"""

import json
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.requirement import history as routes
from app.config import Settings
from app.llm.embeddings import EmbeddingError
from app.main import create_app
from app.schemas.document.search import SearchResult
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.services.requirement.extract import build_result
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import FakeModel, answer, evidence

"""响应构造函数：提供旧需求流程产生的结果，等待RAG补充知识回答。"""


def response(message: str) -> RequirementChatResponse:
    return RequirementChatResponse(
        result=build_result(message, date(2026, 9, 14),
                            TravelRequestExtraction.model_validate(answer(intent="travel_info"))),
        reply="原来的范围提示", status="unsupported", changed_fields=[], request_id="test",
    )


"""追问持久化测试函数：首轮引导不查地图网页，恢复后简短回答仍沿用杭州问题。"""


def test_saved_clarification_continues_short_reply(store_engine: Engine, monkeypatch) -> None:
    from app.schemas.document.answer import WebSearchResult
    from tests.helpers import RecordingModel

    search = Mock()
    search.search.return_value = SearchResult(items=[evidence()])

    """检索替身函数：保留真实接口和数据库保存，只替换收费检索。"""

    def searches(request: object):
        yield search

    monkeypatch.setattr(routes, "get_search_service", searches)
    web = Mock(return_value=WebSearchResult(status="empty"))
    maps = Mock()
    monkeypatch.setattr(routes.WebSearchClient, "search", web)
    monkeypatch.setattr(routes.AmapClient, "lookup", maps)
    model = RecordingModel([json.dumps(answer(intent="travel_info")),
                            json.dumps(answer(intent="plan_trip")), json.dumps({
        "status": "insufficient", "points": [],
        "clarification": "你更喜欢自然风景还是历史街区？",
    })])
    session = TripService(store_engine).create_session("距离偏好接续")
    app = create_app(Settings(database_url=None))
    history_service = RequirementHistoryService(store_engine)
    app.dependency_overrides[routes.get_history_service] = lambda: history_service
    app.dependency_overrides[routes.get_saved_model] = lambda: model
    url = f"/api/v1/sessions/{session.id}/requirement-messages"
    first = {"message": "杭州有哪些值得玩的景点，三五个离得不远",
             "message_id": str(uuid4()), "expected_revision": 0}
    with TestClient(app) as client:
        saved = client.post(url, json=first)
        assert saved.status_code == 200
        assert "步行多久" in saved.json()["response"]["reply"]
        assert client.get(url).json()["turns"][0] == saved.json()
        assert client.post(url, json=first).json() == saved.json()
        assert len(model.calls) == 1
        web.assert_not_called()
        maps.assert_not_called()
        second = client.post(url, json={"message": "步行半小时以内",
                                       "message_id": str(uuid4()), "expected_revision": 1})
        assert second.status_code == 200
        assert second.json()["response"]["status"] == "knowledge"
        assert "请一起补充" not in second.json()["response"]["reply"]
        assert client.get(url).json()["revision"] == 2
        assert "杭州" in search.search.call_args.args[0]
        assert "杭州" in web.call_args.args[0]
        payload = json.loads(model.calls[-1][-1]["content"])
        assert "助手追问" in payload["conversation_context"]
        assert payload["question"] == "步行半小时以内"


"""长问题测试函数：完整分段检索，不丢末尾问题；模型只收到去重后的5段证据。"""


def test_chat_always_retrieves_and_preserves_full_question() -> None:
    from app.services.chat.rag import ground_chat_response

    search = Mock()
    hit = evidence()
    search.search.return_value = SearchResult(items=[hit])
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可步行游览。", "source_ids": [1]}],
    })
    message = "问" * 5990 + "末尾的真实问题"
    result = ground_chat_response(response(message), search, model, "杭州")
    queries = [call.args[0] for call in search.search.call_args_list]
    assert all(0 < len(query) <= 800 for query in queries)
    assert len(queries) <= 8
    assert "末尾的真实问题" in queries[-1]
    payload = json.loads(model.generate_json.call_args.args[0][1]["content"])
    assert payload["question"] == message
    assert "杭州" in payload["conversation_context"]
    assert len(payload["sources"]) == 1
    assert result.knowledge.sources[0].hit == hit
    assert result.status == "knowledge"
    assert "[1]" not in result.reply


"""失败与不足测试函数：服务故障直接失败，无证据不调用回答模型或自由发挥。"""


def test_chat_fail_closed_and_empty_knowledge() -> None:
    from app.services.chat.rag import ground_chat_response

    search, model = Mock(), Mock()
    search.search.side_effect = EmbeddingError("未配置")
    with pytest.raises(EmbeddingError):
        ground_chat_response(response("去哪散步"), search, model)
    model.generate_json.assert_not_called()
    search.search.side_effect = None
    search.search.return_value = SearchResult(items=[])
    result = ground_chat_response(response("去哪散步"), search, model)
    assert result.knowledge.status == "insufficient"
    assert "无法确认" in result.reply
    assert "知识库" not in result.reply and "资料" not in result.reply
    model.generate_json.assert_not_called()


"""换话题回归函数：新问题不带旧地点，证据不足的住宿提问会补问入住条件。"""


def test_new_topic_retrieval_and_lodging_clarification() -> None:
    from app.services.chat.rag import ground_chat_response

    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[])
    ground_chat_response(response("贵州有哪些网红打卡点？"), search, model,
                         history_context="杭州苏堤")
    assert "杭州" not in search.search.call_args.args[0]
    # 即使能找到片段，也不能在缺日期时让模型猜测符合预算的房源。
    search.search.return_value = SearchResult(items=[evidence()])
    result = ground_chat_response(response("杭州西湖附近有什么300元以下住宿？"), search, model,
                                  history_context="贵州网红打卡点")
    assert "贵州" not in search.search.call_args.args[0]
    assert "入住日期" in result.reply and "离店日期" in result.reply
    assert "经济型" not in result.reply
    model.generate_json.assert_not_called()


"""正文取材测试函数：景点问题补充介绍和门票检索，不让纯标题占用回答的证据名额。"""


def test_attraction_chat_uses_body_instead_of_heading() -> None:
    from app.services.chat.rag import ground_chat_response

    hit = evidence()
    heading = hit.model_copy(update={"chunk": hit.chunk.model_copy(update={
        "id": uuid4(), "text": "## 湖滨路步行街",
    })})
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[heading, hit])
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可步行游览。", "source_ids": [1]}],
    })
    ground_chat_response(response("杭州有哪些散步景点？"), search, model)
    assert "景点介绍 游览特色 门票" in search.search.call_args.args[0]
    sources = json.loads(model.generate_json.call_args.args[0][1]["content"])["sources"]
    assert len(sources) == 1 and sources[0]["text"] == hit.chunk.text


"""混合问题回归函数：登记需求和询问知识同时出现时，资料不足必须明确可见。"""


def test_mixed_planning_question_does_not_hide_insufficient() -> None:
    from app.services.chat.rag import ground_chat_response

    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[])
    original = response("杭州三天两人五千，西湖现在开放吗？").model_copy(update={
        "status": "complete", "reply": "旅行需求已整理完整。",
    })
    result = ground_chat_response(original, search, model)
    assert "旅行需求已整理完整" in result.reply
    assert "无法确认" in result.reply
    assert "知识库" not in result.reply and "资料" not in result.reply
    assert result.status == "complete"


"""上下文回归函数：连续短追问与混合登记的引用地点都保留，旧记录缺引用仍可读取。"""


def test_context_keeps_three_turns_and_mixed_sources() -> None:
    from app.schemas.document.answer import AnswerResult, AnswerSource
    from app.schemas.requirement.history import SavedRequirementTurn
    from app.services.chat.rag import build_chat_context

    hit = evidence()
    first = response("杭州散步地点有哪些？").model_copy(update={
        "status": "complete",
        "knowledge": AnswerResult(
            status="answered", points=[{"text": "可以步行游览。", "source_ids": [1]}],
            sources=[AnswerSource(id=1, hit=hit)],
        ),
    })
    turns = [SavedRequirementTurn(message_id=uuid4(), revision=i + 1, response=item)
             for i, item in enumerate([first, response("还有呢？"), response("分别在哪里？")])]
    context = build_chat_context(turns)
    assert "杭州散步地点" in context
    assert "湖滨路步行街" in context
    assert "还有呢" in context and "分别在哪里" in context
    # 新话题必须出现在检索可见的前180字，不能被旧长消息挤掉。
    switched = [
        SavedRequirementTurn(message_id=uuid4(), revision=1, response=response("杭州" * 500)),
        SavedRequirementTurn(message_id=uuid4(), revision=2, response=response("改问上海外滩")),
        SavedRequirementTurn(message_id=uuid4(), revision=3, response=response("那里怎么走？")),
    ]
    assert "上海外滩" in build_chat_context(switched)[:180]


"""网页追问回归函数：无本地引用时，仍保留上一轮景点卡片的顺序供指代理解。"""


def test_context_keeps_ordered_web_attractions() -> None:
    from app.schemas.document.answer import AnswerResult
    from app.schemas.requirement.history import SavedRequirementTurn
    from app.services.chat.rag import build_chat_context

    knowledge = AnswerResult.model_validate({
        "status": "answered", "points": [{"text": "适合步行。", "source_ids": [6]}],
        "sources": [], "attractions": [
            {"city": "杭州", "name": name, "source_ids": [6], "description": "可散步。",
             "reason": "适合步行。", "location": {"city": "杭州", "name": name,
                                                   "status": "unconfigured"}}
            for name in ["湖滨路步行街", "太子湾公园"]
        ],
    })
    turn = SavedRequirementTurn(message_id=uuid4(), revision=1,
                               response=response("杭州去哪玩").model_copy(
                                   update={"knowledge": knowledge}))
    context = build_chat_context([turn])
    assert "1.杭州/湖滨路步行街" in context
    assert "2.杭州/太子湾公园" in context


"""持久化测试函数：主聊天保存RAG引用，重试与刷新不再检索，失败不提交半轮。"""


def test_chat_citations_persist_and_retry_is_read_only(
    store_engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit = evidence()
    search = Mock()
    search.search.return_value = SearchResult(items=[hit])

    """检索替身函数：只替换外部检索，主接口和PostgreSQL存储走真实代码。"""

    def searches(request: object):
        yield search

    monkeypatch.setattr(routes, "get_search_service", searches)
    from app.schemas.document.answer import MapLookup, WebEvidence, WebSearchResult

    lookup = Mock(return_value=MapLookup(
        city="杭州", name="湖滨路步行街", status="unconfigured",
    ))
    monkeypatch.setattr(routes.AmapClient, "lookup", lookup)
    web_lookup = Mock(return_value=WebSearchResult(status="found", items=[WebEvidence(
        id=6, title="杭州步行介绍", url="https://www.hangzhou.gov.cn/notice",
        content="杭州湖滨路步行街适合步行游览。",
    )]))
    monkeypatch.setattr(routes.WebSearchClient, "search", web_lookup)
    card = {"city": "杭州", "name": "湖滨路步行街", "source_ids": [1, 6],
            "description": "湖边步行街，适合步行游览。", "reason": "符合散步需求。"}
    model = FakeModel([
        answer(intent="travel_info"),
        {"status": "answered", "points": [{"text": "可步行。", "source_ids": [1]}],
         "map_queries": [{"city": "杭州", "name": "湖滨路步行街", "source_id": 1}],
         "attractions": [card]},
        {"status": "answered", "points": [{"text": "适合步行游览。", "source_ids": [1]}],
         "attractions": [card]},
        answer(intent="travel_info"),
    ])
    session = TripService(store_engine).create_session("RAG验收")
    app = create_app(Settings(database_url=None))
    history_service = RequirementHistoryService(store_engine)
    app.dependency_overrides[routes.get_history_service] = lambda: history_service
    app.dependency_overrides[routes.get_saved_model] = lambda: model
    url = f"/api/v1/sessions/{session.id}/requirement-messages"
    payload = {"message": "杭州去哪散步？", "message_id": str(uuid4()), "expected_revision": 0}
    with TestClient(app) as client:
        saved = client.post(url, json=payload)
        assert saved.status_code == 200
        source = saved.json()["response"]["knowledge"]["sources"][0]
        assert source["hit"]["chunk"]["id"] == str(hit.chunk.id)
        assert client.post(url, json=payload).json() == saved.json()
        assert client.get(url).json()["turns"][0] == saved.json()
        assert search.search.call_count == 1
        assert saved.json()["response"]["knowledge"]["map_lookups"][0]["status"] == "unconfigured"
        lookup.assert_called_once_with("杭州", "湖滨路步行街")
        # 首次回答会按缺项补查；重发同条消息和读取历史都不能再次联网。
        assert web_lookup.call_count == 2
        assert web_lookup.call_args_list[1].args == ('杭州 "湖滨路步行街" 门票 详细地址',)
        knowledge = saved.json()["response"]["knowledge"]
        assert knowledge["web_search"]["supplemental_queries"] == [{
            "query": '杭州 "湖滨路步行街" 门票 详细地址', "status": "found",
        }]
        assert knowledge["attractions"][0]["name"] == "湖滨路步行街"
        assert knowledge["web_search"]["items"][0]["id"] == 6
        search.search.side_effect = EmbeddingError("检索不可用")
        failed = client.post(url, json={
            "message": "还有呢？", "message_id": str(uuid4()), "expected_revision": 1,
        })
        assert failed.status_code == 503
        assert client.get(url).json()["revision"] == 1
