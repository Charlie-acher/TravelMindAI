"""
预算服务层：按固定演示价格计算旅行费用和预算余额。
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# 用字符串创建精确金额，避免浮点数计算误差。
CENT = Decimal("0.01")
CONTINGENCY_RATE = Decimal("0.10")
PRICE_VERSION = "demo-cny-v1"


class BudgetValidationError(ValueError):
    """预算校验异常类：表示输入不符合预算规则。"""


@dataclass(frozen=True)
class BudgetEstimate:
    """预算估算结果类：保存费用明细、总费用和预算余额。"""

    # 行程总天数，当前支持 2～5 天
    days: int
    # 同行总人数，当前支持 1～8 人；本次预算计算的是所有人的全程费用。
    travelers: int
    # 住宿晚数，按 days - 1 计算；例如三天行程需要住宿两晚。
    nights: int
    # 每晚需要的房间数，每间最多住 2 人并向上取整；例如 3 人需要 2 间房。
    rooms: int
    # 住宿档位：economy 表示经济型，comfort 表示舒适型；目前只影响每晚房价。
    lodging: str
    # 用户提供的全团、全程预算上限，单位为人民币元；不是每个人的预算。
    total_budget: Decimal
    # 本次使用的价格规则版本，如 demo-cny-v1，便于识别计算依据；不是实时报价日期。
    price_version: str
    # 各项目的演示单价，单位为人民币元。
    unit_prices: dict[str, Decimal]
    # 全团全程的分类费用
    costs: dict[str, Decimal]
    # 分类费用小计；尚未加预留金。
    subtotal: Decimal
    # 预留金比例，用小数表示
    contingency_rate: Decimal
    # 应对额外开销的预留金，等于 subtotal × contingency_rate，四舍五入到分。
    contingency: Decimal
    # 全团、全程的预计总费用，等于 subtotal + contingency，单位为人民币元。
    total: Decimal
    # 预算余额，等于 total_budget - total，单位为人民币元；负数表示超支金额。
    remaining: Decimal
    # 是否超出预算：remaining < 0 时为 True；刚好花完预算时为 False。
    over_budget: bool
    # 本次估算采用的前提说明，例如固定演示单价、每间最多 2 人等。
    # tuple[str, ...] 表示每个元素都是字符串，创建后不能增删或替换其中的元素。
    assumptions: tuple[str, ...]


"""预算计算函数：根据天数、人数和住宿档位估算全团费用。"""

def calculate_budget(
    *, days: int, travelers: int, total_budget: Decimal, lodging: str = "economy"
) -> BudgetEstimate:
    # bool 是 Python int 的子类，所以这里用 type(...) is int 排除 True/False。
    if type(days) is not int or not 2 <= days <= 5:
        raise BudgetValidationError("行程天数必须是2到5之间的整数")
    if type(travelers) is not int or not 1 <= travelers <= 8:
        raise BudgetValidationError("同行人数必须是1到8之间的整数")
    if lodging not in ("economy", "comfort"):
        raise BudgetValidationError("住宿档位仅支持 经济型 或 舒适型")
    # 先检查有限数，再比较大小，避免 NaN 参与比较时产生 Decimal 异常。
    if not isinstance(total_budget, Decimal) or not total_budget.is_finite():
        raise BudgetValidationError("总预算必须是有限的 Decimal 金额")
    if not Decimal("0") < total_budget <= Decimal("9999999999.99"):
        raise BudgetValidationError("总预算必须大于0且不超过9999999999.99元")
    if total_budget != total_budget.quantize(CENT):
        raise BudgetValidationError("总预算最多精确到分，即两位小数")

    # // 是整数除法。(人数+1)//2 得到每间最多2人的向上取整房间数。
    rooms = (travelers + 1) // 2
    nights = days - 1
    person_days = travelers * days

    # 键名明确单价的计费单位。住宿档位只改变房价，不隐式改变餐饮等价格。
    unit_prices = {
        "room_per_night": Decimal("200.00") if lodging == "economy" else Decimal("400.00"),
        "intercity_per_person_trip": Decimal("400.00"),  # 城际交通费
        "local_transport_per_person_day": Decimal("30.00"),
        "meals_per_person_day": Decimal("80.00"),
        "tickets_per_person_day": Decimal("60.00"),
    }
    costs = {
        "accommodation": unit_prices["room_per_night"] * rooms * nights,  # 住宿
        "intercity_transport": unit_prices["intercity_per_person_trip"] * travelers, # 城际交通费：单价乘人数。
        "local_transport": unit_prices["local_transport_per_person_day"] * person_days,
        "meals": unit_prices["meals_per_person_day"] * person_days,
        "tickets_and_activities": unit_prices["tickets_per_person_day"] * person_days,
    }

    # 给 sum 一个 Decimal 起始值，使求和保持在 Decimal 类型中。
    subtotal = sum(costs.values(), Decimal("0.00"))
    # ROUND_HALF_UP 是金额常用的四舍五入规则，预留金明确保留两位小数。
    contingency = (subtotal * CONTINGENCY_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    total = subtotal + contingency
    remaining = total_budget.quantize(CENT) - total

    return BudgetEstimate(
        days=days,
        travelers=travelers,
        nights=nights,
        rooms=rooms,
        lodging=lodging,
        total_budget=total_budget.quantize(CENT),
        price_version=PRICE_VERSION,
        unit_prices=unit_prices,
        costs=costs,
        subtotal=subtotal,
        contingency_rate=CONTINGENCY_RATE,
        contingency=contingency,
        total=total,
        remaining=remaining,
        over_budget=remaining < 0,
        assumptions=(
            "所有单价是固定演示估算，不是实时预订报价；币种为人民币。",
            "按每间最多2人分房，不区分成人儿童；不考虑加床或单住偏好。",
            "住宿晚数等于行程天数减1；城际交通为每人全程估算。",
            "市内交通、餐饮、门票按每人每天计；预留金为分类小计的10%。",
        ),
    )
