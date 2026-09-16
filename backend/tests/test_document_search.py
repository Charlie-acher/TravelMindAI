"""测试层：用真实PostgreSQL和Milvus验收续建、去重、归属及原文回查。"""

import json
import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from app.config import Settings
from app.llm.embeddings import EmbeddingClient
from app.services.document.service import DocumentService

"""接口缺配置测试函数：新路由应明确返回不可用，不是404或空成功。"""


def test_search_routes_report_missing_configuration() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(Settings(database_url=None, milvus_url=None))) as client:
        assert client.post("/api/v1/document-search", json={"query": "西湖"}).status_code == 503
        identifier = uuid4()
        assert client.get(f"/api/v1/documents/{identifier}/index").status_code == 503
        assert client.post(f"/api/v1/documents/{identifier}/index").status_code == 503


"""完整链路测试函数：只替换收费模型HTTP，数据库和向量数据库均使用真实服务。"""


def test_index_resume_search_and_owner(
    store_engine: Engine,
    tmp_path: Path,
) -> None:
    from app.services.document.search import DocumentSearchService
    from app.services.document.vector_store import MilvusStore

    url = os.environ.get("TRAVELMIND_TEST_MILVUS_URL")
    if not url:
        pytest.skip("未配置TRAVELMIND_TEST_MILVUS_URL")
    settings = Settings(
        milvus_url=url,
        embedding_provider="test",
        embedding_model="test-vector",
        embedding_dimensions=2,
        embedding_version=uuid4().hex,
        embedding_base_url="https://example.com/v1",
        embedding_api_key=SecretStr("fake"),
    )
    batches: list[list[str]] = []
    fail_embedding = False

    """模型响应函数：西湖为横轴、美食为纵轴，让预期排序可手工核对。"""

    def respond(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        batches.append(texts)
        if fail_embedding:
            return httpx.Response(503, text="private-provider-error")
        return httpx.Response(
            200,
            json={
                "model": "test-vector",
                "data": [
                    {"index": index, "embedding": [1.0, 0.0] if "西湖" in value else [0.0, 1.0]}
                    for index, value in enumerate(texts)
                ],
            },
        )

    with (
        httpx.Client() as milvus_http,
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
    ):
        vectors = MilvusStore(settings, milvus_http)
        model = EmbeddingClient(settings, http)
        service = DocumentSearchService(store_engine, vectors, model, "local-demo")
        documents = DocumentService(store_engine, tmp_path, "local-demo")
        saved = documents.upload(
            BytesIO(("西湖\n\n" + "美食\n\n" * 10).encode()), "攻略.txt", "text/plain"
        )
        chunks = documents.generate_chunks(saved.document.id)
        try:
            assert service.status(saved.document.id).indexed == 0
            first = service.index_batch(saved.document.id)
            assert first.indexed == 8 and first.total == 11 and not first.complete
            # 新建服务模拟刷新后继续，已经写入的8段不得再次调用模型。
            resumed = DocumentSearchService(store_engine, vectors, model, "local-demo")
            from app.llm.embeddings import EmbeddingError

            fail_embedding = True
            with pytest.raises(EmbeddingError):
                resumed.index_batch(saved.document.id)
            assert resumed.status(saved.document.id).indexed == 8
            fail_embedding = False

            """丢失确认响应函数：Milvus已经写入成功，但调用方遇到网络中断。"""

            def uncertain_response(request: httpx.Request) -> httpx.Response:
                response = milvus_http.send(request)
                if request.url.path.endswith("/upsert"):
                    raise httpx.ReadTimeout("模拟确认丢失", request=request)
                return response

            from app.services.document.vector_store import MilvusError

            with httpx.Client(transport=httpx.MockTransport(uncertain_response)) as uncertain_http:
                uncertain = DocumentSearchService(
                    store_engine,
                    MilvusStore(settings, uncertain_http),
                    model,
                    "local-demo",
                )
                with pytest.raises(MilvusError):
                    uncertain.index_batch(saved.document.id)
            assert resumed.status(saved.document.id).complete
            assert [len(batch) for batch in batches] == [8, 3, 3]
            assert resumed.index_batch(saved.document.id).indexed == 11
            assert [len(batch) for batch in batches] == [8, 3, 3]
            hits = resumed.search("西湖散步", 5, saved.document.id).items
            assert hits[0].chunk.id == chunks.items[0].id
            assert hits[0].file_name == "攻略.txt" and "西湖" in hits[0].chunk.text
            other = DocumentSearchService(store_engine, vectors, model, "other-owner")
            assert other.search("西湖", 5).items == []
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as caught:
                other.index_batch(saved.document.id)
            assert caught.value.status_code == 404
            # 锁竞争立即返回409，不等待另一请求结束后再追加收费调用。
            from sqlalchemy import select

            from app.models.document import DocumentRecord

            with store_engine.begin() as connection:
                connection.execute(
                    select(DocumentRecord.id)
                    .where(
                        DocumentRecord.id == saved.document.id,
                    )
                    .with_for_update()
                )
                with pytest.raises(HTTPException) as conflict:
                    resumed.index_batch(saved.document.id)
                assert conflict.value.status_code == 409
            from fastapi.testclient import TestClient

            from app.api.document.search import get_search_service
            from app.main import create_app

            app = create_app(Settings(database_url=None))
            app.dependency_overrides[get_search_service] = lambda: resumed
            with TestClient(app) as client:
                assert client.get(f"/api/v1/documents/{saved.document.id}/index").json()["complete"]
                assert (
                    client.post("/api/v1/document-search", json={"query": " "}).status_code == 422
                )
                response = client.post("/api/v1/document-search", json={"query": "西湖"})
                assert response.status_code == 200
                assert response.json()["items"][0]["chunk"]["id"] == str(chunks.items[0].id)
            # 21个重复段落不能把后面的4个不同正文永远挡在候选范围外。
            repeated = documents.upload(
                BytesIO(("西湖相同正文\n\n" * 21 + "甲\n\n乙\n\n丙\n\n丁").encode()),
                "重复页眉.txt",
                "text/plain",
            )
            documents.generate_chunks(repeated.document.id)
            while not resumed.index_batch(repeated.document.id).complete:
                pass
            assert len(resumed.search("西湖", 5, repeated.document.id).items) == 5
        finally:
            # 只删除本测试随机版本对应的集合，不接触应用当前索引。
            response = milvus_http.post(
                url.rstrip("/") + "/v2/vectordb/collections/drop",
                json={"collectionName": vectors.collection},
            )
            assert response.json()["code"] == 0
