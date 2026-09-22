"""聊天编排测试层：验证摘要失败不阻断回答、摘要调用上限和比较查询的城市隔离。"""

import json
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.llm.client import ModelClientError
from app.schemas.document.search import SearchResult
from app.schemas.requirement.conversation import HistorySummary
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.chat.context import latest_summary
from app.services.chat.rag import ground_chat_response
from app.services.chat.service import process_saved_message
from tests.helpers import answer, understanding
from tests.test_chat_context import _turn
from tests.test_chat_rag import response

"""摘要编排测试函数：一次有界总结失败后继续回答，重试不会重新总结或改变旧原文。"""


@pytest.mark.parametrize("summary_failure", [False, True])
def test_summary_is_optional_and_once_per_committed_request(summary_failure):
    turns = [_turn(i, f"问题{i}", "已保存回答" * 800) for i in range(1, 9)]
    old_summary = HistorySummary(text="第1轮用户想去苏州", covered_revision=1)
    turns[0].response.history_summary = old_summary
    original = [turn.model_dump_json() for turn in turns]
    history = RequirementHistory(session_id=uuid4(), revision=8, turns=turns)
    service, model = Mock(), Mock()
    service.read.return_value = history
    service.read_plan.return_value = (0, None)

    """保存替身函数：只追加已完成的新轮次，不触碰已有快照。"""

    def append(session_id, message_id, revision, result):
        saved = SavedRequirementTurn(message_id=message_id, revision=revision + 1, response=result)
        history.turns.append(saved)
        history.revision += 1
        return saved

    service.append.side_effect = append
    model.generate_json.side_effect = [
        ModelClientError("摘要服务暂时失败") if summary_failure else json.dumps({"text": "新摘要"}),
        json.dumps(understanding(answer(intent="other"))),
    ]
    model.generate_text.return_value = "等于5。"
    payload = SavedRequirementMessage(message="2加3等于几", message_id=uuid4(), expected_revision=8)
    saved = process_saved_message(history.session_id, payload, model, service, "context-test",
                                  nullcontext(Mock()), Mock())
    assert saved.response.reply == "等于5。"
    assert model.generate_json.call_count == 2
    if summary_failure:
        assert saved.response.history_summary is None
        assert latest_summary(history.turns) == old_summary
    else:
        assert 1 < saved.response.history_summary.covered_revision < 8
    assert original == [turn.model_dump_json() for turn in history.turns[:8]]
    assert process_saved_message(history.session_id, payload, model, service, "retry",
                                 nullcontext(Mock()), Mock()) == saved
    assert model.generate_json.call_count == 2
    model.generate_text.assert_called_once()
    service.append.assert_called_once()


"""比较检索测试函数：本轮逐城检索，不用原旅行目的地把另一城市过滤掉。"""


def test_city_comparison_searches_each_current_city():
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[])
    model.generate_text.return_value = "苏州适合园林古街，杭州适合湖山风景。"
    ground_chat_response(response("哪个适合周末"), search, model, destination="上海",
                         query_cities=["苏州", "杭州"], retrieval_query="苏州和杭州哪个适合周末")
    cities = [call.kwargs["metadata"].city for call in search.search.call_args_list]
    assert cities == ["苏州", "杭州"]
    assert all("上海" not in call.args[0] for call in search.search.call_args_list)


"""解析失败测试函数：即使两次JSON都无效，仍检索原话并把实际资料交给回答模型。"""


def test_understanding_failure_still_retrieves_knowledge():
    from tests.helpers import evidence

    turns = [_turn(1, "想去杭州", "可以逛西湖")]
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=turns)
    service, model, search = Mock(), Mock(), Mock()
    service.read.return_value = history
    service.read_plan.return_value = (0, None)
    service.append.side_effect = lambda sid, mid, rev, response: SavedRequirementTurn(
        message_id=mid, revision=rev + 1, response=response)
    model.generate_json.return_value = "   "
    model.generate_text.return_value = "西湖可以散步。"
    search.search.return_value = SearchResult(items=[evidence()])
    payload = SavedRequirementMessage(message="有哪些热门景点？", message_id=uuid4(),
                                      expected_revision=1)
    result = process_saved_message(history.session_id, payload, model, service, "fallback-test",
                                  nullcontext(search), Mock())
    assert result.response.reply == "西湖可以散步。"
    assert search.search.called
    assert "想去杭州" in search.search.call_args.args[0]
    sent = model.generate_text.call_args.args[0]
    references = json.loads(sent[1]["content"].split("\n", 1)[1])
    assert references["sources"]
    assert sent[-1]["content"] == payload.message
    assert model.generate_json.call_count == 2


"""地图输出失败测试函数：工具选择无效也继续查资料，不要求用户修正提问。"""


@pytest.mark.parametrize("invalid_arguments", [False, True])
def test_invalid_map_choice_continues_retrieval(monkeypatch, invalid_arguments):
    from app.llm.client import ModelOutputError
    from tests.helpers import evidence

    turns = [_turn(1, "杭州灵隐寺附近吃什么", "可以查附近餐馆")]
    history = RequirementHistory(session_id=uuid4(), revision=1, turns=turns)
    service, model, search = Mock(), Mock(), Mock()
    service.read.return_value = history
    service.read_plan.return_value = (0, None)
    service.append.side_effect = lambda sid, mid, rev, response: SavedRequirementTurn(
        message_id=mid, revision=rev + 1, response=response)
    model.generate_json.return_value = json.dumps(understanding(
        answer(intent="travel_info"), response_mode="map", query_cities=["杭州"],
        retrieval_query="杭州灵隐寺附近餐馆名称"))
    model.generate_text.return_value = "可以参考这些餐馆，附近位置尚未核实。"
    search.search.return_value = SearchResult(items=[evidence()])
    monkeypatch.setattr('app.services.chat.service.handle_nearby',
                        Mock(side_effect=ValidationError.from_exception_data("地图参数", [])
                             if invalid_arguments else ModelOutputError("工具选择无效")))
    payload = SavedRequirementMessage(message="我需要具体的店名", message_id=uuid4(),
                                      expected_revision=1)
    result = process_saved_message(history.session_id, payload, model, service, "map-fallback",
                                  nullcontext(search), Mock())
    assert result.response.reply == model.generate_text.return_value
    assert search.search.called
    references = json.loads(model.generate_text.call_args.args[0][1]["content"].split("\n", 1)[1])
    assert references["sources"]


"""行程输出失败测试函数：明确说明草稿未完成，不用自由回答冒充规划成功。"""


@pytest.mark.parametrize("invalid_arguments", [False, True])
def test_invalid_plan_choice_gives_reference_reply(monkeypatch, invalid_arguments):
    from app.llm.client import ModelOutputError
    from tests.helpers import evidence

    history = RequirementHistory(session_id=uuid4(), revision=0, turns=[])
    service, model, search = Mock(), Mock(), Mock()
    service.read.return_value = history
    service.read_plan.return_value = (0, None)
    service.append.side_effect = lambda sid, mid, rev, response: SavedRequirementTurn(
        message_id=mid, revision=rev + 1, response=response)
    model.generate_json.return_value = json.dumps(understanding(
        answer(destination="杭州", days=2, travelers=2, total_budget="5000"),
        destination_action="set", response_mode="plan", query_cities=["杭州"],
        retrieval_query="杭州两天景点安排"))
    model.generate_text.return_value = "可以参考这些景点，行程尚未完成核对。"
    search.search.return_value = SearchResult(items=[evidence()])
    monkeypatch.setattr('app.services.chat.service.plan_trip',
                        Mock(side_effect=ValidationError.from_exception_data("规划参数", [])
                             if invalid_arguments else ModelOutputError("工具选择无效")))
    payload = SavedRequirementMessage(message="杭州两天两人5000元帮我规划", message_id=uuid4(),
                                      expected_revision=0)
    result = process_saved_message(history.session_id, payload, model, service, "plan-fallback",
                                  nullcontext(search), Mock())
    assert result.response.reply == model.generate_text.return_value
    assert result.response.status == "needs_clarification"
    assert result.response.result.extraction.destination == "杭州"
    assert result.response.itinerary is None
    search.search.assert_not_called()
    model.generate_text.assert_called_once()
