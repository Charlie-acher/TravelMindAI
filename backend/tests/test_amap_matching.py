"""测试层：模拟高德地点和区划响应，检查别名与行政地点不会误匹配。"""

import httpx
import pytest

from app.config import Settings
from app.services.amap import AmapClient

"""地址拼接测试函数：重复行政前缀逐级消费，只显示一份省市区县。"""


@pytest.mark.parametrize("parts,raw,expected", [
    (("浙江省", "杭州市", "淳安县"), "浙江省杭州市淳安县梦姑路348号",
     "浙江省杭州市淳安县梦姑路348号"),
    (("浙江省", "杭州市", "淳安县"), "浙江省杭州市梦姑路348号",
     "浙江省杭州市淳安县梦姑路348号"),
    (("浙江省", "杭州市", "上城区"), "杭州市上城区湖滨路",
     "浙江省杭州市上城区湖滨路"),
    (("北京市", "北京市", "朝阳区"), "北京市朝阳区建国路",
     "北京市朝阳区建国路"),
])
def test_full_address_deduplicates_returned_prefix(
    parts: tuple[str, str, str], raw: str, expected: str,
) -> None:
    from app.services.amap import full_address

    assert full_address(dict(zip(("pname", "cityname", "adname"), parts),
                             address=raw)) == expected

"""地点匹配测试函数：已核实的名称变体与区县查询应返回真实地点。"""


@pytest.mark.parametrize("city,name,poi_name,adname,adcode,region", [
    ("杭州", "湖滨路步行街", "湖滨步行街", "上城区", "330102", "330100"),
    ("淳安县", "千岛湖珍珠广场", "千岛湖风景区-千岛湖珍珠广场",
     "淳安县", "330127", "330127"),
])
def test_verified_name_variant_matches_with_region(
    city: str, name: str, poi_name: str, adname: str, adcode: str, region: str,
) -> None:
    """请求替身函数：先返回唯一查询区划，再返回该区划的高德地点。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "杭州市" if city == "杭州" else "淳安县",
                "level": "city" if city == "杭州" else "district",
                "adcode": region, "citycode": "0571",
            }]})
        assert request.url.path == "/v5/place/text"
        assert request.url.params["region"] == region
        poi = {"id": "B001", "name": poi_name, "pname": "浙江省",
               "cityname": "杭州市", "adname": adname, "adcode": adcode,
               "address": "梦姑路348号" if city != "杭州" else "凤起路539-4号",
               "location": "120.162923,30.254158"}
        return httpx.Response(200, json={"status": "1", "pois": [
            dict(poi, id="nearby", name=f"{poi_name}地下停车场"), poi,
        ]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup(city, name)
    assert result.status == "found"
    assert result.poi_id == "B001"
    assert result.location is not None
    assert result.address == ("浙江省杭州市" + adname +
                              ("梦姑路348号" if city != "杭州" else "凤起路539-4号"))


"""同市别名测试函数：杭州市查询珍珠广场时仍接受淳安县真实景区地点。"""


def test_hangzhou_query_matches_verified_qiandao_square() -> None:
    """请求替身函数：限定杭州区划，并返回淳安县的完整景区名称。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "杭州市", "level": "city", "adcode": "330100",
                "citycode": "0571",
            }]})
        assert request.url.params["region"] == "330100"
        return httpx.Response(200, json={"status": "1", "pois": [{
            "id": "B0FFF476HF", "name": "千岛湖风景区-千岛湖珍珠广场",
            "pname": "浙江省", "cityname": "杭州市", "adname": "淳安县",
            "adcode": "330127", "address": "千岛湖镇梦姑路348号千岛湖风景区",
            "location": "119.113943,29.596976",
        }]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup(
            "杭州", "千岛湖珍珠广场",
        )
    assert result.status == "found"
    assert result.matched_name == "千岛湖风景区-千岛湖珍珠广场"
    assert result.poi_id == "B0FFF476HF"
    assert result.address == "浙江省杭州市淳安县千岛湖镇梦姑路348号千岛湖风景区"


"""同名安全测试函数：多个目标别名或跨区县结果不能被搜索顺序覆盖。"""


@pytest.mark.parametrize("mode,expected", [("duplicate", "ambiguous"),
                                          ("wrong_district", "no_match")])
def test_variant_requires_unique_poi_in_requested_district(
    mode: str, expected: str,
) -> None:
    """请求替身函数：构造区划明确但地点重复或跨区县的高德结果。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "淳安县", "level": "district", "adcode": "330127",
                "citycode": "0571",
            }]})
        poi = {"id": "B001", "name": "千岛湖风景区-千岛湖珍珠广场",
               "pname": "浙江省", "cityname": "杭州市", "adname": "淳安县",
               "adcode": "330127", "address": "梦姑路348号",
               "location": "119.113943,29.596976"}
        if mode == "wrong_district":
            poi["adname"], poi["adcode"] = "上城区", "330102"
        pois = [poi, dict(poi, id="B002")] if mode == "duplicate" else [poi]
        return httpx.Response(200, json={"status": "1", "pois": pois})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup(
            "淳安县", "千岛湖珍珠广场",
        )
    assert result.status == expected
    assert result.address is None
    assert result.location is None


"""行政地点测试函数：戴村按唯一行政镇中心返回，不借镇政府或景区坐标。"""


def test_dai_village_uses_verified_administrative_town_center() -> None:
    """请求替身函数：返回萧山区、无同名POI、戴村镇及其省市上级。"""

    def handle(request: httpx.Request) -> httpx.Response:
        words = request.url.params.get("keywords")
        if request.url.path == "/v5/place/text":
            assert request.url.params["region"] == "330109"
            return httpx.Response(200, json={"status": "1", "pois": [{
                "id": "wrong", "name": "戴村镇政府", "cityname": "杭州市",
                "pname": "浙江省", "pcode": "330000", "citycode": "0571",
                "adname": "萧山区", "adcode": "330109",
                "location": "120.195180,30.018340",
            }]})
        districts = {
            "萧山": [{"name": "萧山区", "level": "district", "adcode": "330109",
                     "citycode": "0571"}],
            "戴村": [{"name": "戴村镇", "level": "street", "adcode": "330109",
                    "citycode": "0571", "center": "120.222074,30.004527"}],
        }
        return httpx.Response(200, json={"status": "1", "districts": districts[words]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup("萧山", "戴村")
    assert result.status == "found"
    assert result.poi_id is None
    assert result.address == "浙江省杭州市萧山区戴村镇"
    assert result.location is not None
    assert (result.location.longitude, result.location.latitude) == (120.222074, 30.004527)
    assert result.entrance is None


"""行政歧义测试函数：同名行政镇或归属不一致时不生成坐标与地址。"""


@pytest.mark.parametrize("mode,expected", [("duplicate", "ambiguous"),
                                          ("wrong_parent", "no_match"),
                                          ("conflicting_metadata", "no_match")])
def test_administrative_fallback_checks_uniqueness_and_parent(
    mode: str, expected: str,
) -> None:
    """请求替身函数：行政镇可能重名，也可能不在所请求的萧山区。"""

    def handle(request: httpx.Request) -> httpx.Response:
        words = request.url.params.get("keywords")
        if request.url.path == "/v5/place/text":
            pois = []
            if mode == "conflicting_metadata":
                base = {"id": "B001", "name": "戴村镇政府",
                        "pname": "浙江省", "pcode": "330000", "cityname": "杭州市",
                        "adname": "萧山区", "adcode": "330109", "citycode": "0571"}
                pois = [base, dict(base, id="B002", cityname="别的市")]
            return httpx.Response(200, json={"status": "1", "pois": pois})
        if words == "萧山":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "萧山区", "level": "district", "adcode": "330109",
                "citycode": "0571",
            }]})
        town = {"name": "戴村镇", "level": "street",
                "adcode": "330127" if mode == "wrong_parent" else "330109",
                "citycode": "0571", "center": "120.222074,30.004527"}
        towns = [town, dict(town, adcode="330127")] if mode == "duplicate" else [town]
        return httpx.Response(200, json={"status": "1", "districts": towns})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup("萧山", "戴村")
    assert result.status == expected
    assert result.address is None
    assert result.location is None
