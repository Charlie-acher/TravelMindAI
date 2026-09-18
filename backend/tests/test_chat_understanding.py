"""测试层：一次理解区分旅行目的地、咨询话题和本轮独立检索范围。"""

import json
from datetime import date

import pytest

from app.schemas.requirement.base import TravelRequestExtraction
from app.services.requirement.extract import understand_turn
from tests.helpers import RecordingModel, answer

"""理解构造函数：分别指定目的地动作、话题动作和当前查询范围。"""


def interpretation(*, destination_action="keep", topic_action="set", cities=None,
                   mode="chat", **changes):
    cities = cities or []
    return {"requirement_update": answer(**changes), "destination_action": destination_action,
            "topic_action": topic_action, "query_cities": cities,
            "retrieval_query": "、".join(cities) + "热门景点" if cities else "",
            "conversation": {"topic_cities": cities, "topic_places": []},
            "response_mode": mode}


"""语义动作测试函数：咨询、改去、清空和闲聊分别产生正确的已提交状态。"""


@pytest.mark.parametrize("message,raw,destination,topics", [
    ("杭州有什么景点", interpretation(intent="travel_info", destination="杭州", cities=["杭州"]),
     "苏州", ["杭州"]),
    ("改去杭州", interpretation(intent="modify_trip", destination="杭州", cities=["杭州"],
                              destination_action="set"), "杭州", ["杭州"]),
    ("目的地还没定", interpretation(intent="modify_trip", destination_action="clear",
                                topic_action="clear"), None, []),
    ("讲个笑话", interpretation(intent="other", topic_action="keep"), "苏州", ["苏州"]),
    ("苏州和杭州有什么不同", interpretation(intent="travel_info", cities=["苏州", "杭州"]),
     "苏州", ["苏州", "杭州"]),
])
def test_understanding_separates_destination_and_topic(message, raw, destination, topics):
    from app.schemas.requirement.conversation import ConversationState

    model = RecordingModel([json.dumps(raw)])
    previous = TravelRequestExtraction.model_validate(answer(destination="苏州", days=2))
    history = [{"role": "user", "content": "想去苏州"},
               {"role": "assistant", "content": "园林与老街都可以看看。"}]
    result, understanding = understand_turn(message, model, reference_date=date(2026, 9, 17),
        previous=previous, conversation=ConversationState(topic_cities=["苏州"]),
        history_messages=history)
    assert result.extraction.destination == destination
    assert understanding.conversation.topic_cities == topics
    assert understanding.query_cities == raw["query_cities"]
    sent = model.calls[0]
    assert sent[-1] == {"role": "user", "content": message}
    assert sum(item["content"] == message for item in sent) == 1
    assert history[0] in sent and history[1] in sent
    assert sent.index(history[0]) < sent.index(history[1])


"""范围边界测试函数：一天游不支持结构化行程，但不能取消旅行知识检索。"""


def test_unsupported_plan_keeps_travel_retrieval():
    from app.schemas.requirement.conversation import ConversationState

    raw = interpretation(intent="other", cities=["杭州"], mode="plan")
    result, understood = understand_turn(
        "杭州一天怎么玩", RecordingModel([json.dumps(raw)]), reference_date=date(2026, 9, 17),
        previous=None, conversation=ConversationState(), history_messages=[],
    )
    assert result.message_intent == "other"
    assert understood.query_cities == ["杭州"]
    assert understood.retrieval_query
    assert understood.response_mode == "chat"


"""检索遗漏测试函数：已识别为旅行问答时，检索词缺失也不能直接退回纯模型。"""


@pytest.mark.parametrize("intent", ["travel_info", "trip_question"])
def test_travel_question_without_rewrite_still_searches(intent):
    from app.schemas.requirement.conversation import ConversationState

    raw = interpretation(intent=intent, cities=["杭州"])
    raw["retrieval_query"] = ""
    _, understood = understand_turn(
        "推荐热门景点", RecordingModel([json.dumps(raw)]), reference_date=date(2026, 9, 17),
        previous=None, conversation=ConversationState(), history_messages=[],
    )
    assert "杭州" in understood.retrieval_query
    assert "推荐热门景点" in understood.retrieval_query
