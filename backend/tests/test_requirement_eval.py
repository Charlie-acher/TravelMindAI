"""评测程序也需要考卷：防止把缺答案、模型失败或缺字段算作答对。"""

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.requirement_eval import EvalCase, EvalTurn, evaluate_case, load_dataset, summarize
from tests.test_requirement import FakeModel
from tests.test_requirement_api import answer

"""期望值包含四个必要字段，null是明确标注的未知，不表示跳过评分。"""

def expected(**changes: object) -> dict[str, object]:
    return {"destination": "杭州", "days": 3, "travelers": 2, "total_budget": "5000"} | changes


"""创建一组固定日期的考题，评测不能把期望答案送给模型。"""

def case(turns: list[EvalTurn]) -> EvalCase:
    return EvalCase(
        id="test", category="测试", note="评分自测", reference_date=date(2026, 9, 11), turns=turns
    )


"""金额5000和5000.00相等；真正不同的人数必须记错，不能只检查字段存在。"""

def test_scoring_compares_values_and_normalizes_money() -> None:
    model = FakeModel(
        [json.dumps(answer(destination="杭州", days=3, travelers=3, total_budget="5000.00"))]
    )
    result = evaluate_case(
        case([EvalTurn(message="杭州三天两人五千", intent="plan_trip", expected=expected())]), model
    )
    summary = summarize([result])
    assert summary["required_fields"] == {"correct": 3, "total": 4, "accuracy": 0.75}
    assert summary["cases_passed"] == 0
    assert model.calls[0][-1]["content"] == "杭州三天两人五千"
    assert not any("评分自测" in message["content"] for message in model.calls[0])
    assert result["turns"][0]["differences"][0]["field"] == "travelers"


"""连续两次无效输出必须算失败，四个必要字段全错；不能从分母移除失败题。"""

def test_model_failure_stays_in_denominator() -> None:
    result = evaluate_case(
        case([EvalTurn(message="杭州", intent="plan_trip", expected=expected())]),
        FakeModel(["{}", "{}"]),
    )
    summary = summarize([result])
    assert summary["required_fields"]["total"] == 4
    assert summary["required_fields"]["correct"] == 0
    assert summary["successful_turns"] == 0
    assert result["turns"][0]["model_calls"] == 2


"""模型编造预算要扣分；只填写城市不会因为正确空值多而掩盖已提供字段的错误。"""

def test_unknowns_and_provided_values_are_reported_separately() -> None:
    turn = EvalTurn(
        message="杭州",
        intent="plan_trip",
        expected=expected(days=None, travelers=None, total_budget=None),
    )
    result = evaluate_case(
        case([turn]), FakeModel([json.dumps(answer(destination="杭州", total_budget="5000"))])
    )
    summary = summarize([result])
    assert summary["provided_fields"]["accuracy"] == 1.0
    assert summary["unknown_fields"]["correct"] == 2
    assert summary["unknown_fields"]["total"] == 3
    assert not result["passed"]


"""多轮使用模型实际答案，第一轮错杭州→苏州后第二轮仍沿用苏州，不能偷用标注纠正。"""

def test_multiturn_uses_actual_history_not_gold() -> None:
    model = FakeModel(
        [
            json.dumps(answer(destination="苏州", days=3, travelers=2, total_budget="5000")),
            json.dumps(answer(intent="modify_trip", travelers=3)),
        ]
    )
    turns = [
        EvalTurn(message="杭州三天两人五千", intent="plan_trip", expected=expected()),
        EvalTurn(message="改成三个人", intent="modify_trip", expected=expected(travelers=3)),
    ]
    result = evaluate_case(case(turns), model)
    assert result["turns"][1]["actual"]["extraction"]["destination"] == "苏州"
    assert "苏州" in model.calls[1][1]["content"]


"""正式题库必须有30组唯一编号；规划题不能漏标必要字段。"""

def test_dataset_contract() -> None:
    dataset = load_dataset(Path(__file__).resolve().parents[1] / "evals/requirements.v1.json")
    assert len(dataset.cases) == 30
    assert len({item.id for item in dataset.cases}) == 30
    assert sum(len(item.turns) for item in dataset.cases) > 30
    with pytest.raises(ValueError):
        EvalTurn(message="杭州", intent="plan_trip", expected={"destination": "杭州"})


"""不支持意图计入意图和案例通过率，不把无业务答案的题塞进必要字段准确率。"""

def test_unsupported_is_scored_without_inflating_required_fields() -> None:
    result = evaluate_case(
        case([EvalTurn(message="写首诗", intent="other", expected={})]),
        FakeModel([json.dumps(answer(intent="other"))]),
    )
    summary = summarize([result])
    assert result["passed"]
    assert summary["required_fields"]["total"] == 0
    assert summary["required_fields"]["accuracy"] is None
    assert summary["intent"]["accuracy"] == 1.0


"""默认命令只校验题库，不能读取个人密钥或创建收费模型客户端。"""

def test_cli_validation_never_creates_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import evaluate_requirements

    """默认模式调用这里就使测试失败，不使用真实提供方。"""

    def forbidden(*args, **kwargs):
        raise AssertionError("只校验题库时不得初始化模型")

    monkeypatch.setattr(evaluate_requirements, "DeepSeekClient", forbidden)
    monkeypatch.setattr(evaluate_requirements, "load_settings", forbidden)
    assert evaluate_requirements.main([]) == 0


"""真实运行遇到错误题时也生成完整报告并返回2；同路径再次运行必须拒绝覆盖。"""

def test_cli_records_failed_run_and_preserves_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import evaluate_requirements

    monkeypatch.setattr(
        evaluate_requirements, "DeepSeekClient", lambda *args: FakeModel(["{}"] * 72)
    )
    output = tmp_path / "failed.json"
    assert evaluate_requirements.main(["--live", "--output", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] is True and report["summary"]["successful_turns"] == 0
    assert report["summary"]["required_fields"]["total"] > 0
    before = output.read_bytes()
    assert evaluate_requirements.main(["--live", "--output", str(output)]) == 1
    assert output.read_bytes() == before
    assert "RequirementExtractionError" in output.with_suffix(".md").read_text(encoding="utf-8")
