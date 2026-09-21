"""测试层：检查百度MCP地点核对、商户过滤、详情补查及历史坐标边界。"""

from typing import Any, Literal
from unittest.mock import Mock

import pytest
from langchain_core.tools import ToolException

from app.config import Settings
from app.schemas.document.answer import GeoPoint
from app.services.baidu import BaiduMaps
from app.services.baidu_mcp import MCPTools
from tests.test_dining import anchor, map_service, restaurant

"""类别查询测试函数：餐饮和住宿分别查询，业务坐标明确为BD-09。"""


@pytest.mark.parametrize("category,kind,label", [
    ("dining", "cater", "餐厅"), ("lodging", "hotel", "酒店"),
])
def test_request_and_category(monkeypatch: pytest.MonkeyPatch,
                              category: Literal["dining", "lodging"], kind: str,
                              label: str) -> None:
    poi = restaurant("4.8", detail_info={"type": kind, "tag": label,
                     "overall_rating": 4.8, "price": "88", "distance": 100})
    with map_service(monkeypatch, {"results": [poi]}) as maps:
        query = Mock(return_value={"results": [poi]})
        monkeypatch.setattr(maps, "query", query)
        result = maps.nearby_places(anchor(), category=category, max_cost=100)
    query.assert_called_once_with("map_search_places", region="杭州", query=label,
                                  tag=label, location="30.1,120.1", radius=2000)
    assert result.status == "found"
    assert result.provider == "baidu"
    assert result.category == category
    assert result.max_cost == 100
    assert result.items[0].reference_cost == "88"
    assert result.items[0].location.coordinate_system == "BD-09"
    assert "test-secret" not in result.model_dump_json()


"""数值边界测试函数：无效评分不推荐，零价和未知价格不能满足消费上限。"""


@pytest.mark.parametrize("field,value,max_cost,status", [
    ("overall_rating", 5, None, "found"), ("overall_rating", "-1", None, "ratings_unavailable"),
    *[("price", value, 100, "empty")
      for value in (None, [], True, "0", 0, "NaN", "Infinity", "-1", "101")],
    ("price", "100", 100, "found"), ("price", None, None, "found"),
    ("distance", 2001, None, "empty"), ("distance", True, None, "empty"),
    ("distance", "Infinity", None, "empty"), ("distance", -1, None, "empty"),
])
def test_numeric_boundaries(monkeypatch: pytest.MonkeyPatch, field: str,
                            value: object, max_cost: float | None, status: str) -> None:
    poi = restaurant("4.8")
    poi["detail_info"][field] = value
    with map_service(monkeypatch, {"results": [poi]}) as maps:
        result = maps.nearby_places(anchor(), max_cost=max_cost)
    assert result.status == status


"""地点边界测试函数：异地、越界、缺少类别及伪造口味证据均不能进入推荐。"""


@pytest.mark.parametrize("changes", [
    {"city": "上海市"}, {"location": {"lng": 121.1, "lat": 30.1}},
    {"location": {"lng": True, "lat": 30.1}}, {"location": {"lng": "NaN", "lat": 30.1}},
    {"uid": ""}, {"name": " "}, {"detail_info": []},
    {"detail_info": {"type": "hotel", "tag": "美食", "overall_rating": "4.8"}},
    {"name": "川菜馆", "detail_info": {"type": "cater", "tag": "美食", "overall_rating": "4.8"}},
])
def test_place_boundaries(monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]) -> None:
    with map_service(monkeypatch, {"results": [restaurant("4.8", **changes)]}) as maps:
        result = maps.nearby_places(anchor(), preference="火锅")
    assert result.status == "empty"
    assert not result.items


"""排序去重测试函数：只返回五个不同商户，按真实评分及原始距离排列。"""


def test_unique_top_five(monkeypatch: pytest.MonkeyPatch) -> None:
    pois = [restaurant("4.8", uid=str(index), detail_info={
        "type": "cater", "overall_rating": "4.8", "distance": 110 - index}) for index in range(7)]
    with map_service(monkeypatch, {"results": pois + pois}) as maps:
        result = maps.nearby_places(anchor())
    assert [item.poi_id for item in result.items] == ["6", "5", "4", "3", "2"]


"""地点匹配测试函数：唯一同名同城或同区结果才提供坐标，不接受名称近似。"""


@pytest.mark.parametrize("city,changes,expected", [
    ("杭州", {}, "found"), ("西湖区", {}, "found"),
    ("杭州", {"city": "上海市"}, "no_match"),
    ("淳安县", {}, "no_match"),
    ("杭州", {"name": "云栖竹径停车场"}, "no_match"),
    ("杭州", {"name": "云栖竹径景区"}, "found"),
    ("杭州", {"uid": ""}, "no_match"),
    ("杭州", {"location": None}, "no_match"),
    ("杭州", {"city": None, "area": None}, "no_match"),
])
def test_lookup_requires_exact_name_and_region(
    monkeypatch: pytest.MonkeyPatch, city: str, changes: dict[str, Any], expected: str,
) -> None:
    poi = restaurant("4.8", name="云栖竹径")
    poi.update(changes)
    with map_service(monkeypatch, {"results": [poi]}) as maps:
        result = maps.lookup(city, "云栖竹径")
    assert result.status == expected
    assert result.provider == "baidu"
    if expected == "found":
        assert result.poi_id == "B1"
        assert result.location.coordinate_system == "BD-09"
        assert result.address == "梅灵南路10号"
    else:
        assert result.location is None
        assert result.address is None


"""同名优先测试函数：原名精确命中优先于别名，多个同名主体仍须补问。"""

@pytest.mark.parametrize("duplicate,expected", [(False, "found"), (True, "ambiguous")])
def test_lookup_exact_name_before_alias(monkeypatch: pytest.MonkeyPatch,
                                       duplicate: bool, expected: str) -> None:
    exact = restaurant("4.8", uid="exact", name="花港观鱼",
                       detail_info={"tag": "旅游景点"})
    alias = restaurant("4.8", uid="alias", name="花港观鱼" if duplicate else "花港公园",
                       detail_info={"tag": "旅游景点;公园", "new_alias": "花港观鱼"})
    with map_service(monkeypatch, {"results": [alias, exact]}) as maps:
        result = maps.lookup("杭州", "花港观鱼")
    assert result.status == expected
    assert result.poi_id == ("exact" if not duplicate else None)


"""唯一性测试函数：同名地点有歧义或结果为空时，不借用任何候选坐标。"""


@pytest.mark.parametrize("duplicate,status", [(True, "ambiguous"), (False, "no_match")])
def test_lookup_requires_unique_result(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool, status: str,
) -> None:
    pois = [restaurant("4.8", uid=uid, name="云栖竹径") for uid in ("A1", "A2")]
    with map_service(monkeypatch, {"results": pois if duplicate else []}) as maps:
        result = maps.lookup("杭州", "云栖竹径")
    assert result.status == status
    assert result.location is None
    assert result.entrance is None
    assert result.address is None


"""景区主体测试函数：公交站同名不制造歧义，严格城市前缀可识别官方主体。"""


@pytest.mark.parametrize("city,name,subject_name,subject_address", [
    ("杭州", "西湖", "西湖风景区", "杭州市西湖区龙井路1号"),
    ("长沙", "岳麓山", "岳麓山国家重点风景名胜区", "湖南省长沙市岳麓区登高路58号"),
    ("苏州", "同里国家湿地公园", "江苏同里国家湿地公园", "江苏省苏州市吴江区同里镇肖甸湖村"),
])
def test_lookup_prefers_place_subject_over_same_named_bus_stop(
    monkeypatch: pytest.MonkeyPatch, city: str, name: str,
    subject_name: str, subject_address: str,
) -> None:
    subject = restaurant("4.8", uid="subject", name=subject_name, address=subject_address,
                         city=f"{city}市", detail_info={"type": "scope", "tag": "旅游景点;公园"})
    bus = restaurant("4.8", uid="bus", name=name, address="7107路;7107路高峰支线",
                     city=f"{city}市", detail_info={"type": None, "tag": "交通设施;公交车站"})
    with map_service(monkeypatch, {"results": [subject, bus]}) as maps:
        result = maps.lookup(city, name)
    assert result.status == "found"
    assert result.poi_id == "subject"
    assert result.matched_name == subject_name
    assert result.address == subject_address


"""国家景区后缀测试函数：只认完整主体，不能将入口、停车场或周边设施并成景区。"""

@pytest.mark.parametrize("name", ["岳麓山国家重点风景名胜区-东门",
                                  "岳麓山国家重点风景名胜区停车场", "岳麓山旅游码头"])
def test_national_scenic_subject_does_not_match_facilities(name):
    assert not BaiduMaps._same_name({"name": name}, "岳麓山", "长沙")


"""严格后缀测试函数：景区附属站点不能因名称包含主体而冒充景区。"""


def test_lookup_rejects_scenic_suffix_when_not_at_strict_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    station = restaurant(
        "4.8", name="西湖风景区-总站", address="公交线路",
        detail_info={"type": None, "tag": "交通设施;公交车站"},
    )
    with map_service(monkeypatch, {"results": [station]}) as maps:
        result = maps.lookup("杭州", "西湖")
    assert result.status == "no_match"


"""参考价测试函数：地图消费不等于门票，零值或非法值也不能推断免费。"""


@pytest.mark.parametrize("price,expected", [("8", "8"), ("0", None), ([], None), (None, None)])
def test_lookup_price_does_not_become_ticket(
    monkeypatch: pytest.MonkeyPatch, price: object, expected: str | None,
) -> None:
    poi = restaurant("4.8", name="云栖竹径")
    poi["detail_info"]["price"] = price
    with map_service(monkeypatch, {"results": [poi]}) as maps:
        result = maps.lookup("杭州", "云栖竹径")
    assert result.status == "found"
    assert result.reference_cost == expected
    assert "ticket_price" not in result.model_dump()
    assert result.checked_at is not None


"""失败隔离测试函数：配置或连接不可用不执行查询，业务错误不泄露密钥及坐标。"""


@pytest.mark.parametrize("operation", ["lookup", "nearby_places"])
@pytest.mark.parametrize("mode,expected", [
    ("unconfigured", "unconfigured"), ("connection", "error"),
    ("query_error", "error"), ("bad_json", "error"), ("bad_results", "error"),
    ("empty", "empty"),
])
def test_failure_states(monkeypatch: pytest.MonkeyPatch, operation: str,
                         mode: str, expected: str) -> None:
    with map_service(monkeypatch, {"results": []}) as maps:
        query = Mock(return_value={"results": {} if mode == "bad_results" else []})
        if mode in ("unconfigured", "connection"):
            maps._tools = MCPTools(status="unconfigured" if mode == "unconfigured" else "error")
        elif mode == "query_error":
            query.side_effect = ToolException("test-secret")
        elif mode == "bad_json":
            query.side_effect = ValueError("test-secret")
        monkeypatch.setattr(maps, "query", query)
        result = (maps.lookup("杭州", "云栖竹径") if operation == "lookup"
                  else maps.nearby_places(anchor()))
    assert result.status == ("no_match" if mode == "empty" and operation == "lookup" else expected)
    assert "test-secret" not in result.model_dump_json()
    if operation == "lookup":
        assert result.location is None
    else:
        assert not result.items
    if mode in ("unconfigured", "connection"):
        query.assert_not_called()


"""无配置测试函数：真实客户端没有密钥时不会构造外部适配器。"""


def test_without_key_never_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    with BaiduMaps(Settings(baidu_map_api_key=None)) as maps:
        adapter = Mock(side_effect=AssertionError("无密钥不应连接"))
        monkeypatch.setattr(maps.client, "_adapter", adapter)
        assert maps.lookup("杭州", "云栖竹径").status == "unconfigured"
        assert maps.nearby_places(anchor()).status == "unconfigured"
    adapter.assert_not_called()


"""历史坐标测试函数：旧来源或GCJ-02位置必须重新查百度，历史对象保持原值。"""


@pytest.mark.parametrize("provider,coordinate", [("amap", "GCJ-02"), ("baidu", "GCJ-02"),
                                                  ("amap", "BD-09")])
def test_historical_anchor_is_rematched(
    monkeypatch: pytest.MonkeyPatch, provider: str, coordinate: str,
) -> None:
    old = anchor().model_copy(update={"provider": provider, "poi_id": "old-id", "location":
        GeoPoint(longitude=121, latitude=31, coordinate_system=coordinate)})
    before = old.model_dump()
    with map_service(monkeypatch, {"results": [restaurant("4.8")]}) as maps:
        lookup = Mock(return_value=anchor())
        monkeypatch.setattr(maps, "lookup", lookup)
        result = maps.nearby_places(old)
    lookup.assert_called_once_with("杭州", "云栖竹径")
    assert result.status == "found"
    assert result.anchor.provider == "baidu"
    assert result.anchor.poi_id == "A1"
    assert result.anchor.location.coordinate_system == "BD-09"
    assert old.model_dump() == before


"""历史匹配失败测试函数：无法唯一定位时停止周边查询，不沿用旧坐标。"""


@pytest.mark.parametrize("status", ["no_match", "ambiguous", "error"])
def test_failed_historical_lookup_stops_nearby(
    monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    old = anchor().model_copy(update={"provider": "amap"})
    with map_service(monkeypatch, {}) as maps:
        monkeypatch.setattr(maps, "lookup", Mock(return_value=anchor().model_copy(
            update={"status": status, "location": None, "poi_id": None})))
        query = Mock(side_effect=AssertionError("定位失败不应查周边"))
        monkeypatch.setattr(maps, "query", query)
        result = maps.nearby_places(old)
    assert result.status == "needs_clarification"
    assert not result.items
    assert result.anchor.location is None
    query.assert_not_called()


"""详情补查测试函数：缺少评分时只查前十项，且详情必须属于原始uid。"""


@pytest.mark.parametrize("matching_uid", [True, False])
def test_detail_enrichment_uses_original_uid_and_first_ten(
    monkeypatch: pytest.MonkeyPatch, matching_uid: bool,
) -> None:
    pois = [restaurant(None, uid=str(i), detail_info={"type": "cater"}) for i in range(11)]
    calls = []

    """查询替身函数：检索缺评分，详情返回可追溯或故意不匹配的uid。"""

    def query(name: str, **arguments: Any) -> dict[str, Any]:
        if name == "map_search_places":
            return {"results": pois}
        assert name == "map_place_details"
        calls.append(arguments["uid"])
        return {"result": restaurant("4.8", uid=arguments["uid"] if matching_uid else "wrong")}

    with map_service(monkeypatch, {}) as maps:
        monkeypatch.setattr(maps, "query", query)
        result = maps.nearby_places(anchor())
    assert calls == [str(i) for i in range(10)]
    assert result.status == ("found" if matching_uid else "ratings_unavailable")
    assert result.rating_missing_count == (1 if matching_uid else 11)
    assert {item.poi_id for item in result.items} <= {str(i) for i in range(10)}


"""环境别名测试函数：支持明确的百度变量名，并保持密钥显示脱敏。"""


def test_baidu_setting_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAIDU_MAP_API_KEY", "test-secret")
    settings = Settings()
    assert settings.baidu_map_api_key is not None
    assert settings.baidu_map_api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(settings)


"""提供方别名测试函数：只接受百度明确返回的完整别名，仍核对城市与唯一性。"""


@pytest.mark.parametrize("mode,status", [("alias", "found"), ("partial", "no_match"),
                                          ("foreign", "no_match"), ("duplicate", "ambiguous")])
def test_lookup_provider_alias_requires_same_city_and_unique_match(
    monkeypatch: pytest.MonkeyPatch, mode: str, status: str,
) -> None:
    poi = restaurant("4.8", name="湖滨路步行街")
    poi["detail_info"]["new_alias"] = "湖滨步行街;杭州湖滨步行街"
    if mode == "foreign":
        poi["city"] = "上海市"
    pois = [poi, dict(poi, uid="B2")] if mode == "duplicate" else [poi]
    with map_service(monkeypatch, {"results": pois}) as maps:
        result = maps.lookup("杭州", "湖滨步行" if mode == "partial" else "湖滨步行街")
    assert result.status == status
    if mode == "alias":
        assert result.matched_name == "湖滨路步行街"
        assert result.poi_id == "B1"
    else:
        assert result.location is None
        assert result.address is None


"""当前锚点测试函数：百度位置也必须核对成功且具备uid和坐标才能查周边。"""


@pytest.mark.parametrize("changes", [{"status": "no_match"}, {"location": None}, {"poi_id": None}])
def test_incomplete_baidu_anchor_never_queries_nearby(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any],
) -> None:
    with map_service(monkeypatch, {}) as maps:
        query = Mock(side_effect=AssertionError("锚点不完整不应查询"))
        monkeypatch.setattr(maps, "query", query)
        result = maps.nearby_places(anchor().model_copy(update=changes))
    assert result.status == "needs_clarification"
    assert not result.items
    query.assert_not_called()
