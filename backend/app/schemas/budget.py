"""
数据格式层：定义预算接口的请求、响应和校验规则。
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Annotated 为类型附加校验规则。金额最多12位有效数字，其中最多2位小数。
# gt=0 要求严格大于0；NaN/Infinity 等非有限数不属于可接受预算。
BudgetMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]


class BudgetInput(BaseModel):
    """预算请求类：接收天数、人数、总预算和住宿条件。"""

    # strict=True 禁止把 True、2.5、"3" 悄悄当作合法整数。
    # ge=2, le=5 表示 2 到 5 天，含首尾两天。 2<=days<=5
    days: int = Field(ge=2, le=5, strict=True, description="含首尾两天，支持2到5天")
    travelers: int = Field(ge=1, le=8, strict=True, description="全团人数")
    total_budget: BudgetMoney
    lodging: Literal["economy", "comfort"] = "economy" # 限定只能使用这两个值
    # date 自动解析 ISO 日期；None 表示还未决定日期，只按天数估算。
    start_date: date | None = None
    end_date: date | None = None

    model_config = ConfigDict(
        extra="forbid",  # 字段拼错时直接报错，不让未知字段悄悄失效。
        json_schema_extra={
            "examples": [
                {"days": 3, "travelers": 2, "total_budget": "5000.00", "lodging": "economy"}
            ]
        },
    )

    """预算校验函数：禁止把布尔值当作金额。"""

    @field_validator("total_budget", mode="before")
    @classmethod
    def reject_boolean_budget(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("总预算不能使用 true 或 false")
        return value

    """日期校验函数：检查起止日期是否成对填写，并与旅行天数一致。"""

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("开始日期和结束日期必须同时填写或同时留空")
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("结束日期不能早于开始日期")
            if (self.end_date - self.start_date).days + 1 != self.days:
                raise ValueError("首尾日期计算出的天数必须与 days 一致")
        return self

"""预算结果类：保存各项费用、总费用和预算余额。"""
class BudgetSummary(BaseModel):
    # 允许直接读取预算计算结果对象的字段。
    model_config = ConfigDict(from_attributes=True)

    days: int
    travelers: int
    nights: int
    rooms: int
    lodging: str
    # 显式给出文档示例，避免 Swagger 根据 Decimal 正则生成难读的超长随机数字。
    total_budget: Decimal = Field(examples=["5000.00"])
    price_version: str
    unit_prices: dict[str, Decimal] = Field(examples=[{"room_per_night": "200.00"}])
    costs: dict[str, Decimal] = Field(examples=[{"accommodation": "400.00"}])
    subtotal: Decimal = Field(examples=["2220.00"])
    contingency_rate: Decimal = Field(examples=["0.10"])
    contingency: Decimal = Field(examples=["222.00"])
    total: Decimal = Field(examples=["2442.00"])
    remaining: Decimal = Field(examples=["2558.00"])
    over_budget: bool
    assumptions: list[str]

"""预算响应类：返回预算结果和请求编号。"""
class BudgetResponse(BaseModel):
    budget: BudgetSummary
    request_id: str

