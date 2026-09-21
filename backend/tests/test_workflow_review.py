"""测试层：独立审查不能放过篡改预算、日期或无效来源。"""

from datetime import date
from unittest.mock import Mock

import pytest

from app.services.itinerary.rules import build_plan
from tests.test_itinerary_agent import places, proposal, requirements


def candidate():
    return build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")],
                      places(), None, None)


@pytest.mark.parametrize("damage", ["budget", "date", "source", "overlap"])
def test_program_rejects_damage_before_model(damage):
    from app.services.itinerary.review import review_plan

    plan = candidate()
    if damage == "budget":
        plan.budget.total += 1
    elif damage == "date":
        plan.days[0].date = date(2026, 1, 1)
    elif damage == "source":
        plan.days[0].activities[0].place.sources[0].text = "无关原文"
    else:
        plan.days[0].activities.append(plan.days[1].activities.pop())
    model = Mock()
    result = review_plan("安排两天", requirements(), plan, None, model)
    assert result.decision == "revise" and result.issues
    model.generate_json.assert_not_called()


def test_model_review_is_independent_and_strict():
    from app.services.itinerary.review import review_plan

    model = Mock()
    model.generate_json.return_value = '{"decision":"pass","issues":[],"question":null}'
    assert review_plan("安排两天", requirements(), candidate(), None, model).decision == "pass"
    assert "独立审查" in model.generate_json.call_args.args[0][0]["content"]
    assert "json" in model.generate_json.call_args.args[0][0]["content"].lower()
    model.generate_json.return_value = '{"decision":"pass","issues":["不符合需求"]}'
    with pytest.raises(ValueError):
        review_plan("安排两天", requirements(), candidate(), None, model)
