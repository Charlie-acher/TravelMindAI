"""需求评测的判卷逻辑：读取人工标注、调用正式服务、比较结果，不修改业务规则。"""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.client import ModelClientError
from app.schemas.requirement import Intent, TravelRequestExtraction
from app.services.requirement_service import (
    ModelClient,
    RequirementExtractionError,
    extract_requirements,
)

# PRD的四个必要字段。日期另外逐项核对，避免天数正确却掩盖具体日期错误。
REQUIRED = ("destination", "days", "travelers", "total_budget")


class EvalTurn(BaseModel):
    """一条原话和人工期望；expected只进入评分函数，绝不作为模型输入。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=6000)
    intent: Intent
    expected: dict[str, Any]

    """规划题必须标齐四个必要字段，null表示应当未知，禁止漏标后虚增分数。"""

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        if (
            self.intent in {"plan_trip", "modify_trip"}
            and not set(REQUIRED) <= self.expected.keys()
        ):
            raise ValueError("规划题必须标注全部四个必要字段，未知值明确填null")
        allowed = set(TravelRequestExtraction.model_fields) - {"intent", "assumptions"}
        if not self.expected.keys() <= allowed:
            raise ValueError("标注包含不支持的旅行字段")
        # 借用正式schema检查标注类型及日期关系，题库本身也不能填无效答案。
        values: dict[str, Any] = {field: None for field in allowed}
        for field in (
            "interests",
            "dietary",
            "lodging_preferences",
            "hard_constraints",
            "excluded_items",
        ):
            values[field] = []
        TravelRequestExtraction.model_validate(
            values | self.expected | {"intent": self.intent, "assumptions": []}
        )
        return self


class EvalCase(BaseModel):
    """一个独立场景，内部可以多轮；每组从空需求开始，避免题与题之间串话。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    category: str
    note: str  # 中文注释说明这一题为什么这样标。
    reference_date: date
    turns: list[EvalTurn] = Field(min_length=1)


class EvalDataset(BaseModel):
    """版本化人工题库；重复编号会让错误报告难以定位，因此读取时拒绝。"""

    model_config = ConfigDict(extra="forbid")
    version: str
    description: str
    cases: list[EvalCase] = Field(min_length=1)

    """每个编号只对应一道题；不限制自定义题库规模，正式验收另检查至少30组。"""

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("题库编号重复")
        return self


"""显式读取UTF-8题库；校验失败直接停止，不悄悄跳过坏题。"""

def load_dataset(path: Path) -> EvalDataset:
    return EvalDataset.model_validate_json(path.read_text(encoding="utf-8"))


class CountingModel:
    """仅包一层调用计数，用于区分首次答案和一次修复，不改变提示词或模型参数。"""

    """接收已有模型，不读取密钥、不创建网络连接。"""

    def __init__(self, model: ModelClient) -> None:
        self.model = model
        self.calls = 0

    """每次真正请求计数一次；提供方异常也计入，便于记录失败成本。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        return self.model.generate_json(messages)


"""金额按数值比较；列表忽略排列顺序但要求条目相同，其余字段严格比较。"""

def same_value(field: str, actual: Any, expected: Any) -> bool:
    if field == "total_budget" and actual is not None and expected is not None:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    if isinstance(actual, list) and isinstance(expected, list):
        return sorted(actual) == sorted(expected)
    return bool(actual == expected)


"""逐轮执行正式抽取服务；下一轮只使用实际成功结果，不从人工答案补齐历史。"""

def evaluate_case(case: EvalCase, model: ModelClient) -> dict[str, Any]:
    previous: TravelRequestExtraction | None = None
    counting = CountingModel(model)
    records: list[dict[str, Any]] = []
    for index, turn in enumerate(case.turns, 1):
        started, calls_before = perf_counter(), counting.calls
        actual: dict[str, Any] | None = None
        error: str | None = None
        try:
            result = extract_requirements(
                turn.message, counting, reference_date=case.reference_date, previous=previous
            )
            actual = result.model_dump(mode="json")
            if result.message_intent in {"plan_trip", "modify_trip"}:
                previous = result.extraction
        except (RequirementExtractionError, ModelClientError) as failure:
            # 只记录自有异常类别，不保存提供方原始响应或连接配置。
            error = type(failure).__name__
        checks: list[dict[str, Any]] = []
        planning = turn.intent in {"plan_trip", "modify_trip"}
        targets = {"message_intent": turn.intent, **turn.expected}
        if planning:
            targets["missing_required_fields"] = [
                field for field in REQUIRED if turn.expected[field] is None
            ]
        for field, expected in targets.items():
            value = (
                (
                    actual.get(field)
                    if field in {"message_intent", "missing_required_fields"}
                    else actual["extraction"].get(field)
                )
                if actual is not None
                else None
            )
            checks.append(
                {
                    "field": field,
                    "expected": expected,
                    "actual": value,
                    "correct": actual is not None and same_value(field, value, expected),
                    "required": planning and field in REQUIRED,
                }
            )
        differences = [check for check in checks if not check["correct"]]
        records.append(
            {
                "turn": index,
                "message": turn.message,
                "actual": actual,
                "checks": checks,
                "differences": differences,
                "error": error,
                "passed": not differences,
                "model_calls": counting.calls - calls_before,
                "seconds": round(perf_counter() - started, 3),
            }
        )
    return {
        "id": case.id,
        "category": case.category,
        "note": case.note,
        "reference_date": case.reference_date.isoformat(),
        "turns": records,
        "passed": all(record["passed"] for record in records),
    }


"""汇总分子和分母，分开已提供值与未知值；失败题仍在分母中，空分母不显示100%。"""

def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [turn for case in results for turn in case["turns"]]
    checks = [check for turn in turns for check in turn["checks"]]

    """返回原始计数及比例，报告可复算，不能只给四舍五入后的百分比。"""

    def metric(items: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(item["correct"] for item in items)
        return {
            "correct": correct,
            "total": len(items),
            "accuracy": correct / len(items) if items else None,
        }

    required = [check for check in checks if check["required"]]
    return {
        "cases": len(results),
        "cases_passed": sum(case["passed"] for case in results),
        "turns": len(turns),
        "successful_turns": sum(turn["error"] is None for turn in turns),
        "turns_passed": sum(turn["passed"] for turn in turns),
        "model_calls": sum(turn["model_calls"] for turn in turns),
        "repair_turns": sum(turn["model_calls"] > 1 for turn in turns),
        "required_fields": metric(required),
        "provided_fields": metric([check for check in required if check["expected"] is not None]),
        "unknown_fields": metric([check for check in required if check["expected"] is None]),
        "intent": metric([check for check in checks if check["field"] == "message_intent"]),
        "per_required_field": {
            field: metric([check for check in required if check["field"] == field])
            for field in REQUIRED
        },
    }
