"""评测测试层：验证召回分母、无证据题和事实检查不会被有效引用掩盖。"""

from pathlib import Path

from scripts.document_eval import load_dataset, score_case, summarize

"""评分检查函数：引用有效但事实错误时，内容覆盖必须失败。"""


def test_wrong_fact_with_valid_citation_fails() -> None:
    case = {
        "relevant_ids": ["a", "b"],
        "expected_status": "answered",
        "expected_facts": [["清河坊"]],
        "forbidden_terms": [],
    }
    answer = {"status": "answered", "points": [{"text": "别名是南锣鼓巷", "source_ids": [1]}]}
    result = score_case(case, ["x", "a"], answer)
    assert result["recall_at_5"] == 0.5
    assert result["reciprocal_rank"] == 0.5
    assert result["citation_valid"] is True
    assert result["facts_covered"] == 0
    assert result["faithfulness"] is None


"""无证据检查函数：拒答单独计分，不进入召回率的分母。"""


def test_no_evidence_and_failure_denominators() -> None:
    case = {
        "relevant_ids": [],
        "expected_status": "insufficient",
        "expected_facts": [],
        "forbidden_terms": [],
    }
    result = score_case(case, ["a"], {"status": "insufficient", "points": []})
    assert result["recall_at_5"] is None
    assert result["status_correct"] is True
    failed = score_case(
        {
            **case,
            "relevant_ids": ["b"],
            "expected_status": "answered",
            "expected_facts": [["正确"]],
        },
        [],
        None,
    )
    summary = summarize([{"scores": result}, {"scores": failed}])
    assert summary["retrieval_questions"] == 1
    assert summary["recall_at_5"] == 0
    assert summary["successful_answers"] == 1
    assert summary["faithfulness_reviewed"] == 0


"""固定题库检查函数：所有人工标注都能指向唯一、可追溯的固定片段。"""


def test_fixed_dataset() -> None:
    dataset = load_dataset(Path(__file__).resolve().parents[1] / "evals/documents.v1.json")
    assert len(dataset["cases"]) >= 30
    assert 20 <= len(dataset["corpus"]) <= 50
