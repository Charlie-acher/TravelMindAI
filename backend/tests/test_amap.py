"""测试层：用模拟HTTP检查地图匹配、消费金额与失败处理，不访问高德。"""

import httpx
import pytest

from app.config import Settings

"""地图查询测试函数：同名异地、多个同名点、空价格和网络错误不能生成门票结论。"""


@pytest.mark.parametrize("mode, expected", [
    ("normal", "found"), ("zero", "found"), ("empty", "found"),
    ("wrong_city", "no_match"), ("duplicate", "ambiguous"),
    ("denied", "error"), ("timeout", "error"), ("bad_json", "error"),
])
def test_map_lookup_preserves_price_meaning(mode: str, expected: str) -> None:
    from app.services.amap import AmapClient

    """请求替身函数：检查地域限制与商业字段，返回不同的高德响应。"""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "restapi.amap.com"
        if request.url.path == "/v3/config/district":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "杭州市", "level": "city", "adcode": "330100",
                "citycode": "0571",
            }] if request.url.params["keywords"] == "杭州" else []})
        assert request.url.params["region"] == "330100"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["show_fields"] == "business,navi"
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-in-url", request=request)
        if mode == "bad_json":
            return httpx.Response(200, text="not json")
        poi = {"id": "B001", "name": "云栖竹径", "cityname": "杭州市",
               "address": "梅灵南路", "business": {"cost": "8"}}
        if mode == "zero":
            poi["business"] = {"cost": "0"}
        if mode == "empty":
            poi["business"] = {"cost": []}
        if mode == "wrong_city":
            poi["cityname"] = "上海市"
        pois = [poi, dict(poi, id="B002")] if mode == "duplicate" else [poi]
        return httpx.Response(200, json={"status": "0" if mode == "denied" else "1",
                                       "pois": pois})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test-key"), http).lookup("杭州", "云栖竹径")
    assert result.status == expected
    assert "test-key" not in result.model_dump_json()
    assert "ticket_price" not in result.model_dump()
    assert result.checked_at is not None
    if expected == "found":
        assert result.reference_cost == ({"zero": "0", "empty": None}.get(mode, "8"))


"""未配置测试函数：没有密钥时不发请求，清楚返回未启用状态。"""


def test_map_without_key_never_sends_request() -> None:
    from app.services.amap import AmapClient

    with httpx.Client() as http:
        result = AmapClient(Settings(amap_api_key=None), http).lookup("杭州", "云栖竹径")
    assert result.status == "unconfigured"
    assert result.reference_cost is None
