"""预算领域测试：先用手算结果规定规则，再编写计算函数。

测试不启动网页、不调用模型，类似单独测试 Java Service 中的计算方法。
金额用 Decimal("金额")，避免先创建 float 再带入二进制小数误差。
"""

from decimal import Decimal

import pytest

from app.services.budget_service import BudgetValidationError, calculate_budget

"""经济档：住宿400+城际800+市内180+餐饮480+门票360=2220，再加222预留金。"""

def test_two_people_three_days_matches_hand_calculation() -> None:
    result = calculate_budget(days=3, travelers=2, total_budget=Decimal("5000"))

    # assert 断言 条件不成立就报错
    assert result.rooms == 1
    assert result.nights == 2
    # Decimal 预算函数
    assert result.costs == {
        "accommodation": Decimal("400.00"),
        "intercity_transport": Decimal("800.00"),
        "local_transport": Decimal("180.00"),
        "meals": Decimal("480.00"),
        "tickets_and_activities": Decimal("360.00"),
    }
    assert result.subtotal == Decimal("2220.00")
    # contingency 应急储备
    assert result.contingency == Decimal("222.00")
    assert result.total == Decimal("2442.00")
    assert result.remaining == Decimal("2558.00")
    assert result.over_budget is False
    assert result.price_version == "demo-cny-v1"
    assert result.unit_prices["room_per_night"] == Decimal("200.00")


"""单人也需一间房；奇数人数不能少订一间；8 人边界仍能正确计费。"""

@pytest.mark.parametrize(
    ("travelers", "rooms", "total"),
    [(1, 1, "1441.00"), (3, 2, "3883.00"), (8, 4, "9768.00")],
)
def test_room_count_rounds_up_without_charging_per_person(
    travelers: int, rooms: int, total: str
) -> None:
    result = calculate_budget(days=3, travelers=travelers, total_budget=Decimal("20000"))

    assert result.rooms == rooms
    assert result.total == Decimal(total)


"""舒适档每间每晚400元；其他分类不因住宿档位变化。"""

def test_comfort_changes_only_accommodation_rate() -> None:
    result = calculate_budget(days=3, travelers=2, total_budget=Decimal("5000"), lodging="comfort")
    assert result.costs["accommodation"] == Decimal("800.00")
    assert result.total == Decimal("2882.00")


"""恰好够用不算超支；差一分钱也应准确反映，不把负余额改成0。"""

@pytest.mark.parametrize(
    ("budget", "remaining", "over_budget"),
    [("2442", "0.00", False), ("2441.99", "-0.01", True), ("2442.01", "0.01", False)],
)
def test_exact_and_one_cent_budget_boundaries(
    budget: str, remaining: str, over_budget: bool
) -> None:
    result = calculate_budget(days=3, travelers=2, total_budget=Decimal(budget))
    assert result.remaining == Decimal(remaining)
    assert result.over_budget is over_budget


"""首版只支持2到5天；晚数必须跟天数同步变化。"""

@pytest.mark.parametrize(("days", "total"), [(2, "1848.00"), (5, "3630.00")])
def test_supported_day_boundaries(days: int, total: str) -> None:
    result = calculate_budget(days=days, travelers=2, total_budget=Decimal("10000"))
    assert result.nights == days - 1
    assert result.total == Decimal(total)


"""即使绕过 API 直接调用函数，也不能跳过业务范围和金额校验。"""

@pytest.mark.parametrize(
    ("days", "travelers", "budget", "lodging"),
    [
        (1, 2, "5000", "economy"),
        (6, 2, "5000", "economy"),
        (True, 2, "5000", "economy"),
        (3, 0, "5000", "economy"),
        (3, 9, "5000", "economy"),
        (3, True, "5000", "economy"),
        (3, 2, "0", "economy"),
        (3, 2, "-1", "economy"),
        (3, 2, "NaN", "economy"),
        (3, 2, "Infinity", "economy"),
        (3, 2, "10000000000", "economy"),
        (3, 2, "5000.001", "economy"),
        (3, 2, "5000", "unknown"),
    ],
)
def test_domain_rejects_invalid_inputs(
    days: int, travelers: int, budget: str, lodging: str
) -> None:
    with pytest.raises(BudgetValidationError):
        calculate_budget(
            days=days, travelers=travelers, total_budget=Decimal(budget), lodging=lodging
        )


"""不使用随机数或模型；相同输入必须返回相同结果，包括价格版本。"""

def test_repeated_calculation_is_identical() -> None:
    first = calculate_budget(days=3, travelers=2, total_budget=Decimal("5000"))
    second = calculate_budget(days=3, travelers=2, total_budget=Decimal("5000"))
    assert first == second
