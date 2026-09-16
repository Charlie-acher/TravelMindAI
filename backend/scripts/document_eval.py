"""评测评分层：核对固定题库、检索指标和标注事实，不把引用存在当作内容忠实。"""

import json
from pathlib import Path
from statistics import mean
from typing import Any

"""题库读取函数：检查片段和题目关联，拒绝缺失的事实标注。"""


def load_dataset(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    corpus, cases = data["corpus"], data["cases"]
    ids = {item["id"] for item in corpus}
    if not 20 <= len(corpus) <= 50 or len(ids) != len(corpus) or len(cases) < 30:
        raise ValueError("题库需要20至50个唯一片段，至少30道题")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("题目编号重复")
    for item in corpus:
        if not 0 < len(item["text"]) <= 800 or not item["locator"]:
            raise ValueError("片段超过限额或缺少出处")
    for case in cases:
        if not set(case["relevant_ids"]).issubset(ids):
            raise ValueError("标注指向不存在的片段")
        if case["expected_status"] not in {"answered", "insufficient"}:
            raise ValueError("回答状态标注错误")
        if case["expected_status"] == "answered" and (
            not case["expected_facts"] or not case["relevant_ids"]
        ):
            raise ValueError("可回答题缺少证据或事实标注")
    return data


"""单题评分函数：召回、事实覆盖和引用编号分别计算，全文忠实度留待逐题审查。"""


def score_case(
    case: dict[str, Any],
    retrieved: list[str],
    answer: dict[str, Any] | None,
) -> dict[str, Any]:
    expected = set(case["relevant_ids"])
    ranked = retrieved[:5]
    parts = (answer or {}).get("points", []) + (answer or {}).get("attractions", [])
    text = "\n".join(
        str(part.get(key, ""))
        for part in parts
        for key in ("text", "name", "description", "reason")
    )
    citations = [identifier for part in parts for identifier in part.get("source_ids", [])]
    facts = case["expected_facts"]
    return {
        "success": answer is not None,
        "recall_at_5": len(expected.intersection(ranked)) / len(expected) if expected else None,
        "reciprocal_rank": next(
            (1 / rank for rank, cid in enumerate(ranked, 1) if cid in expected), 0.0
        )
        if expected
        else None,
        "status_correct": answer is not None and answer["status"] == case["expected_status"],
        "facts_covered": sum(any(term in text for term in alternatives) for alternatives in facts),
        "facts_total": len(facts),
        "forbidden_found": [term for term in case["forbidden_terms"] if term in text],
        "citation_valid": all(type(i) is int and 1 <= i <= len(ranked) for i in citations)
        if citations
        else None,
        # ponytail: 关键词只核对已标事实；新增断言必须阅读全文，不能自动宣称忠实。
        "faithfulness": None,
        "faithfulness_note": "待逐题核对所有要点、卡片和票价的断言与引用原文",
    }


"""汇总函数：失败保留在分母，未审查的忠实度不产生百分比。"""


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["scores"] for row in results]
    retrieval = [s for s in scores if s["recall_at_5"] is not None]
    cited = [s for s in scores if s["citation_valid"] is not None]
    reviewed = [s for s in scores if s["faithfulness"] is not None]
    facts = sum(s["facts_total"] for s in scores)
    summary = {
        "questions": len(scores),
        "successful_answers": sum(s["success"] for s in scores),
        "retrieval_questions": len(retrieval),
        "recall_at_5": mean(s["recall_at_5"] for s in retrieval) if retrieval else None,
        "mrr_at_5": mean(s["reciprocal_rank"] for s in retrieval) if retrieval else None,
        "status_correct": sum(s["status_correct"] for s in scores),
        "facts_covered": sum(s["facts_covered"] for s in scores),
        "facts_total": facts,
        "citation_valid_questions": sum(s["citation_valid"] for s in cited),
        "citation_questions": len(cited),
        "faithfulness_reviewed": len(reviewed),
        "faithfulness_passed": sum(s["faithfulness"] for s in reviewed),
        "faithfulness_rate": mean(s["faithfulness"] for s in reviewed) if reviewed else None,
    }
    for key in ("search_seconds", "answer_seconds"):
        times = sorted(row[key] for row in results if key in row)
        summary[key] = (
            {"mean": mean(times), "p95": times[min(len(times) - 1, int(len(times) * 0.95))]}
            if times
            else None
        )
    return summary
