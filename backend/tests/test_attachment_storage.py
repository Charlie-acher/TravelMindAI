"""测试层：验证私人附件的输入检查、会话隔离、去重与受保护原件。"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.attachments import router
from app.api.auth import COOKIE_NAME
from app.api.document.upload_limits import DocumentUploadLimitMiddleware
from app.config import Settings
from app.models.document import DocumentRecord
from app.schemas.attachment import AttachmentAnalysis, AttachmentWaypoint
from app.services.attachment.storage import AttachmentService, validate_upload
from app.services.auth import AuthService
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID

"""非法输入测试函数：文件名、真实格式和大小全部由服务端校验。"""

@pytest.mark.parametrize("filename,content,mime,status", [
    ("../a.txt", b"hello", "text/plain", 422),
    ("C:\\a.txt", b"hello", "text/plain", 422),
    ("a.html", b"hello", "text/html", 422),
    ("a.png", b"not a png", "image/png", 422),
    ("a.pdf", b"not a pdf", "application/pdf", 422),
    ("a.txt", b"", "text/plain", 422),
    ("a.txt", b"\x00bad", "text/plain", 422),
    ("a.txt", b"hello", "text/html", 422),
    ("a.txt", b"a" * (10 * 1024 * 1024 + 1), "text/plain", 413),
], ids=["traversal", "windows-path", "unsupported", "fake-png", "fake-pdf", "empty",
        "binary-text", "wrong-mime", "too-large"])
def test_reject_invalid_upload(filename, content, mime, status):
    with pytest.raises(HTTPException) as caught:
        validate_upload(BytesIO(content), filename, mime)
    assert caught.value.status_code == status


"""图片测试函数：损坏图、扩展名不一致和尺寸过大都不能保存。"""

def test_image_content_validation():
    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (24, 24), "blue").save(stream, format="PNG")
    content = stream.getvalue()
    assert validate_upload(BytesIO(content), "路线.png", "image/png")[0] == content
    for bad, name in ((content[:-20], "a.png"), (content, "a.jpg")):
        with pytest.raises(HTTPException):
            validate_upload(BytesIO(bad), name, "application/octet-stream")


"""图片边界测试函数：验证像素数和单边大小，防止压缩图片占用过多内存。"""

@pytest.mark.parametrize("size", [(10001, 1), (5000, 4001)])
def test_image_dimensions_are_bounded(size):
    from PIL import Image

    stream = BytesIO()
    Image.new("L", size).save(stream, format="PNG")
    with pytest.raises(HTTPException) as caught:
        validate_upload(BytesIO(stream.getvalue()), "large.png", "image/png")
    assert caught.value.status_code == 422


"""支持格式测试函数：真实JPEG、WebP、文字PDF和DOCX都能通过上传校验。"""

def test_supported_formats_are_readable():
    from docx import Document
    from PIL import Image

    from tests.test_document_parser import pdf_bytes

    for format_name, suffix, mime in (("JPEG", "jpg", "image/jpeg"),
                                     ("WEBP", "webp", "image/webp")):
        stream = BytesIO()
        Image.new("RGB", (24, 24), "blue").save(stream, format=format_name)
        assert validate_upload(BytesIO(stream.getvalue()), f"route.{suffix}", mime)[2] == mime
    document = Document()
    document.add_paragraph("杭州路线")
    word = BytesIO()
    document.save(word)
    assert validate_upload(BytesIO(word.getvalue()), "路线.docx", "")[1] == ".docx"
    assert validate_upload(BytesIO(pdf_bytes()), "路线.pdf", "")[1] == ".pdf"
    for suffix in ("txt", "md", "markdown"):
        result = validate_upload(BytesIO("杭州路线".encode()), f"路线.{suffix}", "")
        assert result[1] == f".{suffix}"


"""附件环境函数：每例使用真实隔离数据库和自己的临时目录。"""

@pytest.fixture
def attachments(store_engine, tmp_path):
    session = TripService(store_engine).create_session("私人路线", user_id=TEST_USER_ID)
    return AttachmentService(store_engine, tmp_path), session.id


"""去重测试函数：同一会话复用原件，不进入公共资料表。"""

def test_upload_deduplicates_only_within_session(attachments, store_engine, tmp_path):
    service, session_id = attachments
    first = service.upload(session_id, BytesIO(b"Hangzhou route"), "route.txt", "text/plain")
    again = service.upload(session_id, BytesIO(b"Hangzhou route"), "renamed.md", "text/markdown")
    other = TripService(store_engine).create_session("另一个会话", user_id=TEST_USER_ID)
    separate = service.upload(other.id, BytesIO(b"Hangzhou route"), "route.txt", "text/plain")
    assert first.id == again.id != separate.id
    assert first.status == "uploaded" and first.analysis is None
    assert len(list(tmp_path.iterdir())) == 2
    assert service.read_content(session_id, first.id) == (b"Hangzhou route", "text/plain")
    assert [item.id for item in service.list(session_id)] == [first.id]
    with pytest.raises(HTTPException) as caught:
        service.get(other.id, first.id)
    assert caught.value.status_code == 404
    with Session(store_engine) as unit:
        assert unit.scalar(select(func.count()).select_from(DocumentRecord)) == 0


"""会话存在测试函数：所有服务入口都拒绝不存在的会话。"""

def test_missing_session_is_rejected(attachments):
    service, _ = attachments
    missing, identifier = uuid4(), uuid4()
    calls = [
        lambda: service.upload(missing, BytesIO(b"route"), "a.txt", "text/plain"),
        lambda: service.list(missing), lambda: service.get(missing, identifier),
        lambda: service.read_content(missing, identifier),
        lambda: service.save_analysis(missing, identifier, None, "识别失败"),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as caught:
            call()
        assert caught.value.status_code == 404


"""结果保存测试函数：成功、待确认和失败状态随分析内容一起改变。"""

def test_analysis_state_and_database_constraints(attachments, store_engine):
    service, session_id = attachments
    item = service.upload(session_id, BytesIO(b"route"), "a.txt", "text/plain")
    analysis = AttachmentAnalysis(summary="路线", waypoints=[
        AttachmentWaypoint(name="西湖", evidence="西湖", needs_confirmation=True),
    ])
    failed = service.save_analysis(session_id, item.id, None, "暂时不可用")
    assert failed.status == "failed" and failed.analysis is None
    assert service.save_analysis(session_id, item.id, analysis, None).status == "needs_confirmation"
    late_failure = service.save_analysis(session_id, item.id, None, "较晚返回的失败")
    assert late_failure.status == "needs_confirmation" and late_failure.analysis == analysis
    second = service.upload(session_id, BytesIO(b"other route"), "b.txt", "text/plain")
    ready = service.save_analysis(
        session_id, second.id, AttachmentAnalysis(summary="文字摘要"), None,
    )
    assert ready.status == "ready" and ready.error_message is None
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), store_engine.begin() as connection:
        connection.execute(text(
            "UPDATE conversation_attachments SET status='failed', error_message=NULL WHERE id=:id"
        ), {"id": item.id})


"""并发测试函数：两个同时上传的相同原件只落一条记录和一个文件。"""

def test_concurrent_upload_deduplicates(attachments, tmp_path):
    service, session_id = attachments
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: service.upload(
            session_id, BytesIO(b"same route"), "a.txt", "text/plain",
        ), range(2)))
    assert results[0].id == results[1].id
    assert len(list(tmp_path.iterdir())) == 1


"""事务失败测试函数：数据库提交失败后清理本次写入的原件。"""

def test_database_failure_cleans_new_file(attachments, store_engine, tmp_path):
    from sqlalchemy import event

    service, session_id = attachments

    def fail_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO conversation_attachments"):
            raise RuntimeError("injected insert failure")

    event.listen(store_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            service.upload(session_id, BytesIO(b"route"), "a.txt", "text/plain")
    finally:
        event.remove(store_engine, "before_cursor_execute", fail_insert)
    assert list(tmp_path.iterdir()) == []


"""接口权限测试函数：原件、列表和详情都通过真实Cookie与会话归属检查。"""

def test_http_ownership_and_protected_download(store_engine, tmp_path):
    auth = AuthService(store_engine)
    alice = auth.create_user("attachment-alice", "testpass123")
    auth.create_user("attachment-bob", "testpass123")
    trip = TripService(store_engine).create_session("路线", user_id=alice.id)
    app = FastAPI()
    app.state.database_engine = store_engine
    app.state.settings = Settings(document_upload_dir=tmp_path)
    app.include_router(router, prefix="/api/v1")
    base = f"/api/v1/sessions/{trip.id}/attachments"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        client.cookies.set(COOKIE_NAME, auth.login("attachment-alice", "testpass123")[1])
        uploaded = client.post(base, files={"file": ("路线.txt", b"route", "text/plain")},
                               headers={"X-Requested-With": "TravelMindAI"})
        assert uploaded.status_code == 201, uploaded.text
        identifier = uploaded.json()["id"]
        download = client.get(f"{base}/{identifier}/content")
        assert download.content == b"route"
        assert download.headers["Cache-Control"] == "no-store"
        assert download.headers["X-Content-Type-Options"] == "nosniff"
        assert download.headers["Content-Disposition"].startswith("attachment;")
        assert client.get(f"{base}/{identifier}/content?preview=true").headers[
            "Content-Disposition"
        ].startswith("attachment;")
        from tests.test_document_parser import pdf_bytes

        pdf = client.post(base, files={"file": ("路线.pdf", pdf_bytes(), "application/pdf")},
                          headers={"X-Requested-With": "TravelMindAI"})
        assert pdf.status_code == 201
        pdf_path = f"{base}/{pdf.json()['id']}/content?preview=true"
        preview = client.get(pdf_path)
        assert preview.headers["Content-Disposition"].startswith("inline;")
        assert preview.headers["Content-Type"] == "application/pdf"
        assert preview.headers["Cache-Control"] == "no-store"
        assert "object-src 'self'" in preview.headers["Content-Security-Policy"]
        assert "sandbox" not in preview.headers["Content-Security-Policy"]
        assert preview.content == pdf_bytes()
        client.cookies.set(COOKIE_NAME, auth.login("attachment-bob", "testpass123")[1])
        assert client.get(pdf_path).status_code == 404
        for path in (base, f"{base}/{identifier}", f"{base}/{identifier}/content"):
            assert client.get(path).status_code == 404
        assert client.post(base, files={"file": ("a.txt", b"route", "text/plain")},
                           headers={"X-Requested-With": "TravelMindAI"}).status_code == 404


"""PDF边界测试函数：30MB可进入解析，多一个字节拒绝，其他格式仍限制10MiB。"""

def test_pdf_size_boundary(monkeypatch):
    parsed = []
    monkeypatch.setattr("app.services.attachment.storage.parse_document",
                        lambda content, suffix: parsed.append(len(content)))
    content = b"%PDF-" + b" " * (30_000_000 - 5)
    assert validate_upload(BytesIO(content), "route.PDF", "application/pdf")[0] == content
    assert parsed == [30_000_000]
    with pytest.raises(HTTPException) as caught:
        validate_upload(BytesIO(content + b" "), "route.pdf", "application/pdf")
    assert caught.value.status_code == 413
    assert parsed == [30_000_000]


"""数据库边界测试函数：迁移只允许PDF达到30MB，不能放宽其他类型。"""

def test_pdf_database_size_boundary(attachments, store_engine):
    from sqlalchemy.exc import IntegrityError

    from tests.test_document_parser import pdf_bytes

    service, session_id = attachments
    item = service.upload(session_id, BytesIO(pdf_bytes()), "route.pdf", "application/pdf")
    with store_engine.begin() as connection:
        connection.execute(text(
            "UPDATE conversation_attachments SET size_bytes=30000000 WHERE id=:id",
        ), {"id": item.id})
    for assignment in ("size_bytes=30000001", "mime_type='text/plain'"):
        with pytest.raises(IntegrityError), store_engine.begin() as connection:
            connection.execute(text(
                f"UPDATE conversation_attachments SET {assignment} WHERE id=:id",
            ), {"id": item.id})


"""请求体测试函数：附件在表单解析前限制30MB加表单开销。"""

@pytest.mark.parametrize("chunked", [False, True])
def test_attachment_request_body_limit(chunked):
    app = FastAPI()
    app.add_middleware(DocumentUploadLimitMiddleware)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "upload-test"
        return await call_next(request)

    @app.post("/api/v1/sessions/{session_id}/attachments")
    async def consume(request: Request):
        await request.body()
        return Response(status_code=204)

    with TestClient(app) as client:
        path = f"/api/v1/sessions/{uuid4()}/attachments"
        assert client.post(path, content=b"a" * 30_000_000).status_code == 204
        if chunked:
            response = client.post(path, content=iter([b"a" * (30_000_000 + 64 * 1024 + 1)]))
        else:
            response = client.post(path, headers={
                "content-length": str(30_000_000 + 64 * 1024 + 1),
            })
        assert response.status_code == 413
