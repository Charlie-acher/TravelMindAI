"""研究委派测试层：主规划模型实际调用独立子循环，核实结果回到原规划工具箱。"""

from unittest.mock import Mock

from app.schemas.document.answer import GeoPoint, MapLookup
from app.schemas.document.search import SearchResult
from app.services.itinerary.graph import plan_trip
from tests.helpers import evidence
from tests.test_itinerary_agent import proposal, requirements
from tests.test_nearby import native

"""模型序列包含主委派、子查询、子返回、主提交，子上下文不含主内部消息。"""

def test_main_delegates_research_and_uses_verified_places():
    hit = evidence()
    hit.chunk.text += "河坊街位于杭州。"
    search, maps = Mock(), Mock()
    search.search.return_value = SearchResult(items=[hit])
    maps.lookup.side_effect = lambda city, name: MapLookup(
        city=city, name=name, status="found", match_kind="poi", poi_id=name,
        location=GeoPoint(longitude=120.1, latitude=30.2))
    model = native(
        ("delegate_research", {"task": "核实杭州两日慢游的两个地点"}),
        ("knowledge_search", {"query": "杭州慢游"}),
        ("map_lookup", {"name": "湖滨路步行街", "source_ids": ["k1"]}),
        ("map_lookup", {"name": "河坊街", "source_ids": ["k1"]}),
        ("finish_research", {"place_ids": ["p1", "p2"], "missing": ""}),
        ("submit_plan", {"days": [proposal(1, "p1").model_dump(),
                                    proposal(2, "p2").model_dump()]}),
    )
    result = plan_trip("安排杭州两天", requirements(), None, model, search, maps, None)
    assert result.plan is not None
    assert maps.lookup.call_count == 2
    assert len(model.seen_messages) == 6
    assert "研究子智能体" in model.seen_messages[1][0].content
    assert not any(getattr(m, "tool_calls", []) for m in model.seen_messages[1])
