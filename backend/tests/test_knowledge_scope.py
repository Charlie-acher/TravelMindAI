"""测试层：旧向量迁移保留编号与值，中途读回失败也不得提前开放新范围。"""

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import Engine, select

from app.config import Settings
from app.models.document import DocumentRecord
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusError, MilvusStore
from scripts.migrate_knowledge_scope import migrate_scope

"""迁移中断测试函数：写入成功但读回失败后，重跑必须恢复原向量再切换数据库。"""

def test_scope_migration_rechecks_interrupted_batch(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path / "files", "local-demo")
    document = documents.upload(BytesIO("杭州西湖".encode()), "杭州景点.txt", "text/plain").document
    chunk = documents.generate_chunks(document.id).items[0]
    collection = "travelmind_chunks_" + "a" * 24
    original = {"id": str(chunk.id), "document_id": str(document.id),
                "owner_id": "local-demo", "vector": [0.5, 0.25]}
    stored = deepcopy(original)
    corrupt = True

    """向量HTTP替身函数：模拟首批值损坏，第二次重跑必须用日志原值恢复。"""

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal stored, corrupt
        body = json.loads(request.content)
        if request.url.path.endswith("collections/list"):
            data = [collection]
        elif request.url.path.endswith("entities/upsert"):
            stored = deepcopy(body["data"][0])
            if corrupt:
                stored["vector"] = [9.0, 9.0]
                corrupt = False
            data = {"upsertCount": 1}
        elif body["filter"] == 'owner_id == "local-demo"':
            data = [deepcopy(stored)] if stored["owner_id"] == "local-demo" else []
        else:
            data = [deepcopy(stored)]
        return httpx.Response(200, json={"code": 0, "data": data})

    settings = Settings(milvus_url="http://localhost:19530", embedding_provider="test",
                        embedding_base_url="https://example.com/v1", embedding_model="test",
                        embedding_dimensions=2, embedding_version="scope-test")
    journal = tmp_path / "pending.json"
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        vectors = MilvusStore(settings, http)
        with pytest.raises(MilvusError, match="读回"):
            migrate_scope(store_engine, vectors, journal)
        with store_engine.connect() as connection:
            assert connection.scalar(select(DocumentRecord.owner_id).where(
                DocumentRecord.id == document.id,
            )) == "local-demo"
        migrate_scope(store_engine, vectors, journal)
    assert stored == original | {"owner_id": "knowledge-base"}
    assert UUID(stored["id"]) == chunk.id
    with store_engine.connect() as connection:
        assert connection.scalar(select(DocumentRecord.owner_id).where(
            DocumentRecord.id == document.id,
        )) == "knowledge-base"
