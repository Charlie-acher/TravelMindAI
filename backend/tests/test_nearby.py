"""测试层：检查地图类别和价格筛选，不把未知消费当作便宜。"""

import json

# 原生工具调用回归：模型选择仍交给框架，业务只校验地点和过滤条件。
from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.schemas.dining import DiningItem, DiningResult
from app.schemas.requirement.base import RequirementResult, TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import SavedRequirementMessage, SavedRequirementTurn
from app.services.baidu_mcp import MCPTools
from app.services.chat.dining import dining_reply, handle_nearby
from app.services.chat.service import process_saved_message
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, ToolCallingModel, answer, understanding
from tests.test_dining import anchor

"""原生模型构造函数：按顺序返回标准工具调用。"""


def native(*actions):
    return ToolCallingModel(messages=iter([AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": str(uuid4())}],
        response_metadata={"finish_reason": "tool_calls"}) for name, args in actions]))


"""历史构造函数：生成可序列化和恢复的完整会话轮次。"""


def saved(result, message="这附近有什么饭店？"):
    return SavedRequirementTurn(message_id=uuid4(), revision=1,
        created_at="2026-09-16T00:00:00Z", response=RequirementChatResponse(
        result=RequirementResult(original_message=message, reference_date=date(2026,9,16),
            extraction=TravelRequestExtraction(**answer(intent="travel_info")),
            missing_required_fields=[], clarification=None, message_intent="travel_info"),
        reply=dining_reply(result), status="knowledge", changed_fields=[], request_id="test",
        dining=result))


"""价格追问测试函数：复用锚点和口味，刷新后仍保留消费上限。"""


def test_native_price_followup_and_persisted_query():
    previous = DiningResult(status="found", anchor=anchor(), preference="火锅")
    model = native(("search_nearby", {"category": "dining", "max_cost": 100}))
    maps = Mock(tools=MCPTools())
    maps.nearby_places.return_value = previous.model_copy(update={"max_cost":100})
    result = handle_nearby("人均控制在100以下吧", None, [saved(previous)], maps, model)
    maps.nearby_places.assert_called_once_with(previous.anchor, "火锅", 2000, "dining", 100)
    assert result.max_cost == 100
    assert "人均控制在100以下吧" in model.seen_messages[0][-1].content
    restored = SavedRequirementTurn.model_validate_json(saved(result).model_dump_json())
    maps.reset_mock()
    handle_nearby("改成家常菜", None, [restored], maps,
                  native(("search_nearby", {"category":"dining", "preference":"家常菜"})))
    maps.nearby_places.assert_called_once_with(previous.anchor, "家常菜", 2000, "dining", 100)


"""类别切换测试函数：餐馆与酒店可互相作为锚点，筛选不串用。"""


def test_restaurant_to_hotel_to_restaurant_keeps_selected_location():
    item = DiningItem(poi_id="restaurant1", name="真实餐馆", location=anchor().location, rating=4.5)
    previous = DiningResult(status="found", provider="baidu", anchor=anchor(), items=[item],
                            max_cost=100, preference="火锅")
    maps = Mock(tools=MCPTools())
    maps.nearby_places.side_effect = lambda a,p,r,c,m: DiningResult(
        status="found", provider="baidu", anchor=a, category=c, preference=p, max_cost=m,
        items=[item.model_copy(update={"poi_id":"hotel1", "name":"湖边酒店"})])
    result = handle_nearby("第一家饭店附近有什么住宿", None, [saved(previous)], maps,
        native(("search_nearby", {"category":"lodging", "anchor_id":"baidu:restaurant1"})))
    args = maps.nearby_places.call_args.args
    assert args[0].name == "真实餐馆" and args[1:] == (None, 2000, "lodging", None)
    assert "不代表每晚房价" in dining_reply(result)
    handle_nearby("这家酒店附近吃点什么", None, [saved(result)], maps,
        native(("search_nearby", {"category":"dining", "anchor_id":"baidu:hotel1"})))
    assert maps.nearby_places.call_args.args[0].name == "湖边酒店"


"""分流测试函数：未知住宿先问地点，完整行程继续原规划。"""


def test_unknown_hotel_clarifies_and_planning_continues():
    maps = Mock(tools=MCPTools())
    response = handle_nearby("住宿附近有什么饭店", None, [], maps,
        native(("ask_location", {"question":"你住哪家酒店、在哪个城市呀？"})))
    assert response.status == "needs_clarification"
    assert not maps.nearby_places.called
    assert handle_nearby("杭州两天两人五千元喜欢火锅", None, [], maps,
                        native(("continue_chat", {}))) is None


"""工具纠错测试函数：错误编号通过标准ToolMessage反馈给模型。"""


def test_unknown_id_can_recover_through_native_tool_error():
    maps = Mock(tools=MCPTools())
    model = native(("search_nearby", {"category":"lodging", "anchor_id":"invented"}),
                   ("ask_location", {"question":"具体是哪家饭店附近呢？", "category":"lodging"}))
    result = handle_nearby("饭店附近住宿", None, [], maps, model)
    assert result.status == "needs_clarification"
    assert not maps.nearby_places.called
    assert any(m.type == "tool" and m.status == "error" for m in model.seen_messages[1])


"""持久化测试函数：餐费不进入旅行需求抽取，重试不重复查图。"""


def test_nearby_precedes_requirement_extraction_and_survives_refresh(store_engine):
    trip = TripService(store_engine).create_session("周边连续查询", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    first = saved(DiningResult(status="found", anchor=anchor()))
    service.append(trip.id, first.message_id, 0, first.response)
    model = Mock(model=native(("search_nearby", {"category":"dining", "max_cost":100})))
    model.generate_json.return_value = json.dumps(understanding(
        answer(intent="travel_info"), response_mode="map", query_cities=["杭州"],
        retrieval_query="杭州附近人均100元以下餐馆"))
    maps = Mock(tools=MCPTools())
    maps.nearby_places.side_effect = lambda a,p,r,c,m: DiningResult(
        status="found", anchor=a, max_cost=m, category=c)
    payload = SavedRequirementMessage(message="人均控制在100以下吧", message_id=uuid4(),
                                      expected_revision=1)
    response = process_saved_message(trip.id, payload, model, service, "test",
                                     nullcontext(Mock()), maps)
    model.generate_json.assert_called_once()
    assert response.response.result.extraction.total_budget is None
    assert service.read(trip.id).turns[-1].response.dining.max_cost == 100
    assert response.response.changed_fields == []
    assert process_saved_message(trip.id, payload, model, service, "retry",
                                 nullcontext(Mock()), maps) == response
    assert maps.nearby_places.call_count == 1


"""补问连续性测试函数：口味补问保留消费、半径与百度来源，不改变推荐序号。"""


def test_clarification_retains_query_and_provider():
    item = DiningItem(poi_id="B1", name="餐馆一", location=anchor().location, rating=4)
    previous = DiningResult(status="found", anchor=anchor(), items=[item],
                            max_cost=100, radius_m=500, preference="火锅", provider="baidu")
    model = native(("ask_location", {"question":"那你想吃什么？", "keep_query":True}))
    maps = Mock(tools=MCPTools())
    result = handle_nearby("不吃火锅", None, [saved(previous)], maps, model)
    import json
    context = json.loads(model.seen_messages[0][-1].content)
    assert context["recent_turns"][0]["places_in_display_order"][0]["name"] == "餐馆一"
    maps.nearby_places.return_value = previous
    handle_nearby("家常菜", None, [saved(result)], maps,
                  native(("search_nearby", {"category":"dining", "preference":"家常菜"})))
    maps.nearby_places.assert_called_once_with(previous.anchor, "家常菜", 500, "dining", 100)
