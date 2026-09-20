"""地图业务层：所有地点查询走百度MCP，核对同城、坐标、评分与消费。

连接和工具参数由原生适配器负责，本层只把真实返回值整理成已有业务格式。
"""

import json
import re
from contextlib import ExitStack
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Any, Literal, Self

from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from pydantic import ValidationError

from app.config import Settings
from app.schemas.dining import DiningItem, DiningResult
from app.schemas.document.answer import GeoPoint, MapLookup
from app.services.baidu_mcp import BaiduMCPClient, MCPTools

"""数字读取函数：接口数字或字符串必须有限，布尔值不当成价格或评分。"""


def read_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (ValueError, OverflowError):
        return None


"""坐标读取函数：明确标记百度BD-09，旧高德GCJ-02不能混用。"""


def read_point(value: object) -> GeoPoint | None:
    if not isinstance(value, dict):
        return None
    longitude, latitude = read_number(value.get("lng")), read_number(value.get("lat"))
    if longitude is None or latitude is None:
        return None
    try:
        return GeoPoint(longitude=longitude, latitude=latitude, coordinate_system="BD-09")
    except ValidationError:
        return None


class BaiduMaps:
    """地图服务类：请求内延迟连接百度MCP，地点核对与Agent复用同一连接。"""

    """初始化函数：只记录配置，重试命中历史时不会建立外部连接。"""

    def __init__(self, settings: Settings) -> None:
        self.client = BaiduMCPClient(settings)
        self._stack = ExitStack()
        self._tools: MCPTools | None = None

    """进入函数：资源随HTTP请求管理，不在进入时访问网络。"""

    def __enter__(self) -> Self:
        return self

    """释放函数：请求结束时关闭MCP连接及桥接线程。"""

    def __exit__(self, *args: Any) -> None:
        self._stack.close()
        self._tools = None

    """工具获取函数：本次请求只发现一次工具，后续查询复用连接。"""

    @property
    def tools(self) -> MCPTools:
        if self._tools is None:
            self._tools = self._stack.enter_context(self.client.open_tools())
        return self._tools

    """查询函数：通过框架工具执行，业务层只读取已检查过的JSON依据。"""

    def query(self, name: str, **arguments: Any) -> dict[str, Any]:
        tool = next((item for item in self.tools.tools if item.name == name), None)
        if tool is None:
            raise ToolException("百度地图查询工具不可用")
        result = tool.invoke({"name": name, "args": arguments, "id": "map-query",
                              "type": "tool_call"})
        if not isinstance(result, ToolMessage) or result.status == "error":
            raise ToolException("百度地图本次查询未成功")
        body = json.loads(self.tools.evidence[-1].content)
        if not isinstance(body, dict):
            raise ToolException("百度地图未返回可核对的地点数据")
        return body

    """地点核对函数：唯一同名且同城的POI才能入卡，不把行政中心冒充景点。"""

    def lookup(self, city: str, name: str) -> MapLookup:
        result = MapLookup(city=city, name=name, provider="baidu", status="unconfigured")
        if self.tools.status != "ready":
            return result.model_copy(update={"status": self.tools.status})
        try:
            body = self.query("map_search_places", query=name, region=city)
            pois = body.get("results")
            if not isinstance(pois, list):
                raise ValueError("地点列表格式错误")
            matches = [poi for poi in pois if isinstance(poi, dict)
                       and self._same_name(poi, name, city)
                       and self._same_place_kind(poi, name)
                       and self._same_city(poi, city)]
            if len(matches) != 1:
                return result.model_copy(update={"status": "ambiguous" if matches else "no_match"})
            poi = matches[0]
            point = read_point(poi.get("location"))
            if not isinstance(poi.get("uid"), str) or not poi["uid"] or point is None:
                return result.model_copy(update={"status": "no_match"})
            detail = poi.get("detail_info")
            detail = detail if isinstance(detail, dict) else {}
            cost = read_number(detail.get("price"))
            return result.model_copy(update={"status": "found", "poi_id": poi["uid"],
                "matched_name": poi["name"], "match_kind": "poi", "location": point,
                "address": poi.get("address") if isinstance(poi.get("address"), str) else None,
                "reference_cost": str(detail["price"]) if cost is not None and cost > 0 else None})
        except (ToolException, ValueError):
            return result.model_copy(update={"status": "error"})

    """城市核对函数：只接受提供方明确返回的同城或同区数据。"""

    @staticmethod
    def _same_city(poi: dict[str, Any], city: str) -> bool:
        return any(isinstance(poi.get(field), str)
                   and poi[field].removesuffix("市") == city.removesuffix("市")
                   for field in ("city", "area"))

    """名称核对函数：接受原名、官方别名、严格城市前缀和末尾景区称谓。"""

    @staticmethod
    def _same_name(poi: dict[str, Any], name: str, city: str) -> bool:
        detail = poi.get("detail_info")
        aliases = detail.get("new_alias") if isinstance(detail, dict) else None
        matched = poi.get("name")
        if matched == name or (isinstance(aliases, str) and name in aliases.split(";")):
            return True
        # 只去除完整末尾的景区称谓，不用包含匹配，西湖区和西湖停车场都不会命中。
        suffix = r"(?:(?:国家(?:重点)?)?风景名胜区|风景区|景区)$"
        if not isinstance(matched, str):
            return False
        target = re.sub(suffix, "", name)
        candidate = re.sub(suffix, "", matched)
        if candidate in (target, city.removesuffix("市") + target):
            return True
        # 提供方有时在景区主体前加省名；只接受地址明确印证的完整行政前缀。
        if not candidate.endswith(target):
            return False
        prefix = candidate[:-len(target)]
        address = poi.get("address")
        return bool(prefix and isinstance(address, str) and any(
            address.startswith(prefix + level) for level in ("省", "市", "自治区")
        ))

    """类别核对函数：目标不是交通或附属设施时，排除同名站点、停车场和服务点。"""

    @staticmethod
    def _same_place_kind(poi: dict[str, Any], name: str) -> bool:
        detail = poi.get("detail_info")
        tag = detail.get("tag") if isinstance(detail, dict) else None
        text = ";".join(value for value in (poi.get("name"), tag) if isinstance(value, str))
        auxiliary = {
            "公交车站": ("公交", "车站"),
            "停车场": ("停车场",),
            "出入口": ("入口", "出口"),
            "售票处": ("售票",),
            "游客中心": ("游客中心",),
        }
        return all(marker not in text or any(word in name for word in expected)
                   for marker, expected in auxiliary.items())

    """周边查询函数：先检索再补详情，只推荐有真实评分及满足消费要求的商户。"""

    def nearby_places(self, anchor: MapLookup, preference: str | None = None,
                      radius_m: int = 2000, category: Literal["dining", "lodging"] = "dining",
                      max_cost: float | None = None) -> DiningResult:
        result = DiningResult(status="unconfigured", anchor=anchor, preference=preference,
                              radius_m=radius_m, category=category, max_cost=max_cost,
                              provider="baidu")
        if self.tools.status != "ready":
            return result.model_copy(update={"status": self.tools.status})
        # 历史来源不改写；旧坐标必须按原名称重新匹配百度，不能直接换坐标标签。
        if anchor.provider != "baidu" or (anchor.location and
                                         anchor.location.coordinate_system != "BD-09"):
            anchor = self.lookup(anchor.city, anchor.matched_name or anchor.name)
            result = result.model_copy(update={"anchor": anchor})
        center = anchor.location
        if anchor.status != "found" or center is None or not anchor.poi_id:
            return result.model_copy(update={"status": "needs_clarification",
                "clarification": "请告诉我具体地点名称，暂未核对到百度地图中的唯一位置。"})
        kind, label = ("cater", "餐厅") if category == "dining" else ("hotel", "酒店")
        try:
            body = self.query("map_search_places", region=anchor.city,
                query=preference if preference and preference != "不限" else label, tag=label,
                location=f"{center.latitude},{center.longitude}", radius=radius_m)
            pois = body.get("results")
            if not isinstance(pois, list):
                raise ValueError("地点列表格式错误")
            # MCP搜索不保证附评分；只对本次召回前十项按真实uid补查，控制外部调用量。
            for poi in pois[:10]:
                if not isinstance(poi, dict) or not isinstance(poi.get("uid"), str):
                    continue
                detail = poi.get("detail_info")
                if not isinstance(detail, dict) or "overall_rating" not in detail:
                    more = self.query("map_place_details", uid=poi["uid"]).get("result")
                    if isinstance(more, dict) and more.get("uid") == poi["uid"]:
                        poi.update(more)
            items: list[DiningItem] = []
            seen: set[str] = set()
            missing = known_ratings = 0
            for poi in pois:
                if not isinstance(poi, dict):
                    continue
                detail = poi.get("detail_info")
                location = poi.get("location")
                if not isinstance(detail, dict) or not isinstance(location, dict):
                    continue
                # 类型必须明确匹配；名称中出现“酒店”或“美食”不能单独证明商户类别。
                if detail.get("type") != kind or not self._same_city(poi, anchor.city):
                    continue
                # 百度餐饮大类也含饮品；先看细分类，品牌名只补足缺少细分类的情况。
                if category == "dining" and any(word in str(detail.get("tag", ""))
                        + str(poi.get("name", "")) for word in (
                            "咖啡", "奶茶", "茶饮", "饮品", "甜品", "果汁", "冷饮",
                            "冰淇淋", "茶馆", "茶楼", "沪上阿姨", "蜜雪冰城", "茶百道",
                            "古茗", "喜茶", "奈雪", "霸王茶姬", "益禾堂", "一点点",
                        )):
                    continue
                point = read_point(location)
                if point is None:
                    continue
                lat1, lat2 = radians(center.latitude), radians(point.latitude)
                haversine = (sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2)
                             * sin(radians(point.longitude - center.longitude) / 2) ** 2)
                if 6371000 * 2 * asin(sqrt(min(1, haversine))) > radius_m:
                    continue
                uid, name = poi.get("uid"), poi.get("name")
                if (not isinstance(uid, str) or not uid.strip() or uid in seen
                        or not isinstance(name, str) or not name.strip()):
                    continue
                seen.add(uid)
                if preference and preference != "不限" and not any(
                    isinstance(value, str) and preference in value
                    for value in (name, detail.get("tag"))
                ):
                    continue
                score = read_number(detail.get("overall_rating"))
                if score is None or not 0 <= score <= 5:
                    missing += 1
                    continue
                known_ratings += 1
                if score < 4:
                    continue
                distance = read_number(detail.get("distance"))
                if detail.get("distance") is not None and (
                    distance is None or not 0 <= distance <= radius_m
                ):
                    continue
                cost = read_number(detail.get("price"))
                # 缺价、零价或负价不能证明满足消费上限；住宿同样只核对参考价。
                if max_cost is not None and (cost is None or not 0 < cost <= max_cost):
                    continue
                address = poi.get("address")
                items.append(DiningItem(
                    poi_id=uid, name=name, location=point, rating=score, distance_m=distance,
                    address=address if isinstance(address, str) else None,
                    reference_cost=str(detail["price"]) if cost is not None and cost > 0 else None,
                ))
            items.sort(key=lambda item: (-item.rating, item.distance_m
                                         if item.distance_m is not None else float("inf")))
            return result.model_copy(update={
                "status": "found" if items else (
                    "ratings_unavailable" if missing and not known_ratings else "empty"),
                "items": items[:5], "rating_missing_count": missing,
            })
        except (ToolException, ValueError):
            return result.model_copy(update={"status": "error"})
