"""需求抽取的业务测试：用假模型控制输出，不花 API 费用。

这些用例检查我们自己的规则：缺什么就问什么、日期不能矛盾、最多修复一次。
假模型只代替网络，真正的 JSON 解析、Pydantic 校验和追问逻辑仍然运行。
"""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.requirement.base import TravelRequestExtraction
from app.services.requirement.extract import RequirementExtractionError, extract_requirements
from tests.helpers import RecordingModel as FakeModel

"""提供一份完整模型输出；每个测试拿到新字典，修改不会影响其他测试。"""

@pytest.fixture
def complete_payload() -> dict[str, object]:
    return {
        "intent": "plan_trip",
        "destination": "杭州",
        "origin": "上海",
        "start_date": None,
        "end_date": None,
        "days": 3,
        "travelers": 2,
        "total_budget": "5000.00",
        "pace": None,
        "interests": ["美食"],
        "dietary": [],
        "lodging_preferences": [],
        "hard_constraints": [],
        "excluded_items": [],
        "assumptions": [],
    }


"""完整输入无需追问；钱在 JSON 中仍是字符串，原消息和参考日期可追溯。"""

def test_complete_request(complete_payload: dict[str, object]) -> None:
    model = FakeModel([json.dumps(complete_payload)])
    result = extract_requirements(
        "从上海去杭州玩三天，两个人，总预算五千，喜欢美食",
        model,
        reference_date=date(2026, 9, 9),
    )
    assert result.extraction.destination == "杭州"
    assert result.model_dump(mode="json")["extraction"]["total_budget"] == "5000.00"
    assert result.missing_required_fields == []
    assert result.clarification is None
    assert result.reference_date == date(2026, 9, 9)
    assert len(model.calls) == 1
    assert "2026-09-09" in model.calls[0][0]["content"]
    assert model.calls[0][1]["content"] == result.original_message


"""未知预算和人数保持为空，一次问完；未提到的出发地不强制追问。"""

def test_missing_fields_are_computed_together(complete_payload: dict[str, object]) -> None:
    complete_payload.update(origin=None, days=None, travelers=None, total_budget=None)
    result = extract_requirements(
        "想去杭州", FakeModel([json.dumps(complete_payload)]), reference_date=date(2026, 9, 9)
    )
    assert result.missing_required_fields == ["days", "travelers", "total_budget"]
    assert result.extraction.total_budget is None
    assert result.clarification == (
        "这次准备几个人一起去，大概想玩几天，整趟旅行的总预算大约多少元？"
        "还没想好也没关系，可以先说个大概。"
    )


"""明确的首尾日期可以算出天数，含出发和返回当天，并记录这次推导。"""

def test_dates_determine_days(complete_payload: dict[str, object]) -> None:
    complete_payload.update(start_date="2026-10-01", end_date="2026-10-03", days=None)
    result = extract_requirements(
        "十月一日至三日",
        FakeModel([json.dumps(complete_payload)]),
        reference_date=date(2026, 9, 9),
    )
    assert result.extraction.days == 3
    assert result.extraction.assumptions == ["根据首尾日期计算为3天，包含出发和返回当天。"]
    assert result.missing_required_fields == []


"""只给开始日期仍是有效的部分需求，但还需要天数或结束日期。"""

def test_partial_date_is_not_invented(complete_payload: dict[str, object]) -> None:
    complete_payload.update(start_date="2026-10-01", days=None)
    result = extract_requirements(
        "十月一日出发",
        FakeModel([json.dumps(complete_payload)]),
        reference_date=date(2026, 9, 9),
    )
    assert result.extraction.end_date is None
    assert result.missing_required_fields == ["days"]


"""首轮开始日加天数即可算结束日；首日计入，并覆盖跨月、跨年和闰日。"""

@pytest.mark.parametrize(
    ("start", "expected_end"),
    [
        ("2026-10-01", "2026-10-03"),
        ("2026-10-30", "2026-11-01"),
        ("2026-12-31", "2027-01-02"),
        ("2028-02-28", "2028-03-01"),
    ],
)
def test_first_turn_start_and_days_determine_end(
    complete_payload: dict[str, object], start: str, expected_end: str,
) -> None:
    complete_payload.update(start_date=start, end_date=None, days=3)
    result = extract_requirements(
        f"想去杭州三天，从{start}开始",
        FakeModel([json.dumps(complete_payload)]),
        reference_date=date(2026, 9, 11),
    )
    assert result.extraction.start_date == date.fromisoformat(start)
    assert result.extraction.end_date == date.fromisoformat(expected_end)
    assert result.extraction.days == 3
    assert result.extraction.assumptions
    assert complete_payload["end_date"] is None


"""参数化：让不同坏数据走同一条真实校验路径，确认它们均被拒绝。"""

@pytest.mark.parametrize(
    "changes",
    [
        {"days": 1},
        {"days": 6},
        {"days": True},
        {"days": "3"},
        {"travelers": 0},
        {"travelers": 9},
        {"travelers": 2.5},
        {"total_budget": "-1"},
        {"total_budget": True},
        {"total_budget": "1.001"},
        {"total_budget": "NaN"},
        {"destination": "   "},
        {"interests": [""]},
        {"pace": "fast"},
        {"start_date": "2026-10-03", "end_date": "2026-10-01"},
        {"start_date": "2026-10-01", "end_date": "2026-10-04", "days": 3},
        {"start_date": "2026-10-01", "end_date": "2026-10-10", "days": None},
        {"missing_required_fields": []},
    ],
)
def test_invalid_extraction_is_rejected(
    complete_payload: dict[str, object],
    changes: dict[str, object],
) -> None:
    complete_payload.update(changes)
    with pytest.raises(ValidationError):
        TravelRequestExtraction.model_validate(complete_payload)


"""缺少 JSON 字段属于输出格式错误；字段存在但为 null 才表示用户没说。"""

def test_omitted_field_is_invalid(complete_payload: dict[str, object]) -> None:
    del complete_payload["total_budget"]
    with pytest.raises(ValidationError):
        TravelRequestExtraction.model_validate(complete_payload)


"""无效 JSON、缺字段和业务矛盾均允许一次修复，原始用户消息仍在上下文中。"""

@pytest.mark.parametrize("bad_answer", ["不是JSON", "{}", '{"days": -1}'])
def test_invalid_output_can_be_repaired_once(
    complete_payload: dict[str, object],
    bad_answer: str,
) -> None:
    model = FakeModel([bad_answer, json.dumps(complete_payload)])
    result = extract_requirements("去杭州", model, reference_date=date(2026, 9, 9))
    assert result.extraction.destination == "杭州"
    assert len(model.calls) == 2
    assert model.calls[1][1] == {"role": "user", "content": "去杭州"}
    assert model.calls[1][-2] == {"role": "assistant", "content": bad_answer}
    assert "校验" in model.calls[1][-1]["content"]


"""修复仍失败就结束，不能无限请求，也不能把模型原文带到对外错误里。"""

def test_second_invalid_output_stops() -> None:
    model = FakeModel(["private-invalid-text", "private-invalid-text"])
    with pytest.raises(RequirementExtractionError) as caught:
        extract_requirements("去杭州", model, reference_date=date(2026, 9, 9))
    assert len(model.calls) == 2
    assert "private-invalid-text" not in str(caught.value)


"""空消息在调用模型之前被拒绝，避免发送没有业务意义的收费请求。"""

def test_blank_message_does_not_call_model() -> None:
    model = FakeModel([])
    with pytest.raises(ValueError, match="消息"):
        extract_requirements("  ", model, reference_date=date(2026, 9, 9))
    assert model.calls == []


"""闲聊不应被要求填旅行表；意图分类仍由模型负责，本用例只测后处理。"""

def test_other_intent_does_not_ask_trip_questions(complete_payload: dict[str, object]) -> None:
    complete_payload.update(intent="other", destination=None, days=None, total_budget=None)
    result = extract_requirements(
        "你好", FakeModel([json.dumps(complete_payload)]), reference_date=date(2026, 9, 9)
    )
    assert result.clarification is None
    assert result.message_intent == "other"


"""没有旧档案时，模型误报修改也应建立需求；缺预算仍要追问，不能填默认值。"""

@pytest.mark.parametrize("budget", ["5000.00", None])
def test_first_request_normalizes_modify_intent(
    complete_payload: dict[str, object], budget: str | None,
) -> None:
    complete_payload.update(intent="modify_trip", total_budget=budget, pace="relaxed")
    result = extract_requirements(
        "杭州三天两人，节奏轻松一点" + ("，预算5000元" if budget else ""),
        FakeModel([json.dumps(complete_payload)]),
        reference_date=date(2026, 9, 11),
    )
    assert result.message_intent == "plan_trip"
    assert result.extraction.intent == "plan_trip"
    assert result.extraction.destination == "杭州" and result.extraction.pace == "relaxed"
    assert result.missing_required_fields == (["total_budget"] if budget is None else [])
    if budget is None:
        assert result.extraction.total_budget is None
        assert result.clarification is not None and "预算" in result.clarification


"""第二轮只说人数时，模型看到旧表格，程序合并后仍保留城市和预算。"""

def test_followup_preserves_context(complete_payload: dict[str, object]) -> None:
    previous = TravelRequestExtraction.model_validate(complete_payload)
    changes = dict(complete_payload)
    changes.update(intent="modify_trip", destination=None, origin=None, days=None,
                   travelers=3, total_budget=None, interests=[])
    model = FakeModel([json.dumps(changes)])
    result = extract_requirements(
        "改成三个人", model, reference_date=date(2026, 9, 10), previous=previous,
    )
    assert result.extraction.travelers == 3
    assert result.extraction.destination == "杭州" and result.extraction.total_budget == 5000
    assert result.missing_required_fields == []
    assert result.message_intent == "modify_trip"
    assert "杭州" in model.calls[0][1]["content"]
    assert model.calls[0][-1]["content"] == "改成三个人"


"""删除指令未匹配历史时让模型修复一次，然后正确删除原文条目。"""

def test_merge_error_uses_same_single_repair(complete_payload: dict[str, object]) -> None:
    complete_payload["hard_constraints"] = ["不爬山"]
    previous = TravelRequestExtraction.model_validate(complete_payload)
    changes = dict(complete_payload)
    changes.update(intent="modify_trip", destination=None, origin=None, days=None,
                   travelers=None, total_budget=None, interests=[], hard_constraints=[],
                   remove_items={"hard_constraints": ["不登山"]})
    corrected = changes | {"remove_items": {"hard_constraints": ["不爬山"]}}
    model = FakeModel([json.dumps(changes), json.dumps(corrected)])
    result = extract_requirements(
        "取消不爬山的限制", model, reference_date=date(2026, 9, 10), previous=previous,
    )
    assert result.extraction.hard_constraints == [] and len(model.calls) == 2
    assert previous.hard_constraints == ["不爬山"]


"""清空预算后应重新追问，不将旧预算误当作本轮仍然有效。"""

def test_clear_budget_reopens_clarification(complete_payload: dict[str, object]) -> None:
    previous = TravelRequestExtraction.model_validate(complete_payload)
    changes = dict(complete_payload)
    changes.update(intent="modify_trip", total_budget=None, clear_fields=["total_budget"])
    result = extract_requirements(
        "预算还没定", FakeModel([json.dumps(changes)]),
        reference_date=date(2026, 9, 10), previous=previous,
    )
    assert result.missing_required_fields == ["total_budget"]
    assert result.extraction.total_budget is None
