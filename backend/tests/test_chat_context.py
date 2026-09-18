"""聊天上下文测试：验证历史裁剪、状态恢复和原文回查。"""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.conversation import (
    ConversationState,
    HistorySummary,
    TurnUnderstanding,
)
from app.schemas.requirement.history import SavedRequirementTurn
from app.schemas.requirement.update import RequirementUpdate
from app.services.chat.context import (
    build_history_messages,
    build_recall_messages,
    latest_conversation,
    recall_history,
    select_recent_turns,
)


def _update(destination: str | None = None) -> RequirementUpdate:
    return RequirementUpdate(
        intent="travel_info", destination=destination, origin=None, start_date=None,
        end_date=None, days=None, travelers=None, total_budget=None, pace=None,
        interests=[], dietary=[], lodging_preferences=[], hard_constraints=[],
        excluded_items=[], assumptions=[],
    )


def _turn(
    revision: int, message: str, reply: str, **response_changes: object,
) -> SavedRequirementTurn:
    update = _update(response_changes.pop("destination", None))
    extraction = TravelRequestExtraction.model_validate(
        update.model_dump(exclude={"clear_fields", "remove_items"})
    )
    response = RequirementChatResponse.model_validate({
        "result": {
            "original_message": message, "reference_date": date(2026, 9, 17),
            "extraction": extraction.model_dump(),
            "missing_required_fields": [], "clarification": None,
        },
        "reply": reply, "status": "knowledge", "changed_fields": [],
        "request_id": str(revision), **response_changes,
    })
    return SavedRequirementTurn(message_id=uuid4(), revision=revision, response=response)


def test_turn_understanding_has_bounded_shared_routing_fields() -> None:
    understanding = TurnUnderstanding(
        requirement_update=_update(), response_mode="map", query_cities=["苏州"],
        retrieval_query="苏州热门景点", conversation=ConversationState(topic_cities=["苏州"]),
    )
    assert understanding.response_mode == "map"
    assert understanding.destination_action == "keep"


def test_conversation_state_limits_each_city_and_place_string() -> None:
    with pytest.raises(ValidationError):
        ConversationState(topic_cities=["城" * 81])
    with pytest.raises(ValidationError):
        ConversationState(topic_places=["地点" * 101])


def test_history_keeps_roles_order_and_latest_whole_user() -> None:
    turns = [_turn(1, "想去苏州", "可以看看园林"), _turn(2, "继续", "甲" * 40)]
    messages = build_history_messages(turns, max_chars=18)
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "继续"
    assert "已截断" in messages[1]["content"]
    assert select_recent_turns(turns, max_chars=18) == [turns[-1]]


def test_history_keeps_visible_card_order_city_name_and_id() -> None:
    turn = _turn(1, "推荐景点", "可以看看这些地方")
    cards = [
        SimpleNamespace(city="苏州", name="拙政园", location=SimpleNamespace(poi_id="A1")),
        SimpleNamespace(city="苏州", name="平江路", location=SimpleNamespace(poi_id="A2")),
    ]
    response = turn.response.model_copy(update={"knowledge": SimpleNamespace(attractions=cards)})
    turn = turn.model_copy(update={"response": response})
    assistant = build_history_messages([turn])[1]["content"]
    assert assistant.index("1. 苏州｜拙政园（地点编号：A1）") < assistant.index(
        "2. 苏州｜平江路（地点编号：A2）"
    )


def test_history_keeps_dining_anchor_city_and_itinerary_places() -> None:
    turn = _turn(1, "安排一下", "已经整理")
    dining = SimpleNamespace(
        anchor=SimpleNamespace(city="苏州"),
        items=[SimpleNamespace(name="松鹤楼", poi_id="D1")],
    )
    activity = SimpleNamespace(place=SimpleNamespace(
        id="plan:A1", map=SimpleNamespace(city="苏州", name="拙政园", poi_id="A1")
    ))
    itinerary = SimpleNamespace(plan=SimpleNamespace(
        days=[SimpleNamespace(activities=[activity])]
    ))
    response = turn.response.model_copy(update={"dining": dining, "itinerary": itinerary})
    saved = turn.model_copy(update={"response": response})
    assistant = build_history_messages([saved])[1]["content"]
    assert "1. 苏州｜松鹤楼（地点编号：D1）" in assistant
    assert "2. 苏州｜拙政园（地点编号：A1）" in assistant


def test_latest_conversation_respects_explicit_empty_and_old_fallback() -> None:
    old = _turn(1, "想去苏州", "好", destination="苏州")
    cleared = _turn(2, "目的地未定", "好", conversation=ConversationState())
    assert latest_conversation([old, cleared]) == ConversationState()
    assert latest_conversation([old]) == ConversationState(topic_cities=["苏州"])


def test_recall_matches_real_turn_with_neighbors_and_deduplicates_recent() -> None:
    turns = [
        _turn(7, "虎丘怎么样", "有台阶"),
        _turn(8, "我不去虎丘，因为这次不想爬山", "已经排除虎丘"),
        _turn(9, "那看看园林", "可以"),
        _turn(10, "现在聊别的", "好"),
    ]
    recalled = recall_history(turns, "之前为什么排除虎丘")
    assert [turn.revision for turn in recalled] == [7, 8, 9]
    messages = build_recall_messages(recalled, "之前为什么排除虎丘", [turns[2]], None)
    assert "第8轮 用户原话" in messages[0]["content"]
    assert "第9轮" not in messages[0]["content"]


def test_recall_messages_use_summary_revision_and_do_not_copy_all_history() -> None:
    turns = [
        _turn(1, "聊聊天气", "晴天"),
        _turn(7, "虎丘怎么样", "有台阶"),
        _turn(8, "我不去虎丘，因为不想爬山", "已记录"),
        _turn(9, "看看园林", "可以"),
        _turn(10, "之前为什么排除虎丘", "待回答"),
    ]
    summary = HistorySummary(text="第8轮用户明确排除虎丘。", covered_revision=8)
    messages = build_recall_messages(turns, "之前为什么排除虎丘", [turns[-1]], summary)
    content = messages[0]["content"]
    assert "第1轮" not in content
    assert "第7轮" in content and "第8轮" in content and "第9轮" in content
    assert content.count("之前为什么排除虎丘") == 0
    assert len(content) <= 6200


def test_recall_filters_recent_before_matching_and_uses_relevant_summary_sentence() -> None:
    turns = [
        _turn(1, "天气如何", "晴"),
        _turn(8, "我不去虎丘，因为不想爬山", "已记录"),
        _turn(9, "虎丘还有什么", "台阶较多"),
        _turn(10, "虎丘门票呢", "待核实"),
        _turn(11, "虎丘地址呢", "待核实"),
    ]
    summary = HistorySummary(
        text="第1轮讨论天气。第8轮用户说明排除虎丘的原因。", covered_revision=8,
    )
    messages = build_recall_messages(turns, "为什么排除虎丘", turns[-3:], summary)
    content = messages[0]["content"]
    assert "第1轮" not in content
    assert "第8轮 用户原话" in content


def test_recall_keeps_keyword_fragment_from_oversized_turn() -> None:
    long_message = "甲" * 6500 + "我不去虎丘，因为不想爬山" + "乙" * 500
    turn = _turn(8, long_message, "已经记录这个原因")
    messages = build_recall_messages([turn], "为什么排除虎丘", [], None)
    content = messages[0]["content"]
    assert "第8轮 用户原话] [原文片段]" in content
    assert "虎丘，因为不想爬山" in content
    assert len(content) <= 6000


@pytest.mark.parametrize("summary", [
    None, HistorySummary(text="第1轮寒暄。", covered_revision=1),
])
def test_recall_explicit_revision_reads_history_not_yet_summarized(summary) -> None:
    turns = [
        _turn(1, "你好", "好"),
        _turn(2, "我不去虎丘，因为不想爬山", "已记录"),
        _turn(3, "后面聊别的", "好"),
    ]
    messages = build_recall_messages(turns, "第2轮我说了什么", turns[-1:], summary)
    assert messages
    assert "第2轮 用户原话" in messages[0]["content"]
    assert "我不去虎丘，因为不想爬山" in messages[0]["content"]
