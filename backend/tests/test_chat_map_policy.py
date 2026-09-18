"""测试层：普通资料推荐不消耗地图查询，降级回答仍保存真实参考资料。"""

import json
from unittest.mock import Mock

import pytest

from app.schemas.document.answer import MapLookup, WebSearchResult
from app.schemas.document.search import SearchResult
from app.services.chat.rag import ground_chat_response
from tests.helpers import evidence
from tests.test_chat_rag import response

"""推荐回归函数：即使模型主动填地图请求，也只展示资料卡片，不自动补查。"""


@pytest.mark.parametrize("message", ["想去深圳", "推荐看海的地方"])
def test_travel_recommendation_does_not_lookup_locations(message: str) -> None:
    hit, search, model, maps = evidence(), Mock(), Mock(), Mock()
    hit.chunk.text = "深圳西涌适合看海。"
    search.search.return_value = SearchResult(items=[hit])
    maps.lookup.return_value = MapLookup(city="深圳", name="西涌", status="no_match")
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "西涌适合看海。", "source_ids": [1]}],
        "attractions": [{"city": "深圳", "name": "西涌", "description": "适合看海。",
                         "reason": "符合看海偏好。", "source_ids": [1]}],
        "map_queries": [{"city": "深圳", "name": "西涌", "source_id": 1}],
    })
    result = ground_chat_response(response(message), search, model, maps=maps,
                                  retrieval_query="深圳看海推荐", query_cities=["深圳"])
    maps.lookup.assert_not_called()
    model.generate_json.assert_called_once()
    assert result.knowledge is not None
    assert result.knowledge.map_lookups == []
    assert result.knowledge.attractions[0].location.status == "not_requested"
    assert result.knowledge.sources[0].hit.chunk.id == hit.chunk.id


"""参考保留函数：结构化失败后的自然回答仍能查看传入模型的原文。"""


def test_reference_only_reply_keeps_actual_sources() -> None:
    hit, search, model = evidence(), Mock(), Mock()
    search.search.return_value = SearchResult(items=[hit])
    model.generate_text.return_value = "可以去湖滨路走走。"
    result = ground_chat_response(response("推荐散步地点"), search, model,
                                  reference_only=True, query_cities=["杭州"])
    assert result.knowledge is not None
    assert result.knowledge.sources[0].hit.chunk.id == hit.chunk.id
    assert result.knowledge.status == "insufficient"
    assert result.knowledge.attractions == []
    assert hit.chunk.text in model.generate_text.call_args.args[0][1]["content"]


"""网页禁用测试函数：有无知识命中及结构化降级，都不发起网页补查。"""


@pytest.mark.parametrize("has_hits,reference_only", [(True, False), (False, False), (False, True)])
def test_chat_never_searches_web(has_hits: bool, reference_only: bool) -> None:
    search, model, web = Mock(), Mock(), Mock()
    web.search.return_value = WebSearchResult(status="empty")
    search.search.return_value = SearchResult(items=[evidence()] if has_hits else [])
    model.generate_json.return_value = '{"status":"insufficient","points":[]}'
    model.generate_text.return_value = "可以先介绍杭州的旅行方向。"
    result = ground_chat_response(response("杭州有什么推荐景点"), search, model,
                                  web=web, reference_only=reference_only,
                                  query_cities=["杭州"])
    web.search.assert_not_called()
    assert not result.knowledge or result.knowledge.web_search.status == "not_requested"


"""选材测试函数：相关综合攻略不能独占名额，景点正文也进入本轮模型输入。"""


def test_chat_selects_multiple_relevant_documents() -> None:
    from uuid import uuid4

    guide = evidence()
    hits = []
    for index in range(6):
        hit = guide.model_copy(deep=True)
        hit.chunk.id = uuid4()
        hit.chunk.text = f"杭州西湖攻略正文{index}"
        hits.append(hit)
    places = evidence()
    places.chunk.document_id = uuid4()
    places.file_name = "杭州-景点.md"
    places.chunk.text = "杭州西湖是自然风光景点。"
    hits.append(places)
    source_only = places.model_copy(deep=True)
    source_only.chunk.id = uuid4()
    source_only.chunk.text = "来源：https://example.com/dataset"
    hits.insert(0, source_only)
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=hits)
    model.generate_text.return_value = "可以去西湖。"
    result = ground_chat_response(response("杭州有什么推荐景点"), search, model,
                                  reference_only=True, query_cities=["杭州"],
                                  retrieval_category="景点")
    assert result.knowledge is not None
    assert places.chunk.id in [source.hit.chunk.id for source in result.knowledge.sources]
    assert source_only.chunk.id not in [source.hit.chunk.id for source in result.knowledge.sources]
    assert search.search.call_args.kwargs["metadata"].category == "景点"
