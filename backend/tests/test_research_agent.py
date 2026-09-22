"""研究委派测试层：主规划模型实际调用独立子循环，核实结果回到原规划工具箱。"""

from unittest.mock import Mock

import pytest

from app.schemas.document.answer import GeoPoint, MapLookup
from app.schemas.document.search import SearchResult
from app.services.chat.events import ChatCancelled
from app.services.chat.metrics import measure_run
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.research import run_research
from app.services.itinerary.tools import PlanTools
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
    with measure_run("research-test") as run:
        result = plan_trip("安排杭州两天", requirements(), None, model, search, maps, None)
        steps = run.process_snapshot()["steps"]
    delegation = [step for step in steps if step["stage"] == "subagent"]
    assert len(delegation) == 1
    assert delegation[0]["status"] == "completed"
    assert delegation[0]["call_id"] and delegation[0]["elapsed_seconds"] >= 0
    assert result.plan is not None
    assert maps.lookup.call_count == 2
    assert len(model.seen_messages) == 6
    assert "研究子智能体" in model.seen_messages[1][0].content
    assert not any(getattr(m, "tool_calls", []) for m in model.seen_messages[1])


"""研究状态测试函数：未返回结果、异常和取消均不能冒充完成，也不公开异常内容。"""

@pytest.mark.parametrize("failure, expected", [
    (None, "failed"), (ValueError("private error"), "failed"),
    (ChatCancelled("private cancellation"), "cancelled"),
])
def test_research_incomplete_and_cancelled_status(monkeypatch, failure, expected):
    agent = Mock()
    agent.invoke.side_effect = failure
    monkeypatch.setattr("app.services.itinerary.research.create_agent", lambda *a, **k: agent)
    parent = PlanTools("杭州", Mock(), Mock(), None, None)
    with measure_run("research-state-test") as run:
        if failure:
            with pytest.raises(type(failure)):
                run_research("查询地点", parent, Mock(), "parent")
        else:
            run_research("查询地点", parent, Mock(), "parent")
        steps = run.process_snapshot()["steps"]
    assert len(steps) == 1 and steps[0]["status"] == expected
    assert steps[0]["call_id"] and "private" not in str(steps)
