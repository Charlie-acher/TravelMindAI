"""测试层：复现饮品混入餐馆、特色追问丢失知识和菜名错误定位。"""

import json
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.dining import DiningResult
from app.schemas.document.search import SearchResult
from app.schemas.requirement.history import SavedRequirementMessage
from app.services.baidu_mcp import MCPTools
from app.services.chat.dining import handle_nearby
from app.services.chat.service import process_saved_message
from app.services.document.answer import answer_from_sources
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID, answer, evidence, understanding
from tests.test_dining import anchor, map_service, restaurant
from tests.test_nearby import native, saved

"""饮品排除测试函数：分类或品牌确认的饮品店不能靠高评分进入餐馆列表。"""


@pytest.mark.parametrize("name,tag", [
    ("库迪咖啡(湖滨店)", "美食"), ("瑞幸咖啡", "美食"), ("沪上阿姨", "美食"),
    ("某某小铺", "美食;茶饮店"), ("某某小铺", "美食;咖啡厅"),
    ("某某小铺", "美食;甜品饮品"), ("蜜雪冰城", "美食"),
])
def test_drinks_excluded_from_dining(monkeypatch, name, tag):
    drink = restaurant("5", name=name, detail_info={
        "type": "cater", "tag": tag, "overall_rating": "5", "price": "8"})
    meal = restaurant("4.5", uid="meal", name="杭帮菜餐厅")
    with map_service(monkeypatch, {"results": [drink, meal]}) as maps:
        result = maps.nearby_places(anchor(), max_cost=100)
    assert [item.poi_id for item in result.items] == ["meal"]


"""菜品文字测试函数：即使模型误填菜品草稿，也不能按菜名查地图或生成地点卡。"""


def test_food_text_does_not_geocode_dishes():
    hit = evidence()
    hit.chunk.text = "杭州特色菜有东坡肉，酥而不烂。"
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可以试试东坡肉。", "source_ids": [1]}],
        "attractions": [{"city": "杭州", "name": "东坡肉", "description": "酥而不烂。",
                         "reason": "杭州特色菜。", "source_ids": [1]}],
    })
    maps = Mock()
    result = answer_from_sources("杭州特色菜", [hit], model, maps=maps, text_only=True)
    assert not result.attractions and not result.map_lookups
    assert any("酥而不烂" in point.text for point in result.points)
    maps.lookup.assert_not_called()


"""特色接续测试函数：含糊追问兼顾周边与全城，明确全城提问不沿用旧预算。"""


@pytest.mark.parametrize("nearby", [True, False])
def test_local_food_scope_keeps_only_relevant_filters(nearby):
    previous = DiningResult(status="found", anchor=anchor(), provider="baidu",
                            max_cost=100, radius_m=500, preference="火锅")
    maps = Mock(tools=MCPTools())
    maps.nearby_places.return_value = previous.model_copy(update={"status": "empty", "items": []})
    result = handle_nearby("想吃当地特色，有推荐吗？", None, [saved(previous)], maps,
        native(("local_food", {"city": "杭州", "cuisine": "杭帮菜", "include_nearby": nearby})))
    assert result.city == "杭州"
    if nearby:
        maps.nearby_places.assert_called_once_with(previous.anchor, "杭帮菜", 500, "dining", 100)
        assert result.nearby is not None
    else:
        maps.nearby_places.assert_not_called()
        assert result.nearby is None


"""跨城测试函数：城市特色切换不得把原城市的周边查询混入新回答。"""


def test_new_city_food_does_not_reuse_old_anchor():
    maps = Mock(tools=MCPTools())
    previous = DiningResult(status="found", anchor=anchor(), provider="baidu", max_cost=100)
    result = handle_nearby("成都有什么特色菜？", None, [saved(previous)], maps,
        native(("local_food", {"city": "成都", "cuisine": "川菜", "include_nearby": True})))
    assert result.city == "成都" and result.nearby is None
    maps.nearby_places.assert_not_called()


"""新地点测试函数：首轮说出具体地点时也能兼顾当地菜查询与城市介绍。"""


def test_local_food_with_new_explicit_place():
    maps = Mock(tools=MCPTools())
    maps.lookup.return_value = anchor()
    maps.nearby_places.return_value = DiningResult(status="empty", anchor=anchor())
    result = handle_nearby("杭州云栖竹径附近有什么当地菜？", None, [], maps,
        native(("local_food", {"city": "杭州", "cuisine": "杭帮菜", "include_nearby": True,
                               "place_name": "云栖竹径"})))
    maps.lookup.assert_called_once_with("杭州", "云栖竹径")
    assert result.nearby is not None


"""组合落库测试函数：附近筛空仍有知识回答，保存后重试不重复查询、不改全团预算。"""


def test_local_food_combines_knowledge_and_nearby_atomically(store_engine):
    trip = TripService(store_engine).create_session("特色接续", user_id=TEST_USER_ID)
    service = RequirementHistoryService(store_engine)
    previous = DiningResult(status="found", anchor=anchor(), provider="baidu", max_cost=100)
    first = saved(previous)
    service.append(trip.id, first.message_id, 0, first.response)
    maps = Mock(tools=MCPTools())
    maps.nearby_places.return_value = previous.model_copy(update={"status": "empty"})
    hit = evidence()
    hit.file_name = "杭州-餐馆.md"
    hit.chunk.text = "杭州的东坡肉酥而不烂。杭州甲餐馆位于甲路10号。"
    search = Mock()
    search.search.return_value = SearchResult(items=[hit])
    model = Mock(model=native(("local_food", {
        "city": "杭州", "cuisine": "杭帮菜", "include_nearby": True})))
    model.generate_json.side_effect = [json.dumps(understanding(
        answer(intent="travel_info"), response_mode="map", query_cities=["杭州"],
        retrieval_query="杭州当地特色美食")), json.dumps({"status": "answered", "points": [
        {"text": "杭州可以尝尝东坡肉。甲餐馆位于甲路10号。", "source_ids": [1]}]})]
    payload = SavedRequirementMessage(message="想吃当地特色，有推荐吗？", message_id=uuid4(),
                                      expected_revision=1)
    turn = process_saved_message(
        trip.id, payload, model, service, "test", nullcontext(search), maps)
    response = turn.response
    assert all(text in response.reply for text in ("未查到", "东坡肉", "甲路10号"))
    assert response.dining.max_cost == 100 and response.knowledge.sources
    assert not response.knowledge.attractions and response.result.extraction.total_budget is None
    assert "杭州" in search.search.call_args.args[0] and "餐馆" in search.search.call_args.args[0]
    assert service.read(trip.id).turns[-1] == turn
    assert process_saved_message(
        trip.id, payload, model, service, "retry", nullcontext(search), maps) == turn
    assert search.search.call_count == maps.nearby_places.call_count == 1
