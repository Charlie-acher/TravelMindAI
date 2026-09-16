"""部署迁移层：维护窗口内改写已有向量归属，读回一致后切换原文范围，不调用Embedding。"""

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.database import create_database_engine
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.models.document_job import DocumentJobRecord
from app.services.document.vector_store import MilvusError, MilvusStore
from app.services.knowledge_scope import KNOWLEDGE_SCOPE

"""向量范围迁移函数：分批复用原向量和编号，每批写后逐项读回核对，允许失败后重跑。"""

def migrate_vectors(
    store: MilvusStore, expected: dict[UUID, UUID], journal: Path, database_key: str,
) -> int:
    collections = store._post("collections/list", {})
    if not isinstance(collections, list) or any(not isinstance(name, str) for name in collections):
        raise MilvusError("无法核对向量集合列表")
    pending = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else None
    if pending is not None:
        if pending.get("database") != database_key or pending.get("collection") not in collections:
            raise MilvusError("迁移日志与当前数据库或集合不符，请保持维护状态核对")
        # 先恢复未确认的批次；不能跳过已经改成新范围但未读回核对的向量。
        collection = pending["collection"]
        collections = [collection, *(name for name in collections if name != collection)]
    count = 0
    for collection in collections:
        if not re.fullmatch(r"travelmind_chunks_[0-9a-f]{24}", collection):
            continue
        while True:
            rows = pending["rows"] if pending is not None else store._post("entities/query", {
                "collectionName": collection, "filter": 'owner_id == "local-demo"',
                "outputFields": ["id", "document_id", "owner_id", "vector"],
                "limit": 200, "consistencyLevel": "Strong",
            })
            if not isinstance(rows, list):
                raise MilvusError("旧向量读取失败，保持维护状态")
            if not rows:
                break
            for row in rows:
                try:
                    if expected[UUID(row["id"])] != UUID(row["document_id"]):
                        raise ValueError()
                    if (row["owner_id"] not in {"local-demo", KNOWLEDGE_SCOPE}
                            or not isinstance(row["vector"], list)):
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    raise MilvusError("向量与旧资料片段不一致，保持维护状态") from None
                row["owner_id"] = KNOWLEDGE_SCOPE
            # 写前保存原值；写入或读回中断后，重跑仍能逐值核对同一批数据。
            journal.parent.mkdir(parents=True, exist_ok=True)
            temporary = journal.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "database": database_key, "collection": collection, "rows": rows,
            }), encoding="utf-8")
            temporary.replace(journal)
            result = store._post("entities/upsert", {"collectionName": collection, "data": rows})
            if not isinstance(result, dict) or result.get("upsertCount") != len(rows):
                raise MilvusError("范围改写未全部确认，保持维护状态并重试")
            verified = store._post("entities/query", {
                "collectionName": collection,
                "filter": "id in " + json.dumps([row["id"] for row in rows]),
                "outputFields": ["id", "document_id", "owner_id", "vector"],
                "limit": 200, "consistencyLevel": "Strong",
            })
            if not isinstance(verified, list) or sorted(verified, key=lambda row: row["id"]) != (
                sorted(rows, key=lambda row: row["id"])
            ):
                raise MilvusError("向量读回与原值不一致，保持维护状态")
            count += len(rows)
            journal.unlink()
            pending = None
    return count


"""资料范围迁移函数：锁住资料与任务防止并发处理，最后才提交共享原文范围。"""

def migrate_scope(engine: Engine, store: MilvusStore | None, journal: Path) -> tuple[int, int]:
    with engine.begin() as connection:
        # 运行前仍须停止旧版服务；表锁只保护当前事务，不代替维护窗口。
        from sqlalchemy import text

        connection.execute(text("LOCK TABLE documents, document_jobs IN EXCLUSIVE MODE"))
        active = connection.scalar(select(DocumentJobRecord.document_id).where(
            DocumentJobRecord.status.in_(["queued", "running"]),
        ).limit(1))
        if active is not None:
            raise ValueError("仍有排队或运行中的资料任务，请先暂停并等待结束")
        old = list(connection.scalars(select(DocumentRecord.id).where(
            DocumentRecord.owner_id == "local-demo",
        )))
        if not old:
            return 0, 0
        chunks = dict(connection.execute(select(
            DocumentChunkRecord.id, DocumentChunkRecord.document_id,
        ).where(DocumentChunkRecord.document_id.in_(old))).tuples().all())
        if chunks and store is None:
            raise ValueError("已有片段，必须连接Milvus核对旧向量后才能切换范围")
        # 指纹绑定实际连接与schema；不会输出数据库地址和密码。
        database_key = sha256(engine.url.render_as_string(hide_password=False).encode()).hexdigest()
        count = migrate_vectors(store, chunks, journal, database_key) if store is not None else 0
        connection.execute(update(DocumentRecord).where(DocumentRecord.id.in_(old)).values(
            owner_id=KNOWLEDGE_SCOPE,
        ))
    return len(old), count


"""迁移命令函数：明确进入维护窗口后运行，错误不打印模型密钥或数据库地址。"""

def main() -> int:
    parser = argparse.ArgumentParser(description="将local-demo知识库迁移为knowledge-base")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--maintenance", action="store_true", required=True,
                        help="确认已备份并停止旧版服务与资料处理")
    parser.add_argument("--journal", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "runtime"
                        / "knowledge-scope-pending.json", help="中断恢复日志，不可在失败后删除")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("指定的配置文件不存在")
    settings = load_settings(args.env_file)
    engine = create_database_engine(settings)
    try:
        with httpx.Client() as http:
            store = MilvusStore(settings, http) if settings.milvus_url is not None else None
            documents, vectors = migrate_scope(engine, store, args.journal)
        print(f"迁移完成：{documents}份资料，{vectors}个既有向量；未调用Embedding")
    except (MilvusError, ValueError) as error:
        print(str(error))
        return 1
    except SQLAlchemyError:
        print("数据库操作失败，请保持维护状态并检查迁移与连接；可修复后重跑")
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
