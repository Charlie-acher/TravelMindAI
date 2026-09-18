"""测试层：检查会话改名的输入边界、接口契约和账号隔离。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.trips import get_trip_service
from app.config import Settings
from app.main import create_app
from app.services.trip_service import SessionNotFoundError, TripService
from tests.helpers import TEST_USER_ID, authenticated_client

"""接口校验函数：匿名不可改名，空白、超长和额外字段不进入存储。"""
def test_rename_contract() -> None:
    app = create_app(Settings(database_url=None))
    identifier = uuid4()
    path = f"/api/v1/sessions/{identifier}"
    calls = []

    """保存替身函数：记录归属参数，返回与真实接口一致的会话。"""
    def rename(session_id, title, *, user_id):
        calls.append((session_id, title, user_id))
        return SimpleNamespace(id=session_id, thread_id=uuid4(), title=title,
                               status="active", created_at=datetime.now(timezone.utc),
                               updated_at=datetime.now(timezone.utc))

    app.dependency_overrides[get_trip_service] = lambda: SimpleNamespace(rename_session=rename)
    with TestClient(app) as client:
        assert client.patch(path, json={"title": "新标题"}).status_code == 401
    with authenticated_client(app) as client:
        for body in ({}, {"title": "  "}, {"title": "字" * 201},
                     {"title": "新标题", "user_id": str(uuid4())}):
            assert client.patch(path, json=body).status_code == 422
        assert not calls
        response = client.patch(path, json={"title": "  周末慢游  "})
        assert response.status_code == 200
        assert response.json()["session"]["title"] == "周末慢游"
        assert calls == [(identifier, "周末慢游", TEST_USER_ID)]


"""存储隔离函数：改名只修改自己的标题，不改消息，其他账号不可操作。"""
def test_rename_persistence_and_owner(store_engine: Engine) -> None:
    service = TripService(store_engine)
    trip = service.create_session("旧标题", user_id=TEST_USER_ID)
    with pytest.raises(SessionNotFoundError):
        service.rename_session(trip.id, "错误修改", user_id=uuid4())
    assert service.get_session(trip.id).title == "旧标题"
    changed = service.rename_session(trip.id, "  新标题  ", user_id=TEST_USER_ID)
    assert changed.title == "新标题"
    assert service.get_session(trip.id).title == "新标题"
    assert changed.updated_at == trip.updated_at  # 改名称不改变聊天排序，分页游标保持稳定。
    with pytest.raises(ValueError):
        service.rename_session(trip.id, " ", user_id=TEST_USER_ID)
