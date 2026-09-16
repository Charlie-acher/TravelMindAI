"""
数据格式层：定义旅行需求的字段和校验规则。
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.budget import BudgetMoney

# 文本去掉首尾空白后不能为空。
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# 用户消息的类型，只允许以下几种值。
Intent = Literal["plan_trip", "modify_trip", "trip_question", "travel_info", "budget_only", "other"]
# 判断需求是否完整时，必须检查的字段。
RequiredField = Literal["destination", "days", "travelers", "total_budget"]


class TravelRequestExtraction(BaseModel):
    """旅行需求类：保存目的地、时间、人数、预算和偏好。"""

    model_config = ConfigDict(extra="forbid")

    intent: Intent = Field(description="规划、修改、追问、资料、仅预算或无关请求")
    destination: NonBlankText | None = Field(description="目的地；未知为null")
    origin: NonBlankText | None = Field(description="出发地；未知为null")
    start_date: date | None = Field(description="出发日期YYYY-MM-DD；不明确为null")
    end_date: date | None = Field(description="返回日期YYYY-MM-DD；不明确为null")
    days: int | None = Field(ge=2, le=5, strict=True, description="包含首尾日的2到5天")
    travelers: int | None = Field(ge=1, le=8, strict=True, description="全团1到8人")
    total_budget: BudgetMoney | None = Field(description="全团人民币预算，用金额字符串或null")
    pace: Literal["relaxed", "balanced", "intensive"] | None = Field(
        description="轻松、均衡、紧凑；未说明为null，不默认填均衡"
    )
    interests: list[NonBlankText] = Field(description="兴趣；未提及用空列表")
    dietary: list[NonBlankText] = Field(description="饮食偏好或禁忌；未提及用空列表")
    lodging_preferences: list[NonBlankText] = Field(description="住宿偏好；未提及用空列表")
    hard_constraints: list[NonBlankText] = Field(description="必须遵守的条件，如不能爬山")
    excluded_items: list[NonBlankText] = Field(description="明确不要的活动或地点")
    assumptions: list[NonBlankText] = Field(description="日期或金额推导依据；没有则用空列表")


    """预算校验函数：禁止把布尔值当作金额。"""

    @field_validator("total_budget", mode="before")
    @classmethod
    def reject_boolean_budget(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("总预算不能使用 true 或 false")
        return value


    """日期校验函数：检查日期先后、支持天数和填写天数是否一致。"""

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.start_date is not None and self.end_date is not None:
            duration = (self.end_date - self.start_date).days + 1
            if not 2 <= duration <= 5:
                raise ValueError("首尾日期必须按先后排列，且旅行时长为2到5天")
            if self.days is not None and self.days != duration:
                raise ValueError("日期计算出的天数与 days 不一致")
        return self


class RequirementResult(BaseModel):
    """需求处理结果类：保存旅行需求、缺失信息和补充问题。"""

    original_message: str  # 本次用户发送的原文。
    reference_date: date  # 解释“下周”等相对时间时所采用的上海日历日期。
    extraction: TravelRequestExtraction  # 已通过校验的旅行需求。
    missing_required_fields: list[RequiredField]  # 程序检查后得到的必填缺失项。
    clarification: str | None  # 集中追问；信息充分或非规划意图时为空。
    # 记录经过上下文规则校正后的本轮业务意图，不是模型未经处理的原始标签。
    # 没有旧档案时的modify_trip会转为plan_trip；已有档案的修改仍为modify_trip。
    message_intent: Intent | None = None
