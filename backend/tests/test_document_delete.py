"""测试层：用真实数据库验证删除中断、重试、归属隔离和文件边界。"""

import os
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.api.document.routes import get_document_service
from app.config import Settings
from app.main import create_app
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.services.document.search import DocumentSearchService
from app.services.document.service import DocumentService
from app.services.document.vector_store import MilvusError

"""删除重试测试函数：向量故障后先停用资料，重建服务后仍能清理全部内容。"""

def test_delete_failure_hides_search_and_retry_finishes(store_engine: Engine, tmp_path: Path):
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO("杭州西湖散步".encode()), "攻略.txt", "text/plain")
    identifier = saved.document.id
    chunk = service.generate_chunks(identifier).items[0]
    vectors = Mock()
    vectors.exists.return_value = True
    vectors.search.return_value = [(chunk.id, 0.9)]
    model = Mock()
    model.embed.return_value.vectors = [[1.0, 0.0]]
    search = DocumentSearchService(store_engine, vectors, model, "local-demo")
    assert search.search("西湖", 5).items
    cleanup = Mock(side_effect=MilvusError("向量服务暂不可用"))
    with pytest.raises(MilvusError):
        service.delete(identifier, cleanup)
    assert service.read(identifier).status == "deleting"
    assert service.list()[0].error_message
    assert search.search("西湖", 5).items == []
    with pytest.raises(HTTPException) as blocked:
        search.index_batch(identifier)
    assert blocked.value.status_code == 409
    # 重传相同文件不能让等待清理的旧档案重新参与检索。
    duplicate = service.upload(BytesIO("杭州西湖散步".encode()), "新名字.txt", "text/plain")
    assert duplicate.document.status == "deleting"
    cleanup.side_effect = None
    DocumentService(store_engine, tmp_path, "local-demo").delete(identifier, cleanup)
    assert service.list() == [] and list(tmp_path.iterdir()) == []
    with store_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(DocumentChunkRecord)) == 0
    # 网络断开后重复提交删除也成功，不重复操作已不存在的文件。
    service.delete(identifier, cleanup)
    replacement = service.upload(BytesIO("杭州西湖散步".encode()), "攻略.txt", "text/plain")
    assert replacement.document.id != identifier


"""归属和锁测试函数：不能删除他人的资料，也不能越过正在写索引的行锁。"""

def test_delete_owner_and_index_lock(store_engine: Engine, tmp_path: Path):
    service = DocumentService(store_engine, tmp_path, "owner-a")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    cleanup = Mock()
    DocumentService(store_engine, tmp_path, "owner-b").delete(saved.document.id, cleanup)
    assert service.read(saved.document.id).status == "parsed"
    cleanup.assert_not_called()
    with store_engine.begin() as connection:
        connection.execute(select(DocumentRecord.id).with_for_update())
        with pytest.raises(HTTPException) as caught:
            service.delete(saved.document.id, cleanup)
        assert caught.value.status_code == 409
    assert service.read(saved.document.id).status == "parsed"


"""磁盘故障测试函数：文件被占用时保留停用记录，下次可重试，不返回虚假的成功。"""

def test_delete_disk_failure_is_retryable(store_engine: Engine, tmp_path: Path, monkeypatch):
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", Mock(side_effect=PermissionError("private-path")))
        with pytest.raises(HTTPException) as caught:
            service.delete(saved.document.id)
        assert caught.value.status_code == 503
        assert "private-path" not in str(caught.value.detail)
    assert service.read(saved.document.id).status == "deleting"
    service.delete(saved.document.id)
    assert service.list() == []


"""路径边界测试函数：数据库内的异常文件名不能让删除越出上传目录。"""

def test_delete_rejects_path_outside_uploads(store_engine: Engine, tmp_path: Path):
    uploads = tmp_path / "uploads"
    outside = tmp_path / "keep.txt"
    outside.write_text("保留", encoding="utf-8")
    service = DocumentService(store_engine, uploads, "local-demo")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    with store_engine.begin() as connection:
        connection.execute(DocumentRecord.__table__.update().values(storage_name="../keep.txt"))
    with pytest.raises(HTTPException):
        service.delete(saved.document.id)
    assert outside.read_text(encoding="utf-8") == "保留"
    assert service.read(saved.document.id).status == "deleting"


"""删除接口测试函数：未切分的资料无需模型配置；重复删除返回204，详情变为404。"""

def test_delete_http_without_model_configuration(store_engine: Engine, tmp_path: Path):
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    app = create_app(Settings(database_url=None, milvus_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    with TestClient(app) as client:
        path = f"/api/v1/documents/{saved.document.id}"
        assert client.delete(path).status_code == 204
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404


"""缺配置测试函数：已切分资料不能因缺少向量连接而跳过清理，恢复配置后可继续。"""

def test_delete_indexed_document_keeps_record_without_config(store_engine: Engine, tmp_path: Path):
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    service.generate_chunks(saved.document.id)
    app = create_app(Settings(database_url=None, milvus_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    with TestClient(app) as client:
        path = f"/api/v1/documents/{saved.document.id}"
        assert client.delete(path).status_code == 503
        assert client.get(path).json()["status"] == "deleting"
    assert list(tmp_path.iterdir())


"""迁移往返测试函数：空闲资料可回滚再升级；待清理资料必须阻止不安全回滚。"""

def test_delete_migration_round_trip(store_engine: Engine, tmp_path: Path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import IntegrityError

    config = Config(toml_file=str(Path(__file__).resolve().parents[1] / "pyproject.toml"))
    with store_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0004_document_chunks")
        command.upgrade(config, "head")
        command.check(config)
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"original"), "test.txt", "text/plain")
    service.generate_chunks(saved.document.id)
    with pytest.raises(HTTPException):
        service.delete(saved.document.id)
    with store_engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(IntegrityError):
            command.downgrade(config, "0004_document_chunks")
    assert service.read(saved.document.id).status == "deleting"


"""真实向量删除测试函数：删除同一资料的新旧模型向量，其他资料和归属仍可读取。"""

def test_delete_purges_versions_real_milvus(store_engine: Engine, tmp_path: Path):
    from app.services.document.vector_store import MilvusStore

    url = os.environ.get("TRAVELMIND_TEST_MILVUS_URL")
    if not url:
        pytest.skip("未配置TRAVELMIND_TEST_MILVUS_URL")
    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"delete fixture"), "test.txt", "text/plain")
    chunk = service.generate_chunks(saved.document.id).items[0]
    other_document, other_chunk, other_owner_chunk = uuid4(), uuid4(), uuid4()
    with httpx.Client() as http:
        stores = [MilvusStore(Settings(
            milvus_url=url, embedding_provider="test", embedding_model="delete-test",
            embedding_base_url="https://example.com/v1", embedding_dimensions=2,
            embedding_version=uuid4().hex,
        ), http) for _ in range(2)]
        try:
            for store in stores:
                store.ensure_collection()
                store.upsert("local-demo", saved.document.id, [(chunk.id, [1.0, 0.0])])
                store.upsert("local-demo", other_document, [(other_chunk, [1.0, 0.0])])
                store.upsert("other-owner", saved.document.id, [(other_owner_chunk, [1.0, 0.0])])
                assert store.indexed_ids("local-demo", saved.document.id) == {chunk.id}
            service.delete(saved.document.id, stores[0].delete_document)
            for store in stores:
                assert store.indexed_ids("local-demo", saved.document.id) == set()
                assert store.indexed_ids("local-demo", other_document) == {other_chunk}
                assert store.indexed_ids("other-owner", saved.document.id) == {other_owner_chunk}
            assert service.list() == []
            assert list(tmp_path.iterdir()) == []
        finally:
            # 仅移除本用例随机版本创建的两个测试集合。
            for store in stores:
                response = http.post(str(url).rstrip("/") + "/v2/vectordb/collections/drop",
                                     json={"collectionName": store.collection})
                assert response.json()["code"] == 0
