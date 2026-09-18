"""测试层：规划尚无目的地时先引导，不把收集个人需求当作资料不足。"""

import json
from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.document.search import SearchResult
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.baidu_mcp import MCPTools
from app.services.chat.service import process_saved_message
from app.services.requirement.extract import build_result
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, RecordingModel, answer, understanding

MESSAGE = "想安排一个周末短途旅行，请先问问我的出发城市和偏好。"


"""分步补问测试函数：没有目的地时只问未知的出发城市和偏好，不一次索要全部条件。"""


@pytest.mark.parametrize("origin,interests,expected,absent", [
    (None, [], "出发", "总预算"),
    ("上海", [], "喜欢", "哪个城市出发"),
    (None, ["自然风景"], "出发", "喜欢"),
])
def test_undecided_destination_starts_with_origin_and_preferences(
    origin, interests, expected, absent,
):
    result = build_result(MESSAGE, date(2026, 9, 17),
        TravelRequestExtraction.model_validate(answer(origin=origin, interests=interests)))
    assert expected in result.clarification and absent not in result.clarification
    assert result.extraction.destination is None
    assert "destination" in result.missing_required_fields


"""保存接续测试函数：补问不进入知识库，下一轮出发地和偏好可保存且重试不重复调用。"""


@pytest.mark.parametrize("opening", [MESSAGE, "想安排周末短途旅行，可以先问我出发城市和偏好吗？"])
def test_guidance_saves_without_knowledge_service(store_engine, opening):
    trip = TripService(store_engine).create_session("周末引导", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    model = RecordingModel([json.dumps(answer()), json.dumps(answer(
        intent="modify_trip", origin="上海", interests=["自然风景"], days=2))])
    search = Mock()
    search.search.side_effect = AssertionError("纯补问不需要检索资料")
    maps = Mock(tools=MCPTools())
    for revision, message in enumerate([opening, "从上海出发，喜欢自然风景，玩两天"]):
        payload = SavedRequirementMessage(message=message, message_id=uuid4(),
                                          expected_revision=revision)
        turn = process_saved_message(trip.id, payload, model, service, "test",
                                     nullcontext(search), maps)
        assert turn.response.status == "needs_clarification"
        assert turn.response.knowledge is None
        assert "无法确认" not in turn.response.reply
        assert process_saved_message(trip.id, payload, model, service, "retry",
                                     nullcontext(search), maps) == turn
    assert turn.response.result.extraction.origin == "上海"
    assert turn.response.result.extraction.interests == ["自然风景"]
    assert turn.response.result.extraction.days == 2
    assert service.read(trip.id).revision == 2
    search.search.assert_not_called()


"""混合提问测试函数：收集需求时同时问外部事实，仍须检索而不能只返回补问。"""


def test_guidance_does_not_swallow_knowledge_question(store_engine):
    trip = TripService(store_engine).create_session("混合问题", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    model = RecordingModel([json.dumps(understanding(
        answer(), retrieval_query="西湖门票多少钱", query_cities=["杭州"], topic_action="set"))])
    search = Mock()
    search.search.return_value = SearchResult(items=[])
    payload = SavedRequirementMessage(message="想安排旅行，西湖门票多少钱？",
                                      message_id=uuid4(), expected_revision=0)
    turn = process_saved_message(trip.id, payload, model, service, "test",
                                 nullcontext(search), Mock(tools=MCPTools()))
    search.search.assert_called_once()
    assert turn.response.knowledge is None
    assert turn.response.reply == "这是测试回答。"
    assert turn.response.status == "needs_clarification"
