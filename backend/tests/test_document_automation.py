"""测试层：验证上传后的自动处理、标签预填和已有资料的人工内容保护。"""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine

from app.config import Settings
from app.main import create_app
from app.schemas.document.base import DocumentMetadata
from app.services.document.service import DocumentService
from tests.helpers import authenticated_client as TestClient

"""标签预填测试函数：新资料识别文件名和明确字段，重复上传保留人工标签。"""

def test_upload_prefills_and_keeps_manual_metadata(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "owner-a")
    content = "# 重庆餐馆\n\n城市：重庆市\n\n火锅资料".encode()
    saved = service.upload(BytesIO(content), "重庆-餐馆-ChinaTravel.md", "text/markdown").document
    assert (saved.city, saved.category) == ("重庆", "餐馆")
    service.update_metadata(saved.id, DocumentMetadata(city="人工城市", category="景点"))
    same = service.upload(BytesIO(content), "杭州-LvBanGPT.md", "text/markdown")
    assert same.duplicate and same.document.city == "人工城"
    assert same.document.category == "景点"


"""识别边界测试函数：多城市不随意选一个，明确城市字段支持非常见城市。"""

def test_metadata_inference_boundaries(store_engine: Engine, tmp_path: Path) -> None:
    service = DocumentService(store_engine, tmp_path, "owner-a")
    for name, body, city, category in [
        ("游记.txt", "城市：景德镇市\n来源：作者笔记", "景德镇", None),
        ("杭州-上海攻略.md", "杭州和上海的比较", None, None),
        ("攻略.txt", "这座城市不错，来源不详。", None, None),
        ("深圳-景点-LvBanGPT.md", "深圳公园", "深圳", "景点"),
        ("交通.txt", "出发城市：北京\n目的地城市：上海", None, None),
    ]:
        result = service.upload(BytesIO(body.encode()), name, "text/plain").document
        assert (result.city, result.category) == (city, category)


"""自动处理接口测试函数：开启自动处理后切片并记录失败，关闭时只保存，单份失败不挡下一份。"""

def test_auto_upload_schedules_jobs_and_prefill_is_owner_scoped(
    store_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.create_database_engine", lambda _: store_engine)
    settings = Settings(database_url="postgresql+psycopg://unused", document_upload_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/documents?auto_process=true", files={
            "file": ("杭州-自动验收.txt", "杭州自动验收\n\n第二段".encode(), "text/plain"),
        })
        assert response.status_code == 201
        saved = response.json()["document"]
        path = f"/api/v1/documents/{saved['id']}"
        job = client.get(path + "/job").json()
        assert job and job["kind"] == "index" and job["status"] == "failed"
        assert client.get(path + "/chunks").json()["total"] == 2
        assert response.json()["processing_error"] is None
        client.patch(path + "/metadata", json={"city": None, "category": "景点"})
        filled = client.post(path + "/metadata/prefill").json()
        assert filled["city"] == "杭州" and filled["category"] == "景点"
        other = DocumentService(store_engine, tmp_path, "other").upload(
            BytesIO(b"other"), "other.txt", "text/plain",
        ).document
        assert client.post(f"/api/v1/documents/{other.id}/metadata/prefill").status_code == 404
        plain = client.post("/api/v1/documents", files={
            "file": ("plain.txt", b"plain", "text/plain"),
        }).json()["document"]
        assert client.get(f"/api/v1/documents/{plain['id']}/job").json() is None
        broken = client.post("/api/v1/documents?auto_process=true", files={
            "file": ("broken.pdf", b"broken", "application/pdf"),
        }).json()["document"]
        assert broken["status"] == "failed"
        assert client.get(f"/api/v1/documents/{broken['id']}/job").json() is None
        monkeypatch.setattr("app.api.document.routes.get_job_service", reject_submission)
        unscheduled = client.post("/api/v1/documents?auto_process=true", files={
            "file": ("queue-error.txt", b"queue-error", "text/plain"),
        })
        assert unscheduled.status_code == 201 and unscheduled.json()["processing_error"]
        assert unscheduled.json()["document"]["status"] == "parsed"


"""提交失败替代函数：模拟任务服务临时不可用，上传结果仍必须如实返回。"""

def reject_submission(_request):
    raise HTTPException(503, "temporary")


"""并发上限测试函数：六份后台任务只能同时执行两份，排队时事件循环仍可响应。"""

def test_batch_jobs_limit_workers_without_blocking_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from threading import Event, Lock

    from app.api.document.jobs import run_queued_job

    monkeypatch.setattr("app.api.document.jobs.require_knowledge_ready", lambda _: None)
    release, guard = Event(), Lock()
    running = maximum = finished = 0

    """并发场景函数：前两份开始后保持等待，检查其余任务没有占用执行位置。"""
    async def scenario():
        loop, entered = asyncio.get_running_loop(), asyncio.Event()

        """耗时业务替代函数：只有释放信号到达才完成，用于核对真实排队边界。"""
        def process(_engine, _settings, _job):
            nonlocal running, maximum, finished
            with guard:
                running += 1
                maximum = max(maximum, running)
                if running == 2:
                    loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(5)
            finally:
                with guard:
                    running -= 1
                    finished += 1

        monkeypatch.setattr("app.api.document.jobs.process_document_job", process)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            document_job_slots=asyncio.Semaphore(2), database_engine=None, settings=None,
        )))
        tasks = [asyncio.create_task(run_queued_job(request, None)) for _ in range(6)]
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert running == maximum == 2 and finished == 0
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert maximum == 2 and finished == 6


"""入口断线测试函数：后台取得执行锁前失败，连接恢复后的状态查询应可标失败并重试。"""

def test_background_entry_failure_can_recover(
    store_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from sqlalchemy.exc import SQLAlchemyError

    from app.api.document.jobs import run_queued_job
    from app.services.document.jobs import DocumentJobService

    monkeypatch.setattr("app.main.create_database_engine", lambda _: store_engine)
    settings = Settings(database_url="postgresql+psycopg://unused", document_upload_dir=tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        documents = DocumentService(store_engine, tmp_path, "knowledge-base")
        identifier = documents.upload(
            BytesIO(b"entry-failure"), "entry.txt", "text/plain",
        ).document.id
        jobs = DocumentJobService(store_engine, "knowledge-base")
        job, _ = jobs.start(identifier, "index")

        """断线替代函数：代表后台执行入口无法借到数据库连接。"""
        def offline(*args):
            raise SQLAlchemyError("offline")

        monkeypatch.setattr("app.api.document.jobs.process_document_job", offline)
        # 模拟从入口失败到首次持久化失败标记期间，数据库都不可用。
        original = DocumentJobService.mark_interrupted
        monkeypatch.setattr(DocumentJobService, "mark_interrupted", offline)
        asyncio.run(run_queued_job(SimpleNamespace(app=app), job))
        monkeypatch.setattr(DocumentJobService, "mark_interrupted", original)
        state = client.get(f"/api/v1/documents/{identifier}/job")
        assert state.status_code == 200 and state.json()["status"] == "failed"
        retried, scheduled = jobs.start(identifier, "index")
        assert scheduled and retried.run_id != job.run_id


"""懒恢复并发测试函数：数据库恢复后两个首次请求只执行一次中断识别。"""

def test_lazy_recovery_only_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from app.api.document.jobs import get_job_service

    monkeypatch.setattr("app.api.document.jobs.require_knowledge_ready", lambda _: None)
    entered, release = Event(), Event()
    calls = []

    """恢复替代函数：故意停在第一次恢复，让第二个请求同时进入依赖。"""
    def recover(_self):
        calls.append(1)
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr("app.api.document.jobs.DocumentJobService.recover_interrupted", recover)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        database_engine=object(), document_jobs_recovered=False,
        document_job_recovery_lock=Lock(), document_job_failures=set(),
    )))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(get_job_service, request)
        assert entered.wait(3)
        second = pool.submit(get_job_service, request)
        release.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert calls == [1]
