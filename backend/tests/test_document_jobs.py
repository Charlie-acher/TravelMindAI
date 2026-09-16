"""测试层：用真实数据库验证后台处理的去重、中断识别和显式重试。"""

from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine

from app.schemas.document.job import DocumentJobView
from app.schemas.document.search import IndexProgress
from app.services.document.jobs import DocumentJobService
from app.services.document.service import DocumentService

"""任务恢复测试函数：重复提交沿用任务，中断后只标失败，用户重试才继续缺失批次。"""

def test_job_recovery_and_resume(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(
        BytesIO("杭州西湖".encode()), "杭州.txt", "text/plain",
    ).document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    first, scheduled = jobs.start(identifier, "index")
    assert scheduled and first.status == "queued"
    same, scheduled = jobs.start(identifier, "index")
    assert not scheduled and same.run_id == first.run_id
    assert jobs.recover_interrupted() == 1
    assert jobs.read(identifier).status == "failed"
    retried, scheduled = jobs.start(identifier, "index")
    assert scheduled and retried.run_id != first.run_id
    calls: list[int] = []

    """批次模拟函数：代表两批已确认写入的向量。"""
    def batch() -> IndexProgress:
        calls.append(1)
        return IndexProgress(total=10, indexed=min(len(calls) * 8, 10), complete=len(calls) == 2)

    jobs.execute(identifier, first.run_id, batch)  # 旧后台回调不能接管重试后的新任务。
    assert calls == []
    jobs.execute(identifier, retried.run_id, batch)
    result = jobs.read(identifier)
    assert result.status == "completed" and result.indexed == result.total == 10
    jobs.execute(identifier, retried.run_id, batch)
    assert len(calls) == 2


"""归属与失败测试函数：越权查不到任务，错误保存安全说明，暂停停止后续批次。"""

def test_job_owner_failure_and_pause(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(BytesIO(b"guide"), "guide.txt", "text/plain").document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    other = DocumentJobService(store_engine, "owner-b")
    with pytest.raises(HTTPException) as missing:
        other.start(identifier, "index")
    assert missing.value.status_code == 404
    first, _ = jobs.start(identifier, "index")

    """失败批次函数：内部异常内容不得保存到可见状态。"""
    def fail() -> IndexProgress:
        raise RuntimeError("secret-token-must-not-leak")

    jobs.execute(identifier, first.run_id, fail)
    assert jobs.read(identifier).status == "failed"
    assert "secret-token" not in jobs.read(identifier).error_message
    retry, _ = jobs.start(identifier, "index")

    """暂停批次函数：当前批次允许完成，后续批次不再执行。"""
    def pause() -> IndexProgress:
        jobs.pause(identifier)
        return IndexProgress(total=10, indexed=8, complete=False)

    jobs.execute(identifier, retry.run_id, pause)
    assert jobs.read(identifier).status == "paused"
    with pytest.raises(HTTPException):
        other.read(identifier)


"""存活锁测试函数：其他应用启动时不能把正在执行的任务当作中断。"""

def test_recovery_skips_live_worker(store_engine: Engine, tmp_path: Path) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(BytesIO(b"live"), "live.txt", "text/plain").document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    first, _ = jobs.start(identifier, "parse")

    """存活处理函数：执行期间再次恢复应跳过持锁的当前任务。"""
    def parse() -> None:
        assert jobs.recover_interrupted() == 0

    jobs.execute(identifier, first.run_id, parse)
    assert jobs.read(identifier).status == "completed"


"""重复提交竞争测试函数：后台执行等待短暂提交锁，不能把唯一回调直接丢弃。"""

def test_worker_waits_for_duplicate_submission(
    store_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(BytesIO(b"queued"), "queued.txt", "text/plain").document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    first, _ = jobs.start(identifier, "parse")
    entered, release, executing, finished = Event(), Event(), Event(), Event()
    original_start = jobs._start
    calls: list[int] = []

    """重复提交函数：保留真实数据库锁，用事件固定回调恰好撞上提交锁的顺序。"""
    def delayed_start(document_id: UUID, kind: str) -> tuple[DocumentJobView, bool]:
        entered.set()
        assert release.wait(5)
        return original_start(document_id, kind)

    """后台回调函数：记录进入与退出，批次只记次数，不调用模型。"""
    def execute() -> None:
        executing.set()
        try:
            jobs.execute(identifier, first.run_id, lambda: calls.append(1))
        finally:
            finished.set()

    monkeypatch.setattr(jobs, "_start", delayed_start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate = pool.submit(jobs.start, identifier, "parse")
        try:
            assert entered.wait(5)
            worker = pool.submit(execute)
            assert executing.wait(5)
            # 旧实现此时直接退出；新实现等重复提交释放锁后才核对并执行任务。
            assert not finished.wait(0.2)
        finally:
            release.set()
        repeated, scheduled = duplicate.result(timeout=5)
        worker.result(timeout=5)
    assert not scheduled and repeated.run_id == first.run_id
    assert calls == [1]
    assert jobs.read(identifier).status == "completed"


"""暂停后重试测试函数：当前批次持锁时拒绝新运行，结束后才允许显式续建。"""

def test_resume_waits_until_paused_batch_releases_lock(
    store_engine: Engine, tmp_path: Path,
) -> None:
    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(BytesIO(b"pause"), "pause.txt", "text/plain").document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    first, _ = jobs.start(identifier, "index")

    """暂停批次函数：状态已暂停但当前批次未返回时，重试仍应得到冲突。"""
    def pause_batch() -> IndexProgress:
        assert jobs.pause(identifier).status == "paused"
        with pytest.raises(HTTPException) as conflict:
            jobs.start(identifier, "index")
        assert conflict.value.status_code == 409
        return IndexProgress(total=10, indexed=8, complete=False)

    jobs.execute(identifier, first.run_id, pause_batch)
    paused = jobs.read(identifier)
    assert paused.status == "paused" and paused.indexed == 8
    resumed, scheduled = jobs.start(identifier, "index")
    assert scheduled and resumed.run_id != first.run_id
    assert resumed.status == "queued"


"""进程中断测试函数：子进程在持锁执行时强制退出，重新创建服务后恢复并允许重试。"""

def test_process_crash_releases_lock(store_engine: Engine, tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    documents = DocumentService(store_engine, tmp_path, "owner-a")
    identifier = documents.upload(BytesIO(b"crash"), "crash.txt", "text/plain").document.id
    jobs = DocumentJobService(store_engine, "owner-a")
    first, _ = jobs.start(identifier, "parse")
    environment = os.environ.copy()
    environment["JOB_TEST_DATABASE"] = store_engine.url.render_as_string(hide_password=False)
    program = """
import os, sys
from uuid import UUID
from sqlalchemy import create_engine
from app.services.document.jobs import DocumentJobService
engine = create_engine(os.environ['JOB_TEST_DATABASE'])
DocumentJobService(engine, 'owner-a').execute(
    UUID(sys.argv[1]), UUID(sys.argv[2]), lambda: os._exit(23),
)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(identifier), str(first.run_id)],
        env=environment, capture_output=True, timeout=15,
    )
    assert result.returncode == 23
    assert jobs.read(identifier).status == "running"
    restored = DocumentJobService(store_engine, "owner-a")
    assert restored.recover_interrupted() == 1
    retry, _ = restored.start(identifier, "parse")
    restored.execute(identifier, retry.run_id, lambda: None)
    assert restored.read(identifier).status == "completed"


"""后台接口测试函数：解析失败状态、显式重试、缺配置错误及删除后的任务清理可见。"""

def test_job_api_and_parse_failure(
    store_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    monkeypatch.setattr("app.main.create_database_engine", lambda _: store_engine)
    settings = Settings(database_url="postgresql+psycopg://unused", document_upload_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        upload = client.post("/api/v1/documents", files={
            "file": ("broken.pdf", b"broken-pdf", "application/pdf"),
        }).json()["document"]
        url = f"/api/v1/documents/{upload['id']}/job"
        assert client.get(url).json() is None
        response = client.post(url, json={"kind": "parse"})
        assert response.status_code == 202 and response.json()["status"] == "queued"
        assert client.get(url).json()["status"] == "failed"
        assert client.post(url, json={"kind": "index"}).status_code == 409
        assert client.post(url, json={"kind": "unknown"}).status_code == 422
        assert client.delete(f"/api/v1/documents/{upload['id']}").status_code == 204
        assert client.get(url).status_code == 404
    with TestClient(create_app(Settings(database_url=None))) as client:
        assert client.get(url).status_code == 503
