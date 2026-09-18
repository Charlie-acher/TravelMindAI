"""资料业务层：为回答补查地图，只允许查询本轮知识片段中有依据的地点。

回答服务调用本文件；地点匹配和MCP查询由BaiduMaps执行。
"""

import re

from app.schemas.document.answer import MapLookup, MapQuery, ModelAnswer, WebEvidence
from app.schemas.document.search import SearchHit
from app.services.baidu import BaiduMaps
from app.services.document.places import evidence_texts

"""地图补查函数：补齐漏填的请求，全部核对依据后，按城市与名称去重查询。

调用前，回答服务已检查所有回答要点的source_ids都属于本轮证据。
"""


def supplement_maps(
    answer: ModelAnswer, evidence: list[SearchHit], maps: BaiduMaps | None,
    web_sources: list[WebEvidence] | None = None,
) -> list[MapLookup]:
    texts = evidence_texts(evidence, web_sources or [])
    # 每个待展示景点都要定位，不能只在缺少门票时才调用地图。
    requested = {(item.city, item.name) for item in answer.map_queries}
    for attraction in answer.attractions:
        pair = (attraction.city, attraction.name)
        if pair not in requested:
            source_id = next(index for index in attraction.source_ids
                             if attraction.city in texts[index] and attraction.name in texts[index])
            answer.map_queries.append(MapQuery(city=pair[0], name=pair[1], source_id=source_id))
            requested.add(pair)
    for point in answer.points:
        # 模型漏填卡片时，仍为正文明确介绍的结构化景点补定位。
        for source_id in point.source_ids:
            body = texts[source_id]
            city = re.search(r"(?:^|[；\n])\s*城市：([^；。\n]+)", body)
            name = re.search(
                r"(?:^|[；\n])\s*(?:景点名称|名称（name）)：([^；。，,\n]+)", body,
            )
            if city is None or name is None or name[1].strip() not in point.text:
                continue
            pair = (city[1].strip(), name[1].strip())
            if pair not in requested and len(answer.map_queries) < 3:
                answer.map_queries.append(MapQuery(
                    city=pair[0], name=pair[1], source_id=source_id,
                ))
                requested.add(pair)
    # 先全部校验，再调用工具，防止模型查无依据的新地点或使用越界编号。
    if len(requested) > 3:
        raise ValueError("一轮最多核对三个景点")
    for lookup_query in answer.map_queries:
        if lookup_query.source_id not in texts:
            raise ValueError("地图查询缺少依据")
        text = texts[lookup_query.source_id]
        if lookup_query.name not in text or lookup_query.city not in text:
            raise ValueError("城市或景点不在指定原文中")
    # 相同城市和景点只查一次；地图失败可保留有依据的介绍，知识库失败仍不放行。
    unique = dict.fromkeys((query.city, query.name) for query in answer.map_queries)
    return [maps.lookup(city, name) if maps is not None else
               MapLookup(city=city, name=name, status="unconfigured", provider="baidu")
               for city, name in unique]
