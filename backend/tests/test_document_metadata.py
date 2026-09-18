"""测试层：验证元数据保存、归属隔离以及向量检索前的候选过滤。"""

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Engine, insert, update

from app.models.document import DocumentRecord
from app.schemas.document.base import DocumentMetadata
from app.schemas.document.search import SearchRequest
from app.services.document.search import DocumentSearchService
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusStore

"""标题回查测试函数：命中标题后返回同节正文，不误接下一景点，也不重复正文。"""


def test_heading_hit_returns_own_body(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    saved = documents.upload(BytesIO(
        ("# 杭州\n\n## 西湖\n\n杭州西湖适合散步。\n\n## 空标题\n\n"
         "## 灵隐寺\n\n杭州灵隐寺位于山林。").encode()
    ), "景点.md", "text/markdown")
    chunks = documents.generate_chunks(saved.document.id).items
    title = next(c for c in chunks if c.text.strip() == "## 西湖")
    body = next(c for c in chunks if "适合散步" in c.text)
    empty = next(c for c in chunks if c.text.strip() == "## 空标题")
    vectors = SimpleNamespace(exists=lambda: True, search=lambda *a, **kw: [
        (title.id, .9), (body.id, .8), (empty.id, .7)])
    model = SimpleNamespace(embed=lambda _: SimpleNamespace(vectors=[[1., 0.]]))
    result = DocumentSearchService(store_engine, vectors, model, "owner-a").search("西湖", 5)
    assert [h.chunk.id for h in result.items] == [body.id]
    assert result.items[0].chunk.text == body.text
    assert result.items[0].score == .9


"""专类检索测试函数：聊天同时保留综合攻略，排除其他类别和其他城市的资料。"""


def test_category_can_include_general_guides(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    candidates = []
    for city, category, text in [("杭州", "景点", "杭州西湖介绍"),
                                 ("杭州", None, "杭州综合攻略"),
                                 ("杭州", "餐馆", "杭州餐馆介绍"),
                                 ("苏州", "景点", "苏州园林介绍")]:
        saved = documents.upload(BytesIO(text.encode()), text + ".txt", "text/plain")
        documents.update_metadata(saved.document.id, DocumentMetadata(city=city, category=category))
        candidates.append(documents.generate_chunks(saved.document.id).items[0])
    vectors = SimpleNamespace(exists=lambda: True, search=lambda *a, **kw: [
        (c.id, .9) for c in candidates if c.document_id in kw['document_ids']])
    model = SimpleNamespace(embed=lambda _: SimpleNamespace(vectors=[[1., 0.]]))
    service = DocumentSearchService(store_engine, vectors, model, "owner-a")
    metadata = DocumentMetadata(city="杭州", category="景点")
    assert len(service.search("杭州", 5, metadata=metadata).items) == 1
    result = service.search("杭州", 5, metadata=metadata, include_general=True)
    assert [h.chunk.id for h in result.items] == [c.id for c in candidates[:2]]

"""字段校验测试函数：拒绝旧标签和非法类别，空白城市保存为空。"""

def test_metadata_validation() -> None:
    for body in ({"owner_id": "other"}, {"review_status": "anything"},
                 {"category": "交通"}, {"city": "城" * 101}):
        with pytest.raises(ValidationError):
            DocumentMetadata.model_validate(body)
    assert DocumentMetadata(city="  ").city is None
    assert SearchRequest(query="西湖", city=" 杭州 ").city == "杭州"


"""保存隔离测试函数：更新只改显式字段，列表精确筛选，停用及其他归属不能更新。"""

def test_metadata_update_and_owner(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "owner-a")
    document = service.upload(BytesIO("西湖".encode()), "a.txt", "text/plain").document
    assert document.city is None and document.category is None
    service.update_metadata(document.id, DocumentMetadata(city="杭州", category="景点"))
    changed = service.update_metadata(document.id, DocumentMetadata(city="绍兴"))
    assert changed.city == "绍兴" and changed.category == "景点"
    assert len(service.list(metadata=DocumentMetadata(category="景点"))) == 1
    assert service.list(metadata=DocumentMetadata(city="杭州")) == []
    other = DocumentService(store_engine, tmp_path, "owner-b")
    assert other.list(metadata=DocumentMetadata(city="绍兴")) == []
    with pytest.raises(HTTPException) as denied:
        other.update_metadata(document.id, DocumentMetadata(city="宁波"))
    assert denied.value.status_code == 404
    service.update_metadata(document.id, DocumentMetadata(category=None))
    assert service.read(document.id).category is None
    with store_engine.begin() as connection:
        connection.execute(update(DocumentRecord).where(DocumentRecord.id == document.id)
                           .values(status="deleting", error_message="待清理"))
    with pytest.raises(HTTPException) as stopped:
        service.update_metadata(document.id, DocumentMetadata(city="宁波"))
    assert stopped.value.status_code == 409


"""候选过滤测试函数：前201段均不符条件时仍能找到后面的目标，且不传元数据字符串。"""

def test_metadata_filters_before_vector_top_k(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    noise = documents.upload(BytesIO(("无关\n\n" * 201).encode()), "noise.txt", "text/plain")
    target = documents.upload(BytesIO("西湖目标".encode()), "target.txt", "text/plain")
    noise_chunks = documents.generate_chunks(noise.document.id)
    target_chunk = documents.generate_chunks(target.document.id).items[0]
    documents.update_metadata(target.document.id, DocumentMetadata(city='杭州" or true',
                                                                  category="景点"))
    calls = []

    """替代检索函数：模拟无关候选排在前面，只有向量前置过滤才能拿到目标。"""
    def search(owner, vector, limit, document_id=None, *, document_ids=None):
        calls.append(document_ids)
        candidates = [(item, .99) for item in noise_chunks.items] * 5 + [(target_chunk, .8)]
        return [(item.id, score) for item, score in candidates
                if document_ids is None or item.document_id in document_ids][:limit]

    vectors = SimpleNamespace(exists=lambda: True, search=search)
    model = SimpleNamespace(embed=lambda _: SimpleNamespace(vectors=[[1., 0.]]))
    service = DocumentSearchService(store_engine, vectors, model, "owner-a")
    result = service.search("西湖", 5, metadata=DocumentMetadata(city='杭州" or true',
                                                             category="景点"))
    assert [hit.chunk.id for hit in result.items] == [target_chunk.id]
    assert calls and all(ids == [target.document.id] for ids in calls)
    calls.clear()
    assert service.search("西湖", 5, metadata=DocumentMetadata(city="不存在")).items == []
    assert calls == []
    with store_engine.begin() as connection:
        connection.execute(update(DocumentRecord).where(DocumentRecord.id == target.document.id)
                           .values(status="deleting", error_message="待清理"))
    assert service.search("西湖", 5, metadata=DocumentMetadata(city='杭州" or true')).items == []
    assert calls == []


"""HTTP合同测试函数：列表和搜索校验标签，PATCH不能更改归属或正文。"""

def test_metadata_http_contract(store_engine: Engine, tmp_path: Path) -> None:
    from app.api.document.routes import get_document_service
    from app.config import Settings
    from app.main import create_app
    from tests.helpers import authenticated_client as TestClient

    documents = DocumentService(store_engine, tmp_path, "owner-a")
    saved = documents.upload(BytesIO("绍兴".encode()), "test.txt", "text/plain").document
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_document_service] = lambda: documents
    with TestClient(app) as client:
        path = f"/api/v1/documents/{saved.id}/metadata"
        assert client.patch(path, json={"city": "绍兴", "category": "景点"}).status_code == 200
        assert len(client.get("/api/v1/documents", params={"city": "绍兴"}).json()) == 1
        assert client.get("/api/v1/documents", params={"city": "杭州"}).json() == []
        for body in ({"owner_id": "owner-b"}, {"review_status": "wrong"}, {"city": "a" * 101}):
            assert client.patch(path, json=body).status_code == 422
        assert client.get("/api/v1/documents", params={"category": "wrong"}).status_code == 422
        assert client.patch(path, json={"city": " "}).json()["city"] is None


"""向量表达式测试函数：JSON转义归属和值，空候选不发请求，沿用已有集合。"""

def test_vector_document_filter_is_escaped() -> None:
    from app.config import Settings

    identifier = uuid4()
    payloads = []

    """检索响应函数：只记录实际提交的过滤条件，不连接真实向量库。"""
    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": []})

    settings = Settings(milvus_url="http://localhost:19530", embedding_provider="test",
                        embedding_base_url="https://example.com/v1", embedding_model="test",
                        embedding_dimensions=2, embedding_version="metadata-test")
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        vectors = MilvusStore(settings, http)
        owner = 'owner" or true or owner_id == "'
        assert vectors.search(owner, [1., 0.], 5, document_ids=[]) == []
        assert payloads == []
        vectors.search(owner, [1., 0.], 5, document_ids=[identifier])
        assert payloads[0]["filter"] == (
            "owner_id == " + json.dumps(owner) + " and document_id in "
            + json.dumps([str(identifier)])
        )


"""大集合测试函数：超过500份资料时分批限定候选，按全局分数合并而不漏掉最后一批。"""

def test_candidate_document_batches_keep_global_ranking(store_engine: Engine, tmp_path: Path):
    ids = [uuid4() for _ in range(501)]
    with store_engine.begin() as connection:
        connection.execute(insert(DocumentRecord), [{
            "id": identifier, "owner_id": "owner-a", "file_name": "a.txt",
            "storage_name": f"{identifier.hex}.txt", "mime_type": "text/plain",
            "content_hash": identifier.hex.ljust(64, "0"), "size_bytes": 1,
            "status": "parsed", "error_message": None,
            "sections_json": [{"text": "正文", "order": 1}], "warnings_json": [],
        } for identifier in ids])
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    first = documents.generate_chunks(ids[0]).items[0]
    last = documents.generate_chunks(ids[-1]).items[0]
    batches = []

    """分批响应函数：最后一批包含最高分，要求服务合并而非直接返回第一批。"""
    def search(owner, vector, limit, document_id=None, *, document_ids=None):
        batches.append(document_ids)
        return [(chunk.id, score) for chunk, score in ((last, .9), (first, .5))
                if chunk.document_id in document_ids][:limit]

    service = DocumentSearchService(
        store_engine, SimpleNamespace(exists=lambda: True, search=search),
        SimpleNamespace(embed=lambda _: SimpleNamespace(vectors=[[1., 0.]])), "owner-a",
    )
    assert service.search("正文", 1).items[0].chunk.id == last.id
    assert sorted(len(batch) for batch in batches) == [1, 500]
    assert set(identifier for batch in batches for identifier in batch) == set(ids)
