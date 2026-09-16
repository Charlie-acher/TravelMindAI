"""测试层：用高德HTTP替身检查真实评分过滤、查询边界和连续餐饮追问。"""

from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.schemas.document.answer import GeoPoint, MapLookup
from app.services.amap import AmapClient

"""锚点生成函数：提供已确认的杭州景点坐标。"""


def anchor() -> MapLookup:
    return MapLookup(city="杭州", name="云栖竹径", status="found", poi_id="A1",
                     location=GeoPoint(longitude=120.10, latitude=30.10))


"""餐厅生成函数：测试数据中的评分仍按高德字符串返回。"""


def restaurant(rating: object, **changes: object) -> dict:
    return dict({"id": "B1", "name": "真实火锅店", "cityname": "杭州市",
                 "address": "梅灵南路10号", "location": "120.1001,30.1001",
                 "typecode": "050117", "distance": "100",
                 "business": {"rating": rating, "cost": "88"}}, **changes)


"""评分边界测试函数：只有有限且至少四分的原始评分可以推荐。"""


@pytest.mark.parametrize("rating,status", [
    ("4", "found"), ("4.8", "found"), ("3.9", "empty"),
    (None, "ratings_unavailable"), ([], "ratings_unavailable"),
    ("NaN", "ratings_unavailable"), ("Infinity", "ratings_unavailable"),
    ("5.1", "ratings_unavailable"), (True, "ratings_unavailable"),
])
def test_real_rating_boundaries(rating: object, status: str) -> None:
    """请求替身函数：核对附近接口、圆心、半径与城市约束。"""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/place/around"
        assert request.url.params["types"] == "050000"
        assert request.url.params["radius"] == "2000"
        assert request.url.params["show_fields"] == "business"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["keywords"] == "火锅"
        return httpx.Response(200, json={"status": "1", "pois": [restaurant(rating)]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="fake-test-key"), http).nearby_dining(
            anchor(), "火锅",
        )
    assert result.status == status
    assert "fake-test-key" not in result.model_dump_json()
    if status == "found":
        assert result.items[0].rating == float(rating)
        assert result.items[0].reference_cost == "88"


"""异常状态测试函数：API失败、空列表、无配置不混为找到了餐厅。"""


@pytest.mark.parametrize("mode,status", [
    ("failed", "error"), ("timeout", "error"), ("empty", "empty"),
    ("unconfigured", "unconfigured"), ("bad_json", "error"),
])
def test_api_states(mode: str, status: str) -> None:
    """请求替身函数：返回可控故障且禁止无密钥请求。"""

    def handle(request: httpx.Request) -> httpx.Response:
        assert mode != "unconfigured"
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-in-url", request=request)
        if mode == "bad_json":
            return httpx.Response(200, text="broken")
        return httpx.Response(200, json={"status": "0" if mode == "failed" else "1",
                                       "pois": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key=None if mode == "unconfigured" else "x"),
                            http).nearby_dining(anchor(), "火锅")
    assert result.status == status


"""城市距离测试函数：异地、超半径或非餐饮POI不能混入，结果最多五家。"""


def test_result_filter_sort_and_limit() -> None:
    pois = [restaurant("4.1", id=str(i), distance=str(100 + i)) for i in range(7)]
    pois += [restaurant("5", id="foreign", cityname="上海市"),
             restaurant("5", id="far", location="121.1,31.1"),
             restaurant("5", id="hotel", typecode="100000"),
             restaurant("4.9", id="best", distance="150")]
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "1", "pois": pois}),
    )) as http:
        result = AmapClient(Settings(amap_api_key="x"), http).nearby_dining(anchor(), "不限")
    assert [item.poi_id for item in result.items] == ["best", "0", "1", "2", "3"]


"""历史生成函数：构造只涉及餐饮和景点字段的已保存上下文。"""


def turn(*, dining=None, cards=()):
    return SimpleNamespace(response=SimpleNamespace(
        dining=dining, knowledge=SimpleNamespace(attractions=list(cards)),
    ))


"""短偏好测试函数：上一轮已选景点，下一轮只说火锅仍沿用准确位置。"""


def test_short_preference_inherits_anchor() -> None:
    from app.services.chat.dining import handle_dining

    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "1", "pois": [restaurant("4")]}),
    )) as http:
        maps = AmapClient(Settings(amap_api_key="x"), http)
        history = [turn(cards=[SimpleNamespace(location=anchor())])]
        first = handle_dining("这附近有推荐的饭店吗", "杭州", history, maps)
        assert first.status == "found"
        assert "火锅" in first.clarification
        assert first.anchor.name == "云栖竹径"
        result = handle_dining("火锅", "杭州", [turn(dining=first)], maps)
    assert result.status == "found"
    assert result.preference == "火锅"
    assert result.anchor.name == "云栖竹径"


"""锚点缺失测试函数：无地点、多地点或城市切换时先澄清，不乱用旧坐标。"""


@pytest.mark.parametrize("city,cards", [
    (None, []), ("杭州", []), ("上海", [anchor()]),
    ("杭州", [anchor(), anchor().model_copy(update={"name": "西湖", "poi_id": "A2"})]),
])
def test_missing_or_ambiguous_anchor(city, cards) -> None:
    from app.services.chat.dining import handle_dining

    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("不应联网"))) as http:
        result = handle_dining("附近吃火锅", city,
                               [turn(cards=[SimpleNamespace(location=a) for a in cards])],
                               AmapClient(Settings(amap_api_key="x"), http))
    assert result.status == "needs_clarification"
    assert not result.items


"""非餐饮测试函数：普通旅游问题不能被餐饮流程截获。"""


def test_non_dining_is_not_intercepted() -> None:
    from app.services.chat.dining import handle_dining

    with httpx.Client() as http:
        assert handle_dining("推荐杭州的免费景点", "杭州", [],
                             AmapClient(Settings(amap_api_key=None), http)) is None


"""明确地点测试函数：新地点必须重新核对，不能因为名字含旧景点名就复用旧坐标。"""


@pytest.mark.parametrize("message,expected", [
    ("推荐云栖竹径附近的火锅", "found"),
    ("推荐西湖附近的火锅", "found"),
    ("西湖周围有什么饭店推荐吗", "found"),
    ("推荐云栖竹径文化广场附近的火锅", "needs_clarification"),
])
def test_explicit_anchor_is_exact(message: str, expected: str) -> None:
    from app.services.chat.dining import handle_dining

    """高德响应函数：西湖可唯一核对，虚构文化广场不能替换为旧地点。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            districts = ([{"name": "杭州市", "level": "city", "adcode": "330100"}]
                         if request.url.params["keywords"] == "杭州" else [])
            return httpx.Response(200, json={"status": "1", "districts": districts})
        if request.url.path == "/v5/place/text":
            assert request.url.params["keywords"] != "云栖竹径"
            return httpx.Response(200, json={"status": "1", "pois": [{
                "id": "A2", "name": "西湖", "cityname": "杭州市", "location": "120.1,30.1",
            }]})
        assert "文化广场" not in message
        return httpx.Response(200, json={"status": "1", "pois": [restaurant("4")]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = handle_dining(message, "杭州", [turn(cards=[SimpleNamespace(location=anchor())])],
                               AmapClient(Settings(amap_api_key="x"), http))
    assert result.status == expected
    if "西湖" in message:
        assert result.anchor.name == "西湖"


"""自然口语测试函数：用户问好吃的也应进入餐饮查询。"""


def test_colloquial_dining_question() -> None:
    from app.services.chat.dining import handle_dining

    with httpx.Client() as http:
        result = handle_dining("附近有什么好吃的", "杭州", [],
                               AmapClient(Settings(amap_api_key=None), http))
    assert result is not None
    assert result.status == "needs_clarification"


"""否定偏好测试函数：拒绝火锅时先问其他口味，不能推荐被拒绝的食物。"""


@pytest.mark.parametrize("message", ["我不吃火锅", "不要火锅", "杭州哪里好吃"])
def test_negative_preference_and_new_city_scope_do_not_search(message: str) -> None:
    from app.services.chat.dining import handle_dining

    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("不应联网"))) as http:
        result = handle_dining(message, "杭州", [turn(cards=[SimpleNamespace(location=anchor())])],
                               AmapClient(Settings(amap_api_key="x"), http))
    assert result.status == "needs_clarification"
    assert result.preference is None
    if message.startswith("杭州"):
        assert result.anchor is None
    else:
        assert result.anchor.name == "云栖竹径"


"""真实进度测试函数：仅实际地图调用发出步骤，普通旅游问题保持安静。"""


def test_map_progress_matches_actual_calls() -> None:
    from app.services.chat.dining import handle_dining
    from app.services.chat.events import event_sink

    events = []

    """请求替身函数：检查事件发生在相应地图请求之前。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            assert events[-1][1]["stage"] == "map"
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "杭州市", "level": "city", "adcode": "330100",
            }]})
        if request.url.path == "/v5/place/text":
            return httpx.Response(200, json={"status": "1", "pois": [{
                "id": "A2", "name": "湖滨路步行街", "cityname": "杭州市",
                "location": "120.1,30.1",
            }]})
        assert events[-1][1]["stage"] == "dining"
        return httpx.Response(200, json={"status": "1", "pois": [restaurant("4")]})

    token = event_sink.set(lambda name, data: events.append((name, data)))
    try:
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            maps = AmapClient(Settings(amap_api_key="x"), http)
            assert handle_dining("推荐杭州景点", "杭州", [], maps) is None
            assert events == []
            result = handle_dining("湖滨路步行街周围有什么饭店推荐吗", "杭州", [], maps)
        assert result.status == "found"
        assert [event[1]["stage"] for event in events] == ["map", "dining"]
    finally:
        event_sink.reset(token)


"""口味核对测试函数：关键词召回的高分店也必须有名称、分类或标签证明口味。"""


def test_preference_requires_real_poi_evidence() -> None:
    pois = [
        restaurant("5", id="unrelated", name="一家素·蔬食料理", type="餐饮服务;中餐厅",
                   business={"rating": "5", "keytag": "素食"}),
        restaurant("4.9", id="category", name="真实甲店", type="餐饮服务;中餐厅;火锅店"),
        restaurant("4.8", id="tag", name="真实乙店", type="餐饮服务;中餐厅",
                   business={"rating": "4.8", "keytag": "火锅餐厅"}),
        restaurant("4.7", id="name", name="真实火锅店"),
        restaurant("4.6", id="unknown", name="未知分类店", type=[],
                   business={"rating": "4.6", "keytag": []}),
    ]
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "1", "pois": pois}),
    )) as http:
        result = AmapClient(Settings(amap_api_key="x"), http).nearby_dining(anchor(), "火锅")
    assert [item.poi_id for item in result.items] == ["category", "tag", "name"]


"""空口味结果测试函数：高评分不能让无关餐厅顶替指定口味。"""


def test_only_unrelated_restaurants_returns_empty() -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "1", "pois": [
            restaurant("4.9", name="一家素·蔬食料理", type="餐饮服务;中餐厅"),
        ]}),
    )) as http:
        result = AmapClient(Settings(amap_api_key="x"), http).nearby_dining(anchor(), "火锅")
    assert result.status == "empty"
    assert result.items == []
