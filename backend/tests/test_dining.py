"""测试层：用百度MCP查询替身检查真实评分、城市和口味过滤。"""

from typing import Any

import pytest

from app.config import Settings
from app.schemas.document.answer import GeoPoint, MapLookup
from app.services.baidu import BaiduMaps
from app.services.baidu_mcp import MCPTools

"""锚点生成函数：提供已确认的百度地点和BD-09坐标。"""


def anchor() -> MapLookup:
    return MapLookup(city="杭州", name="云栖竹径", status="found", poi_id="A1",
                     provider="baidu", location=GeoPoint(
                         longitude=120.10, latitude=30.10, coordinate_system="BD-09"))


"""餐厅生成函数：评分、消费和距离沿用百度原始详情字段。"""


def restaurant(rating: object, **changes: Any) -> dict[str, Any]:
    return dict({"uid": "B1", "name": "真实火锅店", "city": "杭州市", "area": "西湖区",
                 "address": "梅灵南路10号", "location": {"lng": 120.1001, "lat": 30.1001},
                 "detail_info": {"type": "cater", "tag": "美食;火锅",
                                 "overall_rating": rating, "price": "88", "distance": 100}},
                **changes)


"""地图替身生成函数：只替换MCP查询，业务过滤仍运行真实服务。"""


def map_service(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> BaiduMaps:
    maps = BaiduMaps(Settings(baidu_map_api_key="test-secret"))
    maps._tools = MCPTools(status="ready")
    monkeypatch.setattr(maps, "query", lambda name, **arguments: body)
    return maps


"""评分边界测试函数：只有有限且至少四分的原始评分可以推荐。"""


@pytest.mark.parametrize("rating,status", [
    ("4", "found"), ("4.8", "found"), ("3.9", "empty"),
    (None, "ratings_unavailable"), ([], "ratings_unavailable"),
    ("NaN", "ratings_unavailable"), ("Infinity", "ratings_unavailable"),
    ("5.1", "ratings_unavailable"), (True, "ratings_unavailable"),
])
def test_real_rating_boundaries(
    monkeypatch: pytest.MonkeyPatch, rating: object, status: str,
) -> None:
    with map_service(monkeypatch, {"results": [restaurant(rating)]}) as maps:
        result = maps.nearby_places(anchor(), "火锅")
    assert result.status == status
    assert "test-secret" not in result.model_dump_json()
    if status == "found":
        assert result.items[0].rating == float(rating)
        assert result.items[0].reference_cost == "88"


"""城市距离测试函数：异地、超半径或非餐饮地点不能混入，结果最多五家。"""


def test_result_filter_sort_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    pois = [restaurant("4.1", uid=str(i), detail_info={
        "type": "cater", "overall_rating": "4.1", "distance": 100 + i}) for i in range(7)]
    pois += [restaurant("5", uid="foreign", city="上海市"),
             restaurant("5", uid="far", location={"lng": 121.1, "lat": 31.1}),
             restaurant("5", uid="hotel", detail_info={"type": "hotel", "overall_rating": "5"}),
             restaurant("4.9", uid="best")]
    with map_service(monkeypatch, {"results": pois}) as maps:
        result = maps.nearby_places(anchor(), "不限")
    assert [item.poi_id for item in result.items] == ["best", "0", "1", "2", "3"]


"""口味核对测试函数：高分店仍必须有真实名称或分类标签证明指定口味。"""


def test_preference_requires_real_poi_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    pois = [
        restaurant("5", uid="unrelated", name="一家素·蔬食料理",
                   detail_info={"type": "cater", "overall_rating": "5", "tag": "美食;素食"}),
        restaurant("4.9", uid="tag", name="真实甲店"),
        restaurant("4.8", uid="name", detail_info={"type": "cater", "overall_rating": "4.8"}),
        restaurant("4.6", uid="unknown", name="未知分类店",
                   detail_info={"type": "cater", "overall_rating": "4.6", "tag": []}),
    ]
    with map_service(monkeypatch, {"results": pois}) as maps:
        result = maps.nearby_places(anchor(), "火锅")
    assert [item.poi_id for item in result.items] == ["tag", "name"]


"""空口味结果测试函数：高评分不能让无关餐厅顶替指定口味。"""


def test_only_unrelated_restaurants_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    poi = restaurant("4.9", name="一家素·蔬食料理", detail_info={
        "type": "cater", "overall_rating": "4.9", "tag": "美食;素食"})
    with map_service(monkeypatch, {"results": [poi]}) as maps:
        result = maps.nearby_places(anchor(), "火锅")
    assert result.status == "empty"
    assert result.items == []
