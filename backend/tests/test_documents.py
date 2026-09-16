"""资料存储和HTTP集成测试：使用真实PostgreSQL临时schema及临时上传目录。

检验重复上传、跨owner读取、失败文件记录和重建服务后恢复，不能用内存字典代替。
"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from app.api.document.routes import get_document_service
from app.config import Settings
from app.main import create_app
from app.services.document.service import DocumentService
from tests.helpers import authenticated_client as TestClient

"""只封装真实服务调用的公共入参，不替换哈希、读取器或数据库逻辑。"""


def upload(service: DocumentService, text: str = "杭州\n\n西湖", name: str = "攻略.txt"):
    return service.upload(BytesIO(text.encode()), name, "text/plain")


"""重复内容只存一份；新服务实例可读取旧内容，原始文件名不能决定磁盘路径。"""


def test_persistence_duplicate_and_safe_filename(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "local-demo")
    first = upload(service, name="../../攻略.txt")
    second = upload(service, name="另一个名字.txt")
    assert first.document.status == "parsed" and not first.duplicate
    assert first.document.file_name == "攻略.txt"
    assert second.duplicate and second.document.id == first.document.id
    restored = DocumentService(store_engine, tmp_path, "local-demo").read(first.document.id)
    assert [part.text for part in restored.sections] == ["杭州", "西湖"]
    assert len(service.list()) == 1
    assert [path.name for path in tmp_path.iterdir()] == [f"{first.document.id.hex}.txt"]


"""同一内容可属于不同owner；猜到另一人的UUID也读不到正文。"""


def test_owner_filter_is_enforced(store_engine: Engine, tmp_path: Path) -> None:
    from fastapi import HTTPException

    first = DocumentService(store_engine, tmp_path, "owner-a")
    second = DocumentService(store_engine, tmp_path, "owner-b")
    saved = upload(first)
    assert second.list() == []
    with pytest.raises(HTTPException) as error:
        second.read(saved.document.id)
    assert error.value.status_code == 404
    assert upload(second).document.id != saved.document.id


"""同时上传同一内容时，数据库唯一约束仍保证一个记录和一个原文件。"""


def test_concurrent_duplicate_has_one_file(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "local-demo")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(service), range(2)))
    assert results[0].document.id == results[1].document.id
    assert sorted(result.duplicate for result in results) == [False, True]
    assert len(list(tmp_path.iterdir())) == 1


"""通过真实字段长度约束制造写库失败，验证已写的新原文件会清理且不会留下档案。"""

def test_database_write_failure_cleans_new_file(store_engine: Engine, tmp_path: Path) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    service = DocumentService(store_engine, tmp_path, "x" * 101)
    with pytest.raises(SQLAlchemyError):
        upload(service)
    assert list(tmp_path.iterdir()) == []
    assert service.list() == []


"""把上传目录替换为普通文件模拟磁盘不可写，必须返回可读错误且不创建成功记录。"""

def test_disk_failure_does_not_create_record(store_engine: Engine, tmp_path: Path) -> None:
    from fastapi import HTTPException

    target = tmp_path / "not-a-directory"
    target.write_text("保留此文件", encoding="utf-8")
    service = DocumentService(store_engine, target, "local-demo")
    with pytest.raises(HTTPException) as error:
        upload(service)
    assert error.value.status_code == 503
    assert target.read_text(encoding="utf-8") == "保留此文件"
    assert service.list() == []


"""HTTP上传成功、刷新读取、失败记录、统一错误，以及列表分页都走真实实现。"""


def test_upload_read_errors_and_refresh(store_engine: Engine, tmp_path: Path) -> None:
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_document_service] = lambda: DocumentService(
        store_engine,
        tmp_path,
        "local-demo",
        max_bytes=100,
    )
    with TestClient(app) as client:
        saved = client.post(
            "/api/v1/documents", files={"file": ("攻略.md", "# 杭州", "text/markdown")}
        )
        assert saved.status_code == 201
        body = saved.json()
        document_id = body["document"]["id"]
        assert body["document"]["status"] == "parsed"
        duplicate = client.post(
            "/api/v1/documents", files={"file": ("攻略.md", "# 杭州", "text/markdown")}
        )
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
        # 缺文件、格式冒充、超大文件必须返回统一的请求编号和可读消息。
        for files, status in [
            ({"file": ("a.exe", b"MZ", "application/octet-stream")}, 415),
            ({"file": ("a.pdf", b"abc", "image/png")}, 415),
            ({"file": ("a.txt", b"x" * 101, "text/plain")}, 413),
            ({"file": ("a.txt", b"", "text/plain")}, 400),
        ]:
            response = client.post("/api/v1/documents", files=files)
            assert response.status_code == status
            assert response.json()["error"]["message"]
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
        failed = client.post(
            "/api/v1/documents",
            files={"file": ("坏文件.docx", b"broken", "application/octet-stream")},
        )
        assert failed.status_code == 201
        assert failed.json()["document"]["status"] == "failed"
        assert failed.json()["document"]["sections"] == []
        assert "损坏" in failed.json()["document"]["error_message"]
        assert len(client.get("/api/v1/documents?limit=1&offset=1").json()) == 1
    with TestClient(app) as refreshed:
        detail = refreshed.get(f"/api/v1/documents/{document_id}").json()
        assert detail["sections"][0]["text"] == "# 杭州"
        assert "storage_name" not in detail and "owner_id" not in detail
        assert len(refreshed.get("/api/v1/documents").json()) == 2
        assert refreshed.get(f"/api/v1/documents/{uuid4()}").status_code == 404
        assert refreshed.get("/api/v1/documents?limit=0").status_code == 422


"""没有配置数据库时不能伪装保存成功，也不能退回刷新即失效的内存列表。"""


def test_missing_database_returns_503() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        assert client.get("/api/v1/documents").status_code == 503


"""文件名筛选测试函数：先在整个归属内查找，再分页；符号按原字匹配，不扩大范围。"""

def test_list_filters_filename_before_pagination(store_engine: Engine, tmp_path: Path):
    service = DocumentService(store_engine, tmp_path, "local-demo")
    names = ["杭州-西湖-ChinaTravel.md", "杭州-灵隐-LvBanGPT.md", "苏州攻略.md", "票价100%_参考.md"]
    for name in names:
        upload(service, text=name, name=name)
    upload(DocumentService(store_engine, tmp_path, "other"), text="另一归属", name=names[0])
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    with TestClient(app) as client:
        path = "/api/v1/documents"
        items = client.get(path, params={"q": " 杭州 ", "limit": 1, "offset": 1}).json()
        assert [item["file_name"] for item in items] == [names[0]]
        assert "sections" not in items[0]
        for query, expected in [("chinatravel", [names[0]]), ("%_", [names[3]]), ("不存在", [])]:
            result = client.get(path, params={"q": query}).json()
            assert [item["file_name"] for item in result] == expected
        assert len(client.get(path, params={"q": "   "}).json()) == 4
        assert client.get(path, params={"q": "x" * 256}).status_code == 422


"""重新解析测试函数：临时读取失败后沿用原档案重试，成功后重复请求不重建片段。"""

def test_retry_parse_recovers_without_reupload(store_engine: Engine, tmp_path: Path, monkeypatch):
    from app.services.document.parser import DocumentParseError

    service = DocumentService(store_engine, tmp_path, "local-demo")

    """临时失败函数：模拟第一次读取故障，磁盘中仍保存真实且完整的原文件。"""

    def fail_once(*args):
        raise DocumentParseError("读取暂时失败")

    with monkeypatch.context() as patch:
        patch.setattr("app.services.document.service.parse_document", fail_once)
        saved = upload(service)
    assert saved.document.status == "failed"
    recovered = service.retry_parse(saved.document.id)
    assert recovered.status == "parsed" and recovered.error_message is None
    assert recovered.id == saved.document.id
    assert recovered.content_hash == saved.document.content_hash
    assert [part.text for part in recovered.sections] == ["杭州", "西湖"]
    chunks = service.generate_chunks(recovered.id)
    with monkeypatch.context() as patch:
        patch.setattr("app.services.document.service.parse_document", fail_once)
        assert service.retry_parse(recovered.id).status == "parsed"
    assert service.list_chunks(recovered.id) == chunks


"""重试边界测试函数：损坏文件仍失败，跨归属和正在清理的资料不能重新解析。"""

def test_retry_parse_failure_owner_and_deleting(store_engine: Engine, tmp_path: Path):
    from fastapi import HTTPException
    from sqlalchemy import update

    from app.models.document import DocumentRecord

    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"broken"), "test.docx", "application/octet-stream")
    result = service.retry_parse(saved.document.id)
    assert result.status == "failed" and result.error_message and result.sections == []
    with pytest.raises(HTTPException) as forbidden:
        DocumentService(store_engine, tmp_path, "other-owner").retry_parse(saved.document.id)
    assert forbidden.value.status_code == 404
    with store_engine.begin() as connection:
        connection.execute(update(DocumentRecord).values(status="deleting", error_message="待清理"))
    with pytest.raises(HTTPException) as deleting:
        service.retry_parse(saved.document.id)
    assert deleting.value.status_code == 409


"""原文件检查测试函数：缺失、替换或异常路径保持失败，不能把另一份文件当作原件。"""

@pytest.mark.parametrize("problem", ["missing", "changed", "outside"])
def test_retry_parse_checks_original_file(store_engine: Engine, tmp_path: Path, problem: str):
    from sqlalchemy import update

    from app.models.document import DocumentRecord

    directory = tmp_path / "uploads"
    service = DocumentService(store_engine, directory, "local-demo")
    saved = service.upload(BytesIO(b"broken"), "test.docx", "application/octet-stream")
    path = directory / f"{saved.document.id.hex}.docx"
    if problem == "missing":
        path.unlink()
    elif problem == "changed":
        path.write_bytes(b"different")
    else:
        (tmp_path / "outside.docx").write_bytes(b"broken")
        with store_engine.begin() as connection:
            connection.execute(update(DocumentRecord).values(storage_name="../outside.docx"))
    result = service.retry_parse(saved.document.id)
    assert result.status == "failed"
    assert result.sections == []
    assert any(word in result.error_message for word in ("原文件", "路径"))
    assert service.read(result.id).error_message == result.error_message


"""重试接口测试函数：无模型配置也能重读；被占用的资料立即409，不排队重复解析。"""

def test_retry_parse_http_and_lock(store_engine: Engine, tmp_path: Path):
    from sqlalchemy import select

    from app.models.document import DocumentRecord

    service = DocumentService(store_engine, tmp_path, "local-demo")
    saved = service.upload(BytesIO(b"broken"), "test.pdf", "application/octet-stream")
    app = create_app(Settings(database_url=None, milvus_url=None))
    app.dependency_overrides[get_document_service] = lambda: service
    with TestClient(app) as client:
        path = f"/api/v1/documents/{saved.document.id}/retry"
        assert client.post(path).json()["status"] == "failed"
        with store_engine.begin() as connection:
            connection.execute(select(DocumentRecord.id).with_for_update())
            assert client.post(path).status_code == 409


"""超大上传必须在multipart读取和服务依赖执行前拒绝，而不是写完整个临时文件再查大小。"""


@pytest.mark.parametrize("path", ["/api/v1/documents", "/api/v1/admin/documents"])
@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_request_is_rejected_before_database_dependency(chunked: bool, path: str) -> None:
    body = b"x" * (21 * 1024 * 1024)
    with TestClient(create_app(Settings(database_url=None))) as client:
        response = client.post(
            path,
            # 迭代器上传不带Content-Length，必须由实际字节计数拦截。
            content=iter([body[: 10 * 1024 * 1024], body[10 * 1024 * 1024 :]]) if chunked else body,
            headers={"Content-Type": "multipart/form-data; boundary=test"},
        )
        assert response.status_code == 413
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
