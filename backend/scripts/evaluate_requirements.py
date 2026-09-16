"""评测命令：默认只校验题库；--live才调用真实DeepSeek，产生API费用。

示例：python -m scripts.evaluate_requirements --live --env-file ../.env
复用网页的抽取/合并服务，但不写旅行会话数据库；题目答案不会传给模型。
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import load_settings
from app.llm.client import DeepSeekClient, ModelClientError
from app.services.requirement.prompt import build_requirement_prompt
from scripts.requirement_eval import evaluate_case, load_dataset, summarize

"""生成方便人阅读的成绩单；完整逐字段答案仍在旁边的JSON里供复查。"""

def render_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# M2 需求抽取评测报告",
        "",
        f"运行时间：{report['started_at']}（UTC）",
        f"模型：{report['model']}；题库：{report['dataset_version']}",
        "",
        "这是人工编写的开发评测集，不能代表真实用户分布或独立盲测准确率。",
        "题库与提示词哈希、逐字段结果和调用次数见同名JSON。",
        "",
        "| 指标 | 正确 / 总数 | 准确率 |",
        "|---|---|---|",
    ]
    for key, label in [
        ("required_fields", "四个必要字段（含应为空值）"),
        ("provided_fields", "必要字段中的已提供值"),
        ("unknown_fields", "必要字段中的应未知值"),
        ("intent", "当前消息意图"),
    ]:
        metric = summary[key]
        rate = f"{metric['accuracy']:.2%}" if metric["accuracy"] is not None else "不适用"
        lines.append(f"| {label} | {metric['correct']} / {metric['total']} | {rate} |")
    lines.extend(
        [
            "",
            f"完整通过：{summary['cases_passed']}/{summary['cases']}组；"
            f"{summary['turns_passed']}/{summary['turns']}轮。",
            f"成功返回有效结果：{summary['successful_turns']}/{summary['turns']}轮；"
            f"模型请求{summary['model_calls']}次，其中{summary['repair_turns']}轮触发一次修复。",
            f"必要字段90%目标：{'达到' if report['required_target_met'] else '未达到'}。",
            "",
            "## 未通过项目",
            "",
        ]
    )
    failures = [case for case in report["results"] if not case["passed"]]
    if not failures:
        lines.append("本次标注检查全部通过。")
    for case in failures:
        lines.extend([f"### {case['id']} · {case['category']}", "", case["note"], ""])
        for turn in case["turns"]:
            if turn["passed"]:
                continue
            lines.extend([f"第{turn['turn']}轮：{turn['message']}", ""])
            if turn["error"]:
                lines.append(f"执行错误：`{turn['error']}`。此轮所有标注项记错，没有从分母剔除。")
            for item in turn["differences"]:
                expected = json.dumps(item["expected"], ensure_ascii=False)
                actual = json.dumps(item["actual"], ensure_ascii=False)
                lines.append(f"- `{item['field']}`：期望 `{expected}`；实际 `{actual}`。")
            lines.append("")
    lines.extend(
        [
            "",
            "## 评分边界",
            "",
            "- 必要字段为目的地、天数、人数、全团预算；规划/修改轮次的四项全部计分。",
            "- 非规划题计入意图及整题通过率，不用其空字段抬高必要字段指标。",
            "- 金额按Decimal数值比较；列表忽略顺序但逐条精确匹配，不用另一个模型判分。",
            "- 日期、偏好及缺项列表也参与整题通过判定；没有标注的可选字段不计分。",
            "- 模型未返回有效结果时，本轮所有标注项算错。修复后的答案计入最终成绩。",
            "- 多轮采用实际上一轮结果，错误可能传播；不会使用标注答案补齐上下文。",
            "- 未测RAG、资料冲突、实时预算或行程生成；这些属于后续阶段。",
            "- 仅记录调用次数和耗时，未取得提供方账单，未推算实际费用。",
            "",
        ]
    )
    return "\n".join(lines)


"""先校验题库，再显式执行收费评测；写版本化结果，不覆盖已有报告。"""

def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="校验并评测M2旅行需求题库")
    parser.add_argument("--dataset", type=Path, default=root / "evals/requirements.v1.json")
    parser.add_argument("--env-file", type=Path, help="显式模型配置路径")
    parser.add_argument("--live", action="store_true", help="调用真实DeepSeek；会产生API费用")
    parser.add_argument("--output", type=Path, help="新JSON报告路径，同名md随运行完成生成")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
    except (OSError, ValidationError):
        print("题库读取或标注校验失败，请检查路径和字段。", file=sys.stderr)
        return 1
    print(
        f"题库校验通过：{len(dataset.cases)}组，"
        f"{sum(len(case.turns) for case in dataset.cases)}轮。",
        flush=True,
    )
    if not args.live:
        print("仅校验题库，未调用模型；实际评测请加--live。")
        return 0
    if args.env_file is not None and not args.env_file.is_file():
        print("配置文件不存在。", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    output = args.output or root / "evals/reports" / f"m2-{now:%Y%m%dT%H%M%S%fZ}.json"
    if output.suffix != ".json" or output.exists() or output.with_suffix(".md").exists():
        print("请指定尚不存在的.json路径，保留此前评测记录。", file=sys.stderr)
        return 1
    try:
        settings = load_settings(args.env_file)
        with httpx.Client() as http:
            model = DeepSeekClient(settings, http)
            report: dict[str, Any] = {
                "started_at": now.isoformat(),
                "completed": False,
                "model": settings.deepseek_model,
                "dataset_version": dataset.version,
                "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                "prompt_sha256": {
                    case.reference_date.isoformat(): hashlib.sha256(
                        build_requirement_prompt(case.reference_date).encode("utf-8")
                    ).hexdigest()
                    for case in dataset.cases
                },
                "packages": {name: version(name) for name in ["langchain-openai", "pydantic"]},
                "results": [],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            for index, case in enumerate(dataset.cases, 1):
                result = evaluate_case(case, model)
                report["results"].append(result)
                # 每完成一组写一次，Ctrl+C中断仍可查看已完成部分，但completed保持false。
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(
                    f"[{index}/{len(dataset.cases)}] {case.id}: "
                    f"{'PASS' if result['passed'] else 'FAIL'}",
                    flush=True,
                )
    except (ValidationError, ModelClientError):
        print("模型配置不可用，请检查显式配置文件；未输出密钥或连接信息。", file=sys.stderr)
        return 1
    report["summary"] = summarize(report["results"])
    report["required_target_met"] = len(dataset.cases) >= 30 and all(
        (report["summary"][key]["accuracy"] or 0) >= 0.9
        for key in ["required_fields", "provided_fields"]
    )
    report["completed"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告：{output.with_suffix('.md')}")
    # 0表示全部标注检查通过；2表示完成评测但存在错误题/目标差距；1表示无法启动评测。
    return (
        0
        if report["required_target_met"] and all(case["passed"] for case in report["results"])
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
