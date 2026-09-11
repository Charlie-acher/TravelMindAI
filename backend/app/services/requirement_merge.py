"""
需求合并层：把本次修改合并到已有旅行需求。
"""

from datetime import timedelta
from typing import Any

from app.schemas.requirement import TravelRequestExtraction
from app.schemas.requirement_update import ListField, RequirementUpdate

SCALAR_FIELDS = (
    "destination",
    "origin",
    "start_date",
    "end_date",
    "days",
    "travelers",
    "total_budget",
    "pace",
)
LIST_FIELDS: tuple[ListField, ...] = (
    "interests",
    "dietary",
    "lodging_preferences",
    "hard_constraints",
    "excluded_items",
)


"""日期调整函数：根据本次修改更新起止日期和旅行天数。"""

def reconcile_dates(data: dict[str, Any], update: RequirementUpdate) -> None:
    # 动态字段字典仅在本模块内部使用，最终统一由TravelRequestExtraction校验。
    start, end, days = data["start_date"], data["end_date"], data["days"]
    clearing = set(update.clear_fields)
    if clearing.intersection({"start_date", "end_date"}):
        return
    if "days" in clearing:
        # 用户明确表示天数未定时，旧结束日期也不能继续暗示原来的天数。
        data["end_date"] = None
        return
    if update.start_date is not None and update.end_date is not None:
        data["days"] = (end - start).days + 1
    elif update.end_date is not None and update.start_date is None and start is not None:
        data["days"] = (end - start).days + 1
        if update.days is not None and update.days != data["days"]:
            raise ValueError("修改后的结束日期与天数冲突")
    elif (
        start is not None
        and days is not None
        and (update.days is not None or update.start_date is not None)
    ):
        data["end_date"] = start + timedelta(days=days - 1)
        data["assumptions"].append("按开始日期和旅行天数重新计算结束日期，包含首尾日。")
    elif start is not None and end is not None and days is None:
        data["days"] = (end - start).days + 1


"""需求合并函数：保留未修改内容，应用新增、覆盖、清空和删除。"""

def merge_requirements(
    previous: TravelRequestExtraction | None,
    update: RequirementUpdate,
) -> TravelRequestExtraction:
    extracted = update.model_dump(exclude={"clear_fields", "remove_items"})
    if previous is not None and update.intent not in {"plan_trip", "modify_trip"}:
        return previous.model_copy(deep=True)
    data = previous.model_dump() if previous is not None else extracted.copy()
    # 当前表格只展示本轮推导，避免旧的“人均预算×人数”成为修改后的错误依据。
    data["assumptions"] = list(update.assumptions)
    data["intent"] = "plan_trip" if update.intent == "modify_trip" else update.intent
    for field in SCALAR_FIELDS:
        if extracted[field] is not None:
            data[field] = extracted[field]
    for field in LIST_FIELDS:
        old_items = list(data[field])
        removed = update.remove_items.get(field, [])
        if any(item not in old_items for item in removed):
            raise ValueError(f"删除{field}时必须使用历史中存在的准确条目")
        data[field] = list(
            dict.fromkeys([item for item in old_items if item not in removed] + extracted[field])
        )
    for field in update.clear_fields:
        data[field] = [] if field in LIST_FIELDS else None
    if previous is not None:
        reconcile_dates(data, update)
    # 新字典中的Decimal和date仍保持Python类型，无须先转JSON再转回来。
    return TravelRequestExtraction.model_validate(data)
