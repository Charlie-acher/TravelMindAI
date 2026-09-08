"""HTTP 请求和响应的数据结构，类似 Java Controller 使用的 DTO。

Pydantic 在入口把 JSON 转换为 Python 类型并校验；返回时把对象转换为 JSON。
计算规则仍在 domain/budget.py，DTO 不负责重复算钱。
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Annotated 为类型附加校验规则。金额最多12位有效数字，其中最多2位小数。
# gt=0 要求严格大于0；NaN/Infinity 等非有限数不属于可接受预算。
BudgetMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]

"""
预算输入类
前端发送的预算条件；未填写的住宿档位默认 economy。
"""
class BudgetInput(BaseModel):
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

    """
    转换前拒绝布尔值，避免把 true 理解为1元；其他转换交给 Decimal 校验。
    """
    @field_validator("total_budget", mode="before")
    @classmethod
    def reject_boolean_budget(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("总预算不能使用 true 或 false")
        return value

    """
    单个字段解析完成后，再校验多个字段之间的关系。
    Self 表示返回当前类的对象。只有校验全部通过，FastAPI 才进入路由函数。
    """
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

"""
API 对外展示的预算结果；金额字段在 JSON 中序列化为 Decimal 字符串。
"""
class BudgetSummary(BaseModel):
    # 允许从领域 dataclass 的属性构造 DTO，而非要求领域对象继承 BaseModel。
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

"""
最外层响应：业务结果和请求编号分开，方便前端展示与排查问题。
"""
class BudgetResponse(BaseModel):
    budget: BudgetSummary
    request_id: str

"""
统一错误内容；details 只放安全的字段说明，不回传原始请求或堆栈。
"""
class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] = Field(default_factory=dict)

"""
让 Swagger 中声明的错误格式与实际响应一致。
"""
class ErrorResponse(BaseModel):
    error: ErrorInfo
    request_id: str
