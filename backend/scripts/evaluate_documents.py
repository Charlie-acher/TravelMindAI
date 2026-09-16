"""评测命令层：复用真实资料服务和模型，隔离数据库后保存可复核的M3逐题报告。

默认仅验证题库；--live显式调用收费模型，结束后清理本次临时数据。
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

from app.config import load_settings
from app.llm.client import DeepSeekClient
from app.llm.embeddings import EmbeddingClient
from app.models.trip import Base
from app.services.document.answer import ANSWER_INSTRUCTIONS, answer_from_sources
from app.services.document.search import DocumentSearchService
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusStore
from scripts.document_eval import load_dataset, score_case, summarize


class RecordedModel:
    """模型记录类：原样调用生产客户端，仅保留模型看到的消息及未经校验的输出。"""

    """初始化函数：接收已经配置的真实客户端。"""

    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    """生成函数：不发送标注答案，生产修复调用也逐次记录。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        call: dict[str, Any] = {"messages": [dict(message) for message in messages]}
        self.calls.append(call)
        raw = self.client.generate_json(messages)
        call["raw"] = raw
        return raw


"""报告保存函数：逐题写检查点，避免后续网络失败丢失前面证据。"""


def save_report(path: Path, report: dict[str, Any]) -> None:
    report["config_sha256"] = hashlib.sha256(
        json.dumps(report["config"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    report["summary"] = summarize(report["results"])
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s = report["summary"]
    path.with_suffix(".md").write_text(
        "# M3 固定题库真实评测\n\n"
        f"题库：{report['dataset_version']}；题库SHA-256：`{report['dataset_sha256']}`。\n\n"
        f"模型：{report['config']['deepseek_model']}；检索：真实Embedding＋Milvus＋PostgreSQL。\n\n"
        f"已执行 {s['questions']} 题；成功返回 {s['successful_answers']} 题。\n\n"
        f"```json\n{json.dumps(s, ensure_ascii=False, indent=2)}\n```\n\n"
        "召回率为有证据题的逐题Recall@5均值；MRR截断到前5名。无证据题不进入召回分母。"
        "失败题保留在执行及标注分母。评分使用生产链路最终返回值，包含其有界修复；"
        "各次模型原始输出另存，不能把本分数称为未经修复的模型准确率。"
        "引用有效率只检查编号范围；事实覆盖只检查预先标注词组。"
        "全文忠实度必须另读每个断言与引用原文，未审查时为null，不用另一个模型自动判分。\n\n"
        "本题库由Agent阅读固定来源并人工式标注，不是外部人员独立盲测；"
        "小规模静态检索不能代表全量资料、实时旅行信息或生产吞吐。未调用网页搜索、高德，"
        "未计算账单费用。LvBanGPT PDF许可未确认，相关摘录仅供本地研究，发布前须确认。\n\n"
        f"清理状态：{json.dumps(report['cleanup'], ensure_ascii=False)}。"
        "完整出处、配置/提示词/源码hash、检索原文、模型原始输出和逐题评分见同名JSON。\n",
        encoding="utf-8",
    )


"""评测入口函数：准备独立schema和集合，按固定顺序调用生产服务，再限定清理范围。"""


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="M3固定题库；默认不调用网络")
    parser.add_argument("--dataset", type=Path, default=root / "evals/documents.v1.json")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--model-env-file", type=Path, help="仅补充DeepSeek配置的文件")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    print(f"题库校验通过：{len(dataset['corpus'])}片段，{len(dataset['cases'])}题。", flush=True)
    if not args.live:
        return 0
    if args.output is None or args.output.exists() or args.output.with_suffix(".md").exists():
        parser.error("真实评测需要--output指定全新报告路径")
    settings = load_settings(args.env_file)
    if args.model_env_file:
        model_settings = load_settings(args.model_env_file)
        settings = settings.model_copy(update={
            "deepseek_api_key": model_settings.deepseek_api_key,
            "deepseek_model": model_settings.deepseek_model,
            "deepseek_timeout_seconds": model_settings.deepseek_timeout_seconds,
        })
    if settings.database_url is None:
        parser.error("未配置PostgreSQL")
    run_id = uuid4().hex
    schema = "m3_eval_" + run_id
    isolated = settings.model_copy(update={"embedding_version": f"m3-eval-{run_id}"})
    admin = create_engine(settings.database_url.get_secret_value())
    engine = create_engine(
        settings.database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={schema}"},
    )
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_version": dataset["version"],
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(ANSWER_INSTRUCTIONS.encode()).hexdigest(),
        "config": {
            key: str(getattr(settings, key))
            for key in (
                "deepseek_model",
                "deepseek_timeout_seconds",
                "embedding_provider",
                "embedding_model",
                "embedding_dimensions",
                "embedding_version",
                "embedding_base_url",
                "embedding_timeout_seconds",
                "milvus_timeout_seconds",
            )
        },
        "schema": schema,
        "owner_id": schema,
        "isolated_embedding_version": isolated.embedding_version,
        "source_hashes": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "app/services/document").glob("*.py")
        },
        "dataset": dataset,
        "results": [],
        "cleanup": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    created_schema = False
    created_collection = False
    with httpx.Client() as http, TemporaryDirectory(prefix="travelmind-m3-eval-") as directory:
        vectors = MilvusStore(isolated, http)
        report["collection"] = vectors.collection
        try:
            if vectors.exists():
                raise RuntimeError("随机评测集合已存在，拒绝复用或清理")
            with admin.begin() as unit:
                unit.execute(text(f'CREATE SCHEMA "{schema}"'))
            created_schema = True
            Base.metadata.create_all(engine, tables=[
                Base.metadata.tables["documents"], Base.metadata.tables["document_chunks"],
            ])
            documents = DocumentService(engine, Path(directory), schema)
            search = DocumentSearchService(engine, vectors, EmbeddingClient(isolated, http), schema)
            model = RecordedModel(DeepSeekClient(settings, http))
            chunk_ids: dict[str, str] = {}
            created_collection = True  # ensure若部分成功后失败，仍清理自己的随机集合。
            for item in dataset["corpus"]:
                uploaded = documents.upload(
                    BytesIO(item["text"].encode()), item["file_name"], "text/plain"
                )
                chunks = documents.generate_chunks(uploaded.document.id)
                if chunks.total != 1:
                    raise RuntimeError("固定短片段被切成多段，停止避免改变标注含义")
                chunk_ids[str(chunks.items[0].id)] = item["id"]
                if not search.index_batch(uploaded.document.id).complete:
                    raise RuntimeError("评测片段未完成索引")
            report["chunk_ids"] = chunk_ids
            save_report(args.output, report)
            for case in dataset["cases"]:
                row: dict[str, Any] = {"id": case["id"], "case": case, "hits": [], "answer": None}
                model.calls = []
                start = perf_counter()
                retrieved: list[str] = []
                try:
                    hits = search.search(case["question"], 5).items
                    row["search_seconds"] = perf_counter() - start
                    row["hits"] = [hit.model_dump(mode="json") for hit in hits]
                    retrieved = [chunk_ids[str(hit.chunk.id)] for hit in hits]
                    start = perf_counter()
                    row["answer"] = answer_from_sources(
                        case["question"], hits, model, web=None, maps=None
                    ).model_dump(mode="json")
                    row["answer_seconds"] = perf_counter() - start
                except Exception as exc:
                    # 不输出连接地址和凭据；原始模型回答仍在calls中供人工核对。
                    row["error"] = type(exc).__name__
                    row["failed_stage_seconds"] = perf_counter() - start
                row["retrieved_ids"] = retrieved
                row["model_calls"] = model.calls
                row["scores"] = score_case(case, retrieved, row["answer"])
                report["results"].append(row)
                save_report(args.output, report)
                print(f"{case['id']}：{'完成' if row['answer'] else '失败'}", flush=True)
        finally:
            if created_collection:
                try:
                    if vectors.exists():
                        vectors._post("collections/drop", {"collectionName": vectors.collection})
                    report["cleanup"]["milvus_collection_removed"] = not vectors.exists()
                except Exception as exc:
                    report["cleanup"]["milvus_error"] = type(exc).__name__
            engine.dispose()
            if created_schema:
                with admin.begin() as unit:
                    unit.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                report["cleanup"]["postgres_schema_removed"] = True
            admin.dispose()
            save_report(args.output, report)
    report["cleanup"]["temporary_files_removed"] = not Path(directory).exists()
    save_report(args.output, report)
    return 0 if all(row["scores"]["success"] for row in report["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
