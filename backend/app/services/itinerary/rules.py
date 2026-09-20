"""行程规则层：核对时间、地点、预算与修改范围，不让模型自行宣布校验通过。"""

import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.budget import BudgetSummary
from app.schemas.itinerary import PlanDay, PlannedActivity, PlanPlace, TravelPlan
from app.schemas.requirement.base import TravelRequestExtraction
from app.services.budget_service import calculate_budget


class ActivityProposal(BaseModel):
    """活动建议类：模型只能选择已查到的地点编号，不能填写名称、价格或坐标。"""

    model_config = ConfigDict(extra="forbid")
    place_id: str = Field(min_length=1, max_length=40)
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(ge=30, le=240, strict=True)
    transport: Literal["walk", "transit", "taxi"] = "transit"
    transfer_minutes: int = Field(ge=0, le=120, strict=True)


class DayProposal(BaseModel):
    """单日建议类：局部修改时仅提交需要改变的天。"""

    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=5, strict=True)
    activities: list[ActivityProposal] = Field(min_length=1, max_length=5)


"""修改范围函数：明确点名某天时限定修改范围，日期或天数改变时需重新规划全程。"""


def requested_days(message: str, old: TravelPlan | None,
                   requirements: TravelRequestExtraction) -> set[int] | None:
    if (old is None or len(old.days) != requirements.days
            or old.destination != requirements.destination
            or old.days[0].date != requirements.start_date):
        return None
    numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    found = re.findall(r"第\s*([一二三四五1-5])\s*天", message)
    if not found:
        return None
    preserved = re.findall(
        r"第\s*([一二三四五1-5])\s*天[^，,。；;第]*?(?:不变|不动|不用改|不要改|保持原样)", message)
    return {numbers[item] if item in numbers else int(item)
            for item in found if item not in preserved}


"""硬条件检查函数：没有路线和无障碍证据时，不把文字承诺当作核实。"""


def unresolved_constraints(requirements: TravelRequestExtraction) -> list[str]:
    # 开始时间和明确排除地点可直接核对；无障碍、路线时长等仍需外部依据。
    return [item for item in requirements.hard_constraints
            if item not in requirements.excluded_items and start_constraint(item) is None]


"""开始时间读取函数：只接受明确某天几点开始，不把到达、返程或模糊时段误当开始时间。"""


def start_constraint(text: str) -> tuple[int, int] | None:
    matched = re.fullmatch(
        r"第([一二三四五1-5])天(?:改为|改成|从)?(上午|早上|下午|晚上)?"
        r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})(?:点(半|[0-9]{1,2}分?)?|:([0-9]{2}))"
        r"(?:开始游玩|开始游览|开始|出发)[。！!]?", re.sub(r"\s", "", text))
    if matched is None:
        return None
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    day, period, hour_text, minute_text, colon_minute = matched.groups()
    if hour_text.isdigit():
        hour = int(hour_text)
    elif "十" in hour_text:
        tens, _, ones = hour_text.partition("十")
        if tens not in ("", "一", "二") or ones not in ("", *digits):
            return None
        hour = (digits.get(tens, 1) * 10) + digits.get(ones, 0)
    elif hour_text in digits:
        hour = digits[hour_text]
    else:
        return None
    minute = 30 if minute_text == "半" else int((colon_minute or minute_text or "0").rstrip("分"))
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return (int(day) if day.isdigit() else digits[day], hour * 60 + minute)


"""行程构造函数：合并指定天并检查全程，任何错误都不能替换已有草稿。"""


def build_plan(requirements: TravelRequestExtraction, proposals: list[DayProposal],
               places: dict[str, PlanPlace], old: TravelPlan | None,
               target_days: set[int] | None) -> TravelPlan:
    count, travelers, money = requirements.days, requirements.travelers, requirements.total_budget
    if count is None or travelers is None or money is None or not requirements.destination:
        raise ValueError("请先补齐目的地、天数、人数和总预算")
    if unresolved_constraints(requirements):
        raise ValueError("这些硬条件还缺少可核实依据："
                         + "、".join(unresolved_constraints(requirements)))
    if len({day.day for day in proposals}) != len(proposals):
        raise ValueError("同一天不能重复提交")
    if target_days is not None and {day.day for day in proposals} != target_days:
        raise ValueError("本次只能修改用户指定的天，请保留其他天")
    days = ({day.day: day.model_copy(deep=True) for day in old.days}
            if old and target_days is not None else {})
    for proposal in proposals:
        activities: list[PlannedActivity] = []
        for proposed_activity in proposal.activities:
            place = places.get(proposed_activity.place_id)
            if (place is None or place.map.status != "found" or place.map.match_kind != "poi"
                    or place.map.location is None or place.map.city != requirements.destination):
                raise ValueError("活动必须引用本目的地已核对的具体地点")
            activities.append(PlannedActivity(
                place=place, **proposed_activity.model_dump(exclude={"place_id"})))
        days[proposal.day] = PlanDay(day=proposal.day, activities=activities)
    if set(days) != set(range(1, count + 1)):
        raise ValueError("逐日安排必须完整覆盖旅行天数")
    seen: set[str] = set()
    for day in days.values():
        day.date = (requirements.start_date + timedelta(days=day.day - 1)
                    if requirements.start_date else None)
        previous_end, total_minutes = 0, 0
        for index, activity in enumerate(day.activities):
            place = activity.place
            identity = place.map.poi_id or place.map.name
            if identity in seen:
                raise ValueError(f"地点{place.map.name}（{place.id}）全程重复了。"
                                 "请让每个地点只出现一次；地点少时每天安排一个即可，不必排满。")
            seen.add(identity)
            if any(item in place.map.name or item in (place.map.matched_name or "")
                   for item in requirements.excluded_items):
                raise ValueError("行程中包含用户明确排除的地点")
            hour, minute = map(int, activity.start_time.split(":"))
            start = hour * 60 + minute
            end = start + activity.duration_minutes
            if start < 8 * 60 or end > 21 * 60:
                raise ValueError("活动应安排在08:00至21:00之间")
            if index and start < previous_end + activity.transfer_minutes:
                raise ValueError("活动时间重叠或未预留交通时间")
            if index and activity.transfer_minutes < 30:
                raise ValueError("不同地点之间至少预留30分钟，实际路线仍待核实")
            previous_end = end
            total_minutes += activity.duration_minutes + activity.transfer_minutes
        cap = {"relaxed": 360, "balanced": 480, "intensive": 600}.get(requirements.pace or "", 480)
        if total_minutes > cap:
            raise ValueError("当天活动和交通预留超过所选节奏的时间上限")
    for constraint in requirements.hard_constraints:
        if start_rule := start_constraint(constraint):
            day_number, required_start = start_rule
            if day_number not in days:
                raise ValueError("开始时间条件超出了本次旅行天数，请先调整条件")
            actual = days[day_number].activities[0].start_time
            hour, minute = map(int, actual.split(":"))
            if hour * 60 + minute != required_start:
                raise ValueError(f"第{day_number}天必须按要求在"
                                 f"{required_start // 60:02d}:{required_start % 60:02d}开始")
    lodging = "comfort" if any("舒适" in p for p in requirements.lodging_preferences) else "economy"
    budget = BudgetSummary.model_validate(calculate_budget(
        days=count, travelers=travelers, total_budget=money, lodging=lodging))
    if budget.over_budget:
        raise ValueError(f"按当前演示单价估算需{budget.total}元，超过总预算；"
                         "请调整天数、人数或预算")
    return TravelPlan(
        title=f"{requirements.destination}{count}日行程草稿", destination=requirements.destination,
        days=[days[i] for i in range(1, count + 1)], budget=budget,
        warnings=[
            "门票、机票与火车票：待查询；请按实际出行日期核对票价、余票和预约。",
            "天气：待查询；未确定出行日期时不能把当前天气当作旅行期间预报。",
            "这是待确认的行程草稿。活动时间为建议，开放时间、预约和门票需出行前核实。",
            "交通方式与预留时间仅作安排建议，尚未查询实际路线，不能保证步行时长或到达时间。",
            "预算采用demo-cny-v1演示单价，含往返城际交通、住宿、餐饮、市内交通、门票及预备金；不是实时报价。",
        ],
    )
