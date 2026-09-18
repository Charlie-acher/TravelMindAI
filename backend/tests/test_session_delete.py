"""测试层：在独立 PostgreSQL 环境验证整段会话硬删除、权限和并发写入。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.document import DocumentRecord
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession
from app.schemas.itinerary import UndoDraftRequest
from app.services.auth import AuthService
from app.services.requirement.history import RequirementHistoryService
from app.services.trip_service import SessionNotFoundError, TripService
from tests.helpers import TEST_USER_ID
from tests.test_itinerary_history import sample_response, save_two

HEADERS = {"X-Requested-With": "TravelMindAI"}


"""匿名删除测试函数：删除入口与其他私人接口一样要求先登录。"""

def test_delete_requires_login() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        assert client.delete(f"/api/v1/sessions/{uuid4()}").status_code == 401


"""删除隔离测试函数：真实 Cookie 校验来源及归属，删除全部子记录并保留其他会话和资料。"""

def test_delete_removes_only_owned_conversation(store_engine: Engine) -> None:
    auth = AuthService(store_engine)
    owner = auth.create_user("delete-owner", "test-pass123", "user")
    other = auth.create_user("delete-other", "test-pass123", "user")
    store = TripService(store_engine)
    target = store.create_session("要删除的对话", user_id=owner.id)
    retained = [store.create_session("保留对话", user_id=user_id)
                for user_id in (owner.id, other.id)]
    for trip in [target, *retained]:
        for version in (1, 2):
            store.save_draft(trip.id, request_json={"days": version}, itinerary_json={})
        with Session(store_engine) as unit:
            unit.add(RequirementTurn(session_id=trip.id, message_id=uuid4(),
                                     revision=1, response_json={}))
            unit.commit()
    with Session(store_engine) as unit:
        document = DocumentRecord(
            owner_id="knowledge-base", file_name="保留资料.txt", storage_name="keep.txt",
            mime_type="text/plain", content_hash="a" * 64, size_bytes=1,
            status="parsed", sections_json=[{"text": "共享资料"}], warnings_json=[],
        )
        unit.add(document)
        unit.commit()
        document_id = document.id
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False),
    ))
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/auth/login", headers=HEADERS, json={
            "username": owner.username, "password": "test-pass123",
        }).status_code == 200
        path = f"/api/v1/sessions/{target.id}"
        assert client.delete(path).status_code == 403
        assert client.delete(path, headers=HEADERS | {
            "Origin": "https://other.example",
        }).status_code == 403
        assert client.delete(f"/api/v1/sessions/{retained[1].id}",
                             headers=HEADERS).status_code == 404
        missing = client.delete(f"/api/v1/sessions/{uuid4()}", headers=HEADERS)
        assert missing.status_code == 404
        deleted = client.delete(path, headers=HEADERS)
        assert deleted.status_code == 204 and deleted.content == b""
        assert client.delete(path, headers=HEADERS).status_code == 404
        assert client.get(path).status_code == 404
        assert client.get(path + "/requirement-messages").status_code == 404
        assert [row["id"] for row in client.get("/api/v1/sessions").json()["items"]] == [
            str(retained[0].id),
        ]
    with Session(store_engine) as unit:
        assert unit.get(TravelSession, target.id) is None
        for model, count in ((RequirementTurn, 2), (TravelRequest, 4), (Itinerary, 4)):
            assert unit.scalar(select(func.count()).select_from(model).where(
                model.session_id == target.id,
            )) == 0
            assert unit.scalar(select(func.count()).select_from(model)) == count
        assert unit.get(DocumentRecord, document_id) is not None


"""并发删除测试函数：与保存、追加和撤销竞争同一行锁，不能留下子记录或复活会话。"""

@pytest.mark.parametrize("operation", ["draft", "append", "undo"])
def test_delete_serializes_with_writes(store_engine: Engine, operation: str) -> None:
    store = TripService(store_engine)
    trip, history, _, _, payload = save_two(store_engine)
    ready = Barrier(2)

    """并发写入函数：允许先完成保存，或发现会话已被删除。"""

    def save() -> None:
        ready.wait(timeout=10)
        try:
            if operation == "draft":
                store.save_draft(trip.id, request_json={}, itinerary_json={})
            elif operation == "append":
                history.append(trip.id, uuid4(), 2, sample_response("再聊一句"))
            else:
                history.undo(trip.id, payload, "concurrent-delete")
        except SessionNotFoundError:
            pass

    """并发删除函数：在同一事务清理全部版本。"""

    def remove() -> None:
        ready.wait(timeout=10)
        store.delete_session(trip.id, user_id=TEST_USER_ID)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save), pool.submit(remove)]
        for future in futures:
            future.result(timeout=15)
    with Session(store_engine) as unit:
        for model in (TravelSession, RequirementTurn, TravelRequest, Itinerary):
            assert unit.scalar(select(func.count()).select_from(model)) == 0
    with pytest.raises(SessionNotFoundError):
        RequirementHistoryService(store_engine).undo(trip.id, UndoDraftRequest(
            operation_id=uuid4(), target_message_id=uuid4(), expected_revision=1,
            expected_itinerary_version=1,
        ), "deleted-session")
    with pytest.raises(SessionNotFoundError):
        history.append(trip.id, uuid4(), 2, sample_response())


"""删除回滚测试函数：中途数据库操作失败时，先删除的消息与草稿也必须完整恢复。"""

def test_delete_rolls_back_all_records_on_failure(store_engine: Engine) -> None:
    trip, history, _, second, _ = save_two(store_engine)

    """故障注入函数：在已删除消息和行程后中止事务，模拟写入连接故障。"""

    def fail_after_delete(connection, cursor, statement, parameters, context, many):
        if statement.startswith("DELETE FROM itineraries"):
            raise RuntimeError("模拟删除中途故障")

    event.listen(store_engine, "after_cursor_execute", fail_after_delete)
    try:
        with pytest.raises(RuntimeError, match="中途故障"):
            TripService(store_engine).delete_session(trip.id, user_id=TEST_USER_ID)
    finally:
        event.remove(store_engine, "after_cursor_execute", fail_after_delete)
    assert history.read(trip.id).turns[-1] == second
    with Session(store_engine) as unit:
        for model in (RequirementTurn, TravelRequest, Itinerary):
            assert unit.scalar(select(func.count()).select_from(model)) == 2
