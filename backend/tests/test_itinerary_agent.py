"""测试层：核对受控工具选择、行程规则和局部修改边界。"""

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.llm.client import ModelClientError
from app.schemas.document.answer import GeoPoint, MapLookup
from app.schemas.document.search import SearchResult
from app.schemas.itinerary import PlanPlace, PlanSource
from app.schemas.requirement.base import TravelRequestExtraction
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.rules import (
    ActivityProposal,
    DayProposal,
    build_plan,
    requested_days,
    unresolved_constraints,
)
from tests.helpers import RecordingModel, answer, evidence


def requirements(**changes):
    return TravelRequestExtraction.model_validate(answer(
        destination="杭州", days=2, travelers=2, total_budget="5000", **changes))


def places():
    return {f"p{i}": PlanPlace(id=f"p{i}", map=MapLookup(
        city="杭州", name=name, status="found", match_kind="poi",
        poi_id=f"poi{i}", location=GeoPoint(longitude=120.1, latitude=30.2)),
        sources=[PlanSource(id="k1", kind="knowledge", title="杭州资料", text=name)])
        for i, name in enumerate(["湖滨路步行街", "河坊街"], 1)}


def proposal(day, place, start="09:00", duration=90, transfer=0):
    return DayProposal(day=day, activities=[ActivityProposal(
        place_id=place, start_time=start, duration_minutes=duration,
        transport="transit", transfer_minutes=transfer)])


def test_program_calculates_budget_and_dates():
    plan = build_plan(requirements(start_date="2026-10-01", end_date="2026-10-02"),
                      [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    assert str(plan.days[1].date) == "2026-10-02"
    assert plan.budget.price_version == "demo-cny-v1"
    assert not plan.budget.over_budget and plan.warnings


def test_overlap_and_unknown_place_rejected():
    day = proposal(1, "p1")
    day.activities.append(ActivityProposal(place_id="p2", start_time="10:00",
                                          duration_minutes=60, transfer_minutes=30))
    with pytest.raises(ValueError, match="重叠|交通"):
        build_plan(requirements(), [day, proposal(2, "p2")], places(), None, None)
    with pytest.raises(ValueError, match="地点"):
        build_plan(requirements(), [proposal(1, "invented"), proposal(2, "p2")],
                   places(), None, None)


def test_local_patch_preserves_other_days_and_rejects_spill():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    new = build_plan(requirements(), [proposal(2, "p2", start="10:00")], places(), old, {2})
    assert old.days[0] == new.days[0] and old.days[1] != new.days[1]
    with pytest.raises(ValueError, match="指定"):
        build_plan(requirements(), [proposal(1, "p1")], places(), old, {2})
    assert requested_days("第二天改为十点出发，第一天保持不变", old, requirements()) == {2}
    assert requested_days("第二天改为十点出发第一天不变", old, requirements()) == {2}
    assert requested_days("第一天保持不变", old, requirements()) == set()


def test_exclusion_and_low_budget_rejected():
    with pytest.raises(ValueError, match="排除"):
        build_plan(requirements(excluded_items=["河坊街"]),
                   [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    req = requirements().model_copy(update={"total_budget": Decimal("10")})
    with pytest.raises(ValueError, match="预算"):
        build_plan(req, [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)


def test_explicit_start_is_verified_without_external_evidence():
    req = requirements(hard_constraints=["第二天11点开始游玩"])
    assert unresolved_constraints(req) == []
    with pytest.raises(ValueError, match="11:00"):
        build_plan(req, [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    assert build_plan(req, [proposal(1, "p1"), proposal(2, "p2", "11:00")],
                      places(), None, None).days[1].activities[0].start_time == "11:00"
    assert unresolved_constraints(requirements(hard_constraints=["必须全程无障碍"]))


"""截图回归：旧记录误存的舒适偏好不能阻止预算补齐后的首次规划。"""

def test_gentle_preferences_in_old_constraints_do_not_block_planning():
    from app.schemas.requirement.update import RequirementUpdate
    from app.services.requirement.merge import merge_requirements

    previous = requirements(hard_constraints=["少走路", "节奏舒适"])
    merged = merge_requirements(previous, RequirementUpdate.model_validate(answer(
        intent="modify_trip", total_budget="5000")))
    assert merged.pace == "relaxed"
    assert merged.hard_constraints == []
    assert "少走路" in merged.interests
    assert build_plan(merged, [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None).days


"""证据不足不等于条件冲突：先给可讨论草稿，但不能声称未查明要求已满足。"""

def test_unverified_constraint_allows_draft_with_specific_notice():
    plan = build_plan(requirements(hard_constraints=["必须全程无障碍"]),
        [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    assert any("必须全程无障碍" in warning and "未确认" in warning for warning in plan.warnings)


def test_empty_patch_recalculates_budget_and_preserves_activities():
    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                     places(), None, None)
    req = requirements().model_copy(update={"total_budget": Decimal("6000")})
    result = plan_trip("预算改6000，第一天保持不变", req, old,
                       RecordingModel(['{"days":[]}']).model, Mock(), Mock(), None)
    assert result.plan is not None and result.plan.days == old.days
    assert result.plan.budget.total_budget == Decimal("6000")


def test_agent_selects_tools_then_validates_real_tool_results():
    hit = evidence()
    hit.chunk.text += "河坊街位于杭州。"
    search, maps, web = Mock(), Mock(), Mock()
    search.search.return_value = SearchResult(items=[hit])
    maps.lookup.side_effect = lambda city, name: MapLookup(
        city=city, name=name, status="found", match_kind="poi", poi_id=name,
        location=GeoPoint(longitude=120.1, latitude=30.2))
    model = RecordingModel([json.dumps(v, ensure_ascii=False) for v in [
        {"tools": [{"tool": "knowledge_search", "query": "杭州景点"}]},
        {"tools": [{"tool": "map_lookup", "name": "湖滨路步行街", "source_ids": ["k1"]},
                   {"tool": "map_lookup", "name": "河坊街", "source_ids": ["k1"]}]},
        {"days": [proposal(1, "p1").model_dump(), proposal(2, "p2").model_dump()]},
    ]])
    result = plan_trip("安排两天行程", requirements(), None, model.model, search, maps, web)
    assert result.plan is not None and len(result.plan.days) == 2
    assert maps.lookup.call_count == 2 and web.search.call_count == 0
    assert len(model.calls) == 3


@pytest.mark.parametrize("agent_kind", ["plan", "nearby"])
def test_planner_does_not_checkpoint_mutable_tool_closures_inside_outer_graph(agent_kind):
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        done: bool

    old = build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    model = RecordingModel(['{"days":[]}'])

    def generate(state):
        if agent_kind == "nearby":
            from app.services.chat.dining import handle_nearby
            from tests.test_nearby import native
            result = handle_nearby("继续规划", None, [], Mock(), native(("continue_chat", {})))
            return {"done": result is None}
        result = plan_trip("第一天不变", requirements(), old, model.model, Mock(), Mock(), None)
        return {"done": result.plan is not None}

    saver = InMemorySaver()
    graph = StateGraph(State)
    graph.add_node("generate", generate)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    config = {"configurable": {"thread_id": "outer-test"}}
    assert graph.compile(checkpointer=saver).invoke(
        {"done": False}, config, durability="sync")["done"]
    assert all(item.config["configurable"]["checkpoint_ns"] == "" for item in saver.list(None))


def test_tool_budget_is_bounded_and_foreign_names_never_queried():
    search, maps = Mock(), Mock()
    raw = json.dumps({"tools": [{"tool": "map_lookup", "name": "编造景点",
                                 "source_ids": ["missing"]}]})
    model = RecordingModel([raw] * 6)
    with pytest.raises(ModelClientError, match="未开放"):
        plan_trip("安排两天", requirements(), None, model.model, search, maps, None)
    assert len(model.calls) <= 6 and maps.lookup.call_count == 0
