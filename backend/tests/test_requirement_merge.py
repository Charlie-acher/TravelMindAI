"""多轮合并的考卷：模型已提取出的新信息，应该怎样改变旧需求。

这些测试只运行Python规则，不联网。重点检查信息不丢失、删除必须明确、
日期修改不遗留旧时间，以及失败时不能破坏上一轮对象。
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.update import RequirementUpdate
from app.services.requirement.merge import merge_requirements

"""创建一份部分修改；没提到的标量用None、列表用[]，表示保持原样。"""

def update(**changes: object) -> RequirementUpdate:
    payload: dict[str, object] = {
        "intent": "modify_trip",
        "destination": None,
        "origin": None,
        "start_date": None,
        "end_date": None,
        "days": None,
        "travelers": None,
        "total_budget": None,
        "pace": None,
        "interests": [],
        "dietary": [],
        "lodging_preferences": [],
        "hard_constraints": [],
        "excluded_items": [],
        "assumptions": [],
    }
    return RequirementUpdate.model_validate(payload | changes)


"""每个测试使用独立旧需求，包含兴趣和两条硬限制，便于检查保留与撤销。"""

@pytest.fixture
def previous() -> TravelRequestExtraction:
    return TravelRequestExtraction.model_validate(
        update(
            intent="plan_trip",
            destination="杭州",
            origin="上海",
            days=3,
            travelers=2,
            total_budget="6000.00",
            interests=["美食"],
            hard_constraints=["不爬山", "必须有电梯"],
        ).model_dump(exclude={"clear_fields", "remove_items"})
    )


"""只改人数，城市、预算、旧限制均保留，旧对象本身不被改写。"""

def test_override_only_mentioned_fields(previous: TravelRequestExtraction) -> None:
    before = previous.model_dump()
    merged = merge_requirements(previous, update(travelers=3))
    assert merged.travelers == 3
    assert merged.destination == "杭州"
    assert merged.total_budget == 6000
    assert merged.hard_constraints == ["不爬山", "必须有电梯"]
    assert merged.intent == "plan_trip"
    assert previous.model_dump() == before


"""再说预算不变不能清空任何已有字段。"""

def test_budget_confirmation_keeps_other_fields(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(previous, update(total_budget="6000"))
    assert merged.days == 3 and merged.travelers == 2 and merged.origin == "上海"


"""新增兴趣按原顺序合并去重，明确删除只影响指定旧条目。"""

def test_merge_lists_and_remove_one_constraint(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(
        previous,
        update(
            interests=["美食", "博物馆"],
            remove_items={"hard_constraints": ["不爬山"]},
        ),
    )
    assert merged.interests == ["美食", "博物馆"]
    assert merged.hard_constraints == ["必须有电梯"]
    assert previous.hard_constraints == ["不爬山", "必须有电梯"]


"""用户明确说预算没定或清空兴趣时，允许清空，后续服务会重新追问预算。"""

def test_clear_scalar_and_list(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(previous, update(clear_fields=["total_budget", "interests"]))
    assert merged.total_budget is None and merged.interests == []
    assert merged.travelers == 2


"""不认识的字段、同一字段同时清空又赋值、同时新增和删除均拒绝。"""

@pytest.mark.parametrize(
    "changes",
    [
        {"clear_fields": ["intent"]},
        {"remove_items": {"travelers": ["2"]}},
        {"clear_fields": ["travelers"], "travelers": 3},
        {"interests": ["美食"], "remove_items": {"interests": ["美食"]}},
    ],
)
def test_conflicting_updates_are_invalid(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        update(**changes)


"""模型删错名称必须修复，不能静默保留限制却回复用户已删除。"""

def test_unknown_removal_fails_without_mutating_old(previous: TravelRequestExtraction) -> None:
    with pytest.raises(ValueError, match="删除"):
        merge_requirements(previous, update(remove_items={"hard_constraints": ["不登山"]}))
    assert previous.hard_constraints == ["不爬山", "必须有电梯"]


"""改旅行天数但未给日期时，保留开始日并重新计算结束日，避免旧天数冲突。"""

def test_duration_change_updates_end_date(previous: TravelRequestExtraction) -> None:
    previous = previous.model_copy(
        update={
            "start_date": date(2026, 10, 1),
            "end_date": date(2026, 10, 3),
        }
    )
    merged = merge_requirements(previous, update(days=4))
    assert merged.start_date == date(2026, 10, 1)
    assert merged.end_date == date(2026, 10, 4)
    assert merged.days == 4
    assert merged.assumptions


"""明确改首尾日期时，应重新计算天数，不能保留旧的3天造成矛盾。"""

def test_new_date_range_replaces_old_duration(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(
        previous,
        update(
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 8),
        ),
    )
    assert merged.days == 4


"""只改开始日时，按已有旅行天数平移结束日。"""

def test_new_start_moves_trip(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(previous, update(start_date=date(2026, 10, 5)))
    assert merged.end_date == date(2026, 10, 7)


"""清空日期时保留旅行天数，但不能从旧结束日期悄悄恢复出发日期。"""

def test_explicit_date_clear_wins(previous: TravelRequestExtraction) -> None:
    previous = previous.model_copy(
        update={
            "start_date": date(2026, 10, 1),
            "end_date": date(2026, 10, 3),
        }
    )
    merged = merge_requirements(previous, update(clear_fields=["start_date", "end_date"]))
    assert merged.start_date is None and merged.end_date is None and merged.days == 3


"""无法支持的新天数或矛盾日期不能污染旧对象。"""

def test_merged_range_is_validated(previous: TravelRequestExtraction) -> None:
    previous = previous.model_copy(
        update={
            "start_date": date(2026, 10, 1),
            "end_date": date(2026, 10, 3),
        }
    )
    with pytest.raises(ValidationError):
        merge_requirements(previous, update(end_date=date(2026, 10, 10)))
    assert previous.days == 3


"""已有旅行时，闲聊或不支持的请求不能把旧目的地改掉。"""

def test_non_edit_intent_preserves_previous(previous: TravelRequestExtraction) -> None:
    merged = merge_requirements(previous, update(intent="other", destination="北京"))
    assert merged == previous and merged is not previous


"""历史推导可能已经失效，当前结果只携带本轮推导，历史留给后续消息存储。"""

def test_old_assumptions_are_not_current_facts(previous: TravelRequestExtraction) -> None:
    previous = previous.model_copy(update={"assumptions": ["原先按每人3000元推导"]})
    merged = merge_requirements(previous, update(travelers=3))
    assert merged.total_budget == 6000 and merged.assumptions == []
