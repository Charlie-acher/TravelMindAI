"""集成测试层：验证聊天实际进入图并原子保存，防止旧餐饮分支抢占规划。"""

import json
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.document.answer import GeoPoint, MapLookup
from app.schemas.document.search import SearchResult
from app.schemas.itinerary import UndoDraftRequest
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.baidu_mcp import MCPTools
from app.services.chat.service import process_saved_message
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, RecordingModel, answer, evidence, understanding
from tests.test_itinerary_agent import proposal

"""完整链路测试函数：含美食偏好仍生成行程，重试不调模型，修改只改变指定天。"""


def test_planning_food_preference_modify_undo_and_retry(store_engine):
    trip = TripService(store_engine).create_session("新建对话", user_id=TEST_USER_ID)
    history = RequirementHistoryService(store_engine)
    search, maps = Mock(), Mock(tools=MCPTools())
    hit = evidence()
    hit.chunk.text += "杭州河坊街适合散步。"
    search.search.return_value = SearchResult(items=[hit])
    maps.lookup.side_effect = lambda city, name: MapLookup(
        city=city, name=name, status="found", match_kind="poi", poi_id=name,
        location=GeoPoint(longitude=120, latitude=30))
    model = RecordingModel([json.dumps(item, ensure_ascii=False) for item in [
        answer(destination="杭州", days=2, travelers=2, total_budget="5000",
               interests=["美食", "小吃"]),
        {"tools": [{"tool": "knowledge_search", "query": "散步"}]},
        {"tools": [{"tool": "map_lookup", "name": "湖滨路步行街", "source_ids": ["k1"]},
                   {"tool": "map_lookup", "name": "河坊街", "source_ids": ["k1"]}]},
        {"days": [proposal(1, "p1").model_dump(), proposal(2, "p2").model_dump()]},
        understanding(answer(intent="modify_trip"), response_mode="plan"),
        {"days": [proposal(2, "p2", "10:00").model_dump()]},
    ]])
    payload = SavedRequirementMessage(message="安排杭州两天，两个人，预算5000元，喜欢美食和小吃",
                                      message_id=uuid4(), expected_revision=0)
    first = process_saved_message(trip.id, payload, model, history, "test",
                                  nullcontext(search), maps)
    assert first.response.itinerary.operation == "create"
    assert first.response.dining is None and not maps.nearby_places.called
    assert process_saved_message(trip.id, payload, model, history, "retry",
                                 nullcontext(search), maps) == first
    second = process_saved_message(trip.id, SavedRequirementMessage(
        message="第二天改为十点出发，第一天保持不变", message_id=uuid4(), expected_revision=1),
        model, history, "test", nullcontext(search), maps)
    assert second.response.itinerary.plan.days[0] == first.response.itinerary.plan.days[0]
    assert second.response.itinerary.plan.days[1].activities[0].start_time == "10:00"
    assert second.response.itinerary.changes == ["调整第2天的日期或活动安排"]
    undo = UndoDraftRequest(operation_id=uuid4(), target_message_id=second.message_id,
                           expected_revision=2, expected_itinerary_version=2)
    restored = history.undo(trip.id, undo, "undo")
    assert restored.response.itinerary.plan == first.response.itinerary.plan
    with pytest.raises(HistoryConflictError):
        process_saved_message(trip.id, SavedRequirementMessage(
            message=restored.response.result.original_message, message_id=undo.operation_id,
            expected_revision=2), model, history, "no-reuse", nullcontext(search), maps)


"""输出恢复测试函数：规划输出无效时仍给参考建议，但不保存未经核对的行程。"""


def test_agent_output_failure_saves_reference_reply_without_plan(store_engine):
    trip = TripService(store_engine).create_session("Agent失败回归", user_id=TEST_USER_ID)
    history = RequirementHistoryService(store_engine)
    model = RecordingModel([json.dumps(answer(destination="杭州", days=2, travelers=2,
                                              total_budget="5000"))])
    from langchain_core.messages import AIMessage

    from tests.helpers import ToolCallingModel
    from tests.test_itinerary_model import calls

    native = ToolCallingModel(messages=iter([
        calls(("knowledge_search", {"query": "杭州景点"})),
        AIMessage(content="中断", response_metadata={"finish_reason": "length"}),
    ]))
    client = Mock(generate_json=model.generate_json, model=native)
    client.generate_text.return_value = "杭州可先参考西湖和河坊街，行程尚未核对。"
    search = Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    saved = process_saved_message(trip.id, SavedRequirementMessage(
        message="杭州两天，两人，5000元", message_id=uuid4(), expected_revision=0),
        client, history, "failed-agent", nullcontext(search), Mock(tools=MCPTools()))
    assert search.search.call_count == 1
    assert saved.response.status == "needs_clarification"
    assert saved.response.reply == client.generate_text.return_value
    client.generate_text.assert_called_once()
    prompt = client.generate_text.call_args.args[0]
    assert "参考方案" in prompt[0]["content"]
    assert history.read(trip.id).revision == 1
    assert history.read_plan(trip.id) == (0, None)


"""资料缺口测试函数：缺城市资料时给文字方案，保留原条件和已有草稿。"""

def test_no_sources_returns_useful_outline_without_publishing(store_engine, monkeypatch):
    from app.services.chat import service
    from app.services.itinerary.graph import PlanResult

    trip = TripService(store_engine).create_session("缺资料", user_id=TEST_USER_ID)
    history = RequirementHistoryService(store_engine)
    model = RecordingModel([json.dumps(answer(destination="郑州", days=3, travelers=3,
        total_budget="5000", pace="relaxed"))])
    model.generate_text = Mock(return_value="先给你一份三天参考方案，地点和交通尚待核实。")
    monkeypatch.setattr(service, "plan_trip", lambda *a, **kw: PlanResult(None, "未找到郑州资料"))
    saved = process_saved_message(trip.id, SavedRequirementMessage(message="郑州三天三人5000元",
        message_id=uuid4(), expected_revision=0), model, history, "no-sources",
        nullcontext(Mock()), Mock(tools=MCPTools()))
    assert "参考方案" in saved.response.reply
    assert saved.response.result.extraction.destination == "郑州"
    assert saved.response.itinerary is None and history.read_plan(trip.id) == (0, None)
