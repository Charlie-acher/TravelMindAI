"""外部工具连接层：查询高德地点信息，核对城市和名称，供知识回答补充参考。

只使用公开Web服务的已声明字段；人均消费不能证明是否收费或门票多少钱。
"""

from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.dining import DiningItem, DiningResult
from app.schemas.document.answer import GeoPoint, MapLookup

# 限定区划的已核对名称变体；不做全局模糊匹配。
VERIFIED_POI_NAMES = {
    ("330100", "湖滨路步行街"): "湖滨步行街",
    ("330100", "千岛湖珍珠广场"): "千岛湖风景区-千岛湖珍珠广场",
    ("330127", "千岛湖珍珠广场"): "千岛湖风景区-千岛湖珍珠广场",
}

"""坐标读取函数：高德用经度、纬度字符串返回位置，异常值保留为空。"""


def read_coordinate(value: object) -> GeoPoint | None:
    if not isinstance(value, str):
        return None
    try:
        longitude, latitude = value.split(",")
        return GeoPoint(longitude=float(longitude), latitude=float(latitude))
    except (ValueError, ValidationError):
        return None


"""地址拼接函数：按省市区县补齐高德原址，去掉原址已有的行政前缀。"""


def full_address(poi: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for field in ("pname", "cityname", "adname"):
        part = poi.get(field)
        if isinstance(part, str) and part and part not in parts:
            parts.append(part)
    prefix = "".join(parts)
    street = poi.get("address")
    if not isinstance(street, str) or not street:
        return prefix or None
    for part in parts:
        if street.startswith(part):
            street = street[len(part):]
    return prefix + street or None


class AmapClient:
    """高德客户端类：一次查询一个景点，返回可保存的结果，不泄露请求密钥。"""

    """初始化函数：借用请求期间的HTTP连接和后端密钥，不在导入时连接外网。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        self.key = settings.amap_api_key
        self.http = http

    """附近餐饮查询函数：按真实锚点检索两公里圆形区域，只接收四分及以上原始评分。"""

    def nearby_dining(self, anchor: MapLookup, preference: str | None = None,
                      radius_m: int = 2000) -> DiningResult:
        result = DiningResult(status="unconfigured", anchor=anchor, preference=preference,
                              radius_m=radius_m)
        center = anchor.location
        if anchor.status != "found" or center is None:
            return result.model_copy(update={"status": "needs_clarification",
                                             "clarification": "请先明确要查询的景点或具体地点。"})
        if self.key is None or not self.key.get_secret_value().strip():
            return result
        try:
            params: dict[str, str | int] = {
                "key": self.key.get_secret_value(), "types": "050000",
                "location": f"{center.longitude:.6f},{center.latitude:.6f}",
                "radius": radius_m, "show_fields": "business", "sortrule": "distance",
                "region": anchor.city, "city_limit": "true", "page_size": 25, "page_num": 1,
            }
            if preference and preference != "不限":
                params["keywords"] = preference
            response = self.http.get("https://restapi.amap.com/v5/place/around",
                                     params=params, timeout=5)
            response.raise_for_status()
            body = response.json()
            if (not isinstance(body, dict) or body.get("status") != "1"
                    or not isinstance(body.get("pois"), list)):
                return result.model_copy(update={"status": "error"})
            items: list[DiningItem] = []
            missing = 0
            known_ratings = 0
            seen: set[str] = set()
            for poi in body["pois"]:
                if not isinstance(poi, dict):
                    continue
                point = read_coordinate(poi.get("location"))
                # 附近搜索仍核对所属城市和坐标，防止错误响应带入异地同名餐厅。
                if (point is None or poi.get("cityname") not in (anchor.city, f"{anchor.city}市")
                        and poi.get("adname") != anchor.city
                        or not isinstance(poi.get("typecode"), str)
                        or not poi["typecode"].startswith("05")):
                    continue
                lat1, lat2 = radians(center.latitude), radians(point.latitude)
                haversine = (sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2)
                             * sin(radians(point.longitude - center.longitude) / 2) ** 2)
                if 6371000 * 2 * asin(sqrt(min(1, haversine))) > radius_m:
                    continue
                if (not isinstance(poi.get("id"), str) or not poi["id"]
                        or poi["id"] in seen or not isinstance(poi.get("name"), str)
                        or not poi["name"].strip()):
                    continue
                seen.add(poi["id"])
                business = poi.get("business")
                business = business if isinstance(business, dict) else {}
                # 关键词召回不等于口味已核实；名称、分类或商业标签至少一项须直接支持。
                if preference and preference != "不限" and not any(
                    isinstance(value, str) and preference in value
                    for value in (poi["name"], poi.get("type"), business.get("keytag"))
                ):
                    continue
                rating = business.get("rating")
                try:
                    score = Decimal(rating) if isinstance(rating, str) else Decimal("NaN")
                except InvalidOperation:
                    score = Decimal("NaN")
                if not score.is_finite() or not 0 <= score <= 5:
                    missing += 1
                    continue
                known_ratings += 1
                if score < 4:
                    continue
                distance = None
                cost = None
                # 距离和消费没有有效值时留空，不用推算值冒充地图返回值。
                for field, raw in (("distance", poi.get("distance")),
                                   ("cost", business.get("cost"))):
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    try:
                        number = Decimal(raw)
                        if number.is_finite() and number >= 0:
                            if field == "distance":
                                distance = float(number)
                            else:
                                cost = raw
                    except InvalidOperation:
                        pass
                if distance is not None and distance > radius_m:
                    continue
                items.append(DiningItem(poi_id=poi["id"], name=poi["name"],
                                        address=full_address(poi), location=point,
                                        rating=float(score), distance_m=distance,
                                        reference_cost=cost))
            items.sort(key=lambda item: (-item.rating, item.distance_m
                                         if item.distance_m is not None else float("inf")))
            return result.model_copy(update={
                "status": "found" if items else (
                    "ratings_unavailable" if missing and not known_ratings else "empty"),
                "items": items[:5], "rating_missing_count": missing,
            })
        except (httpx.HTTPError, ValueError):
            return result.model_copy(update={"status": "error"})

    """区划查询函数：从高德读取真实区划，失败时交由查询函数返回错误。"""

    def _districts(self, words: str) -> list[dict[str, Any]]:
        if self.key is None:
            raise ValueError("map unconfigured")
        response = self.http.get(
            "https://restapi.amap.com/v3/config/district",
            params={"key": self.key.get_secret_value(), "keywords": words,
                    "subdistrict": "0", "extensions": "base"},
            timeout=5,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("status") != "1":
            raise ValueError("district query failed")
        districts = body.get("districts")
        if not isinstance(districts, list):
            raise ValueError("district response invalid")
        return [item for item in districts if isinstance(item, dict)]

    """行政地点回退函数：只有全国唯一且属于查询区划的乡镇才使用高德镇中心。"""

    def _administrative_place(self, name: str, region: dict[str, Any],
                              pois: list[Any], result: MapLookup) -> MapLookup:
        districts = self._districts(name)
        matches = [item for item in districts
                   if item.get("level") == "street"
                   and item.get("name") in (name, f"{name}镇")]
        if len(matches) != 1:
            return result.model_copy(update={"status": "ambiguous" if matches else "no_match"})
        town = matches[0]
        code = town.get("adcode")
        region_code = region["adcode"]
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            return result.model_copy(update={"status": "no_match"})
        if region.get("level") == "district":
            same_region = code == region_code
        else:
            same_region = (town.get("citycode") == region.get("citycode")
                           and code[:4] == region_code[:4])
        if not same_region:
            return result.model_copy(update={"status": "no_match"})
        # POI只用于核对省市区县层级；镇的位置必须采用区划API的中心。
        names = {tuple(poi.get(field) for field in ("pname", "cityname", "adname"))
                 for poi in pois if isinstance(poi, dict)
                 and poi.get("adcode") == code
                 and poi.get("citycode") == town.get("citycode")
                 and poi.get("pcode") == f"{code[:2]}0000"
                 and all(isinstance(poi.get(field), str) and poi[field]
                         for field in ("pname", "cityname", "adname"))}
        center = read_coordinate(town.get("center"))
        if len(names) != 1 or center is None:
            return result.model_copy(update={"status": "no_match"})
        province, city_name, county = names.pop()
        if (not isinstance(province, str) or not isinstance(city_name, str)
                or not isinstance(county, str)):
            return result.model_copy(update={"status": "no_match"})
        if region.get("level") == "district" and county != region["name"]:
            return result.model_copy(update={"status": "no_match"})
        if region.get("level") == "city" and city_name != region["name"]:
            return result.model_copy(update={"status": "no_match"})
        return result.model_copy(update={
            "status": "found", "matched_name": town["name"],
            "match_kind": "administrative", "address": province + city_name + county + town["name"],
            "location": center,
        })

    """景点查询函数：先核对区划，再唯一匹配地点或真实行政镇。"""

    def lookup(self, city: str, name: str) -> MapLookup:
        result = MapLookup(city=city, name=name, status="unconfigured")
        if self.key is None or not self.key.get_secret_value().strip():
            return result
        try:
            # v5的region只正式支持城市中文名或区划代码；先取代码并核对同名区划。
            regions = self._districts(city)
            regions = [item for item in regions
                       if item.get("name") in (city, f"{city}市", f"{city}区", f"{city}县")
                       and item.get("level") in ("city", "district")]
            if len(regions) != 1:
                return result.model_copy(update={
                    "status": "ambiguous" if regions else "no_match",
                })
            region = regions[0]
            code = region.get("adcode")
            if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
                return result.model_copy(update={"status": "error"})
            response = self.http.get(
                "https://restapi.amap.com/v5/place/text",
                params={"key": self.key.get_secret_value(), "keywords": name,
                        "region": code, "city_limit": "true", "show_fields": "business,navi",
                        "page_size": 25, "page_num": 1},
                timeout=5,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("status") != "1":
                return result.model_copy(update={"status": "error"})
            pois = body.get("pois")
            if not isinstance(pois, list):
                return result.model_copy(update={"status": "error"})
            # 搜索排序不能证明是同一个景点；宁可不匹配，也不拿附近商店或异地同名点代替。
            accepted_name = VERIFIED_POI_NAMES.get((code, name), name)
            matches = [poi for poi in pois if isinstance(poi, dict)
                       and poi.get("name") in (name, accepted_name)
                       and ((region["level"] == "city"
                             and poi.get("cityname") == region["name"])
                            or (region["level"] == "district"
                                and poi.get("adname") == region["name"]
                                and poi.get("adcode") == code))]
            if len(matches) != 1:
                if not matches:
                    return self._administrative_place(name, region, pois, result)
                return result.model_copy(update={
                    "status": "ambiguous" if matches else "no_match",
                })
            poi = matches[0]
            if not isinstance(poi.get("id"), str) or not poi["id"]:
                return result.model_copy(update={"status": "error"})
            business = poi.get("business")
            navi = poi.get("navi")
            cost = business.get("cost") if isinstance(business, dict) else None
            # 空数组、空串和非法金额都表示无有效参考值；零也不能推导为免费。
            reference_cost = None
            if isinstance(cost, str) and cost.strip():
                try:
                    number = Decimal(cost)
                    if number.is_finite() and number >= 0:
                        reference_cost = cost
                except InvalidOperation:
                    pass
            return result.model_copy(update={
                "status": "found", "poi_id": poi["id"], "reference_cost": reference_cost,
                "matched_name": poi["name"], "match_kind": "poi",
                "address": full_address(poi),
                "location": read_coordinate(poi.get("location")),
                "entrance": read_coordinate(navi.get("entr_location"))
                if isinstance(navi, dict) else None,
            })
        except (httpx.HTTPError, ValueError):
            # 异常可能含带key的URL，不把异常原文写进聊天、日志或保存快照。
            return result.model_copy(update={"status": "error"})
