"""附件存储集成测试层：验证轮次快照、归属和删除时的磁盘清理。"""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.schemas.attachment import AttachmentAnalysis, AttachmentSnapshot
from app.services.attachment.storage import AttachmentService
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.trip_service import SessionNotFoundError, TripService
from tests.helpers import TEST_USER_ID
from tests.test_itinerary_history import sample_response

"""快照测试函数：提交结果可恢复，重复编号换附件及跨会话引用被拒绝。"""

def test_attachment_history_ownership_and_identity(store_engine, tmp_path):
    trips = TripService(store_engine)
    first = trips.create_session("附件", user_id=TEST_USER_ID)
    second = trips.create_session("另一个附件", user_id=TEST_USER_ID)
    storage = AttachmentService(store_engine, tmp_path)
    a = storage.upload(first.id, BytesIO(b"Hangzhou"), "a.txt", "text/plain")
    b = storage.upload(second.id, BytesIO(b"Suzhou"), "b.txt", "text/plain")
    history = RequirementHistoryService(store_engine)
    response = sample_response("看附件")
    response.attachments = [AttachmentSnapshot(id=a.id, file_name=a.file_name,
        analysis=AttachmentAnalysis(summary="杭州路线"))]
    mid = uuid4()
    saved = history.append(first.id, mid, 0, response)
    assert history.read(first.id).turns[0] == saved
    assert history.append(first.id, mid, 0, response) == saved
    response.attachments = [AttachmentSnapshot(id=b.id, file_name=b.file_name)]
    with pytest.raises(HistoryConflictError):
        history.append(first.id, mid, 0, response)
    with pytest.raises(HistoryConflictError):
        history.append(first.id, uuid4(), 1, response)
    assert history.read(first.id).revision == 1


"""删除测试函数：事务失败保留原件，成功删除只清理本会话原件。"""

def test_attachment_delete_cleans_owned_files_after_commit(store_engine, tmp_path):
    trips = TripService(store_engine, attachment_dir=tmp_path)
    first = trips.create_session("删除附件", user_id=TEST_USER_ID)
    second = trips.create_session("保留附件", user_id=TEST_USER_ID)
    storage = AttachmentService(store_engine, tmp_path)
    a = storage.upload(first.id, BytesIO(b"one"), "one.txt", "text/plain")
    b = storage.upload(second.id, BytesIO(b"two"), "two.txt", "text/plain")

    """事务故障函数：模拟最后删除会话失败。"""

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM sessions"):
            raise RuntimeError("test rollback")

    event.listen(store_engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            trips.delete_session(first.id, user_id=TEST_USER_ID)
    finally:
        event.remove(store_engine, "after_cursor_execute", fail)
    assert storage.read_content(first.id, a.id)[0] == b"one"
    assert len(list(tmp_path.glob("*.txt"))) == 2
    journal = tmp_path / f".delete-{first.id}.json"
    assert journal.exists()
    from scripts.cleanup_attachment_files import cleanup_attachment_files

    # 恢复清理必须再次检查数据库；回滚后的会话仍在，不能按旧记录删原件。
    assert cleanup_attachment_files(store_engine, tmp_path) == (0, 1)
    assert storage.read_content(first.id, a.id)[0] == b"one"
    trips.delete_session(first.id, user_id=TEST_USER_ID)
    assert len(list(tmp_path.glob("*.txt"))) == 1
    assert not journal.exists()
    assert storage.read_content(second.id, b.id)[0] == b"two"


"""文件故障测试函数：数据库提交后文件占用时保留清理依据，归属正确才能重试。"""

@pytest.mark.parametrize("use_script", [False, True])
def test_attachment_delete_retries_file_failure(store_engine, tmp_path, monkeypatch, use_script):
    trips = TripService(store_engine, attachment_dir=tmp_path)
    trip = trips.create_session("文件占用", user_id=TEST_USER_ID)
    storage = AttachmentService(store_engine, tmp_path)
    storage.upload(trip.id, BytesIO(b"private"), "route.txt", "text/plain")
    original = next(tmp_path.glob("*.txt"))
    journal = tmp_path / f".delete-{trip.id}.json"
    actual_unlink = Path.unlink

    """文件占用函数：只阻止这一份原件删除。"""

    def busy(path, *args, **kwargs):
        if path == original:
            raise PermissionError("file busy")
        return actual_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", busy)
        with pytest.raises(PermissionError, match="file busy"):
            trips.delete_session(trip.id, user_id=TEST_USER_ID)
    assert trips.get_session(trip.id) is None
    assert original.exists() and journal.exists()
    with pytest.raises(SessionNotFoundError):
        trips.delete_session(trip.id, user_id=uuid4())
    assert original.exists() and journal.exists()
    if use_script:
        from scripts.cleanup_attachment_files import cleanup_attachment_files

        assert cleanup_attachment_files(store_engine, tmp_path) == (1, 0)
    else:
        from fastapi import FastAPI

        from app.api.trips import router
        from tests.helpers import authenticated_client

        app = FastAPI()
        app.state.trip_service = trips
        app.include_router(router, prefix="/api/v1")
        with authenticated_client(app) as client:
            assert client.delete(f"/api/v1/sessions/{trip.id}").status_code == 204
    assert not original.exists() and not journal.exists()


"""路径防护测试函数：清理记录含目录或非UUID名时，不删除任何原件和外部文件。"""

@pytest.mark.parametrize("unsafe_name", ["../protected.txt", "shared.txt"])
def test_attachment_cleanup_rejects_unsafe_record(store_engine, tmp_path, unsafe_name):
    import json

    from scripts.cleanup_attachment_files import cleanup_attachment_files

    private = tmp_path / "conversations"
    private.mkdir()
    protected = tmp_path / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    original = private / f"{uuid4().hex}.txt"
    original.write_text("private", encoding="utf-8")
    session_id = uuid4()
    journal = private / f".delete-{session_id}.json"
    journal.write_text(json.dumps({
        "session_id": str(session_id), "user_id": str(TEST_USER_ID),
        "storage_names": [original.name, unsafe_name],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="原件名"):
        cleanup_attachment_files(store_engine, private)
    assert protected.exists() and original.exists() and journal.exists()


"""并发清理测试函数：旧记录读取后暂停，再次删除必须等同一数据库锁才能改写记录。"""

def test_attachment_cleanup_serializes_with_repeated_delete(store_engine, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, get_ident
    from time import monotonic

    from sqlalchemy import text

    deleter = TripService(store_engine, attachment_dir=tmp_path)
    cleaner = TripService(store_engine, attachment_dir=tmp_path)
    trip = deleter.create_session("清理并发", user_id=TEST_USER_ID)
    storage = AttachmentService(store_engine, tmp_path)
    storage.upload(trip.id, BytesIO(b"first"), "first.txt", "text/plain")

    """回滚故障函数：留下仅包含第一份附件的清理记录。"""

    def rollback(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM sessions"):
            raise RuntimeError("rollback")

    event.listen(store_engine, "after_cursor_execute", rollback)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            deleter.delete_session(trip.id, user_id=TEST_USER_ID)
    finally:
        event.remove(store_engine, "after_cursor_execute", rollback)
    storage.upload(trip.id, BytesIO(b"second"), "second.txt", "text/plain")
    paused, resume, delete_entered, rewritten = Event(), Event(), Event(), Event()
    deletion_thread: list[int] = []
    deletion_pid: list[int] = []
    original_paths = cleaner._original_attachment_paths
    original_write = deleter._write_cleanup_record

    """暂停清理函数：在读到旧记录之后、查询会话之前停住维护线程。"""

    def pause(names):
        paused.set()
        assert resume.wait(10)
        return original_paths(names)

    """改写观测函数：判断新的删除是否越过了旧记录清理。"""

    def observe_write(record):
        rewritten.set()
        original_write(record)

    """数据库入口观测函数：记录删除使用的真实连接，核验数据库锁等待。"""

    def observe_database(conn, cursor, statement, parameters, context, executemany):
        if deletion_thread and get_ident() == deletion_thread[0] and not deletion_pid:
            deletion_pid.append(conn.connection.driver_connection.info.backend_pid)
            delete_entered.set()

    """再次删除函数：在独立连接中尝试改写同时包含两份附件的记录。"""

    def remove():
        deletion_thread.append(get_ident())
        deleter.delete_session(trip.id, user_id=TEST_USER_ID)

    monkeypatch.setattr(cleaner, "_original_attachment_paths", pause)
    monkeypatch.setattr(deleter, "_write_cleanup_record", observe_write)
    event.listen(store_engine, "before_cursor_execute", observe_database)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            cleanup_future = workers.submit(cleaner.cleanup_attachment_files, trip.id)
            deletion_future = None
            try:
                assert paused.wait(10)
                deletion_future = workers.submit(remove)
                assert delete_entered.wait(10)
                blocked = False
                deadline = monotonic() + 5
                while not rewritten.is_set() and monotonic() < deadline:
                    with store_engine.connect() as connection:
                        blocked = connection.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid"
                            " AND locktype='advisory' AND NOT granted)"
                        ), {"pid": deletion_pid[0]})
                    if blocked:
                        break
                assert blocked, "旧清理记录仍在读取时，再次删除必须等待PostgreSQL锁"
                assert not rewritten.is_set()
            finally:
                resume.set()
                cleanup_future.result(timeout=10)
                if deletion_future is not None:
                    deletion_future.result(timeout=10)
    finally:
        event.remove(store_engine, "before_cursor_execute", observe_database)
    assert deleter.get_session(trip.id) is None
    assert list(tmp_path.iterdir()) == []
