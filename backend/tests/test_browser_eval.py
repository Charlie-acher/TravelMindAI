"""验收脚本测试层：验证清理只作用于专用账号，不误删其他会话。"""

from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attachment import ConversationAttachment
from app.models.auth import User
from app.models.trip import TravelSession
from app.models.usage import UsageEvent
from app.services.attachment.storage import AttachmentService
from app.services.auth import AuthService
from app.services.trip_service import TripService
from scripts.evaluate_browser import cleanup_accounts, cleanup_remote_accounts, compose_target

"""容器目标测试函数：测试连接独立端口，个人模型设置保留且密码不用于网页URL。"""

def test_compose_target_uses_separate_database(tmp_path, monkeypatch):
    for name in ("TRAVELMIND_COMPOSE_DB_PASSWORD", "TRAVELMIND_COMPOSE_POSTGRES_PORT",
                 "TRAVELMIND_COMPOSE_PORT", "TRAVELMIND_DEEPSEEK_MODEL"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / ".env"
    config.write_text("TRAVELMIND_COMPOSE_DB_PASSWORD=testsecret123\n"
                      "TRAVELMIND_DEEPSEEK_MODEL=test-model\n", encoding="utf-8")
    settings, url = compose_target(config)
    assert url == "http://127.0.0.1:8080"
    assert "@127.0.0.1:15432/travelmind" in settings.database_url.get_secret_value()
    assert settings.deepseek_model == "test-model"
    monkeypatch.setenv("TRAVELMIND_COMPOSE_PORT", "18080")
    assert compose_target(config)[1] == "http://127.0.0.1:18080"
    monkeypatch.setenv("TRAVELMIND_COMPOSE_DB_PASSWORD", "bad@password")
    with pytest.raises(ValueError, match="Compose"):
        compose_target(config)


"""容器清理失败测试函数：远端拒绝删除时保留账号，不能转成本机直接删除。"""

def test_remote_cleanup_failure_preserves_account_and_session(store_engine):
    user = AuthService(store_engine).create_user("browser-remote-cleanup", "test123")
    saved = TripService(store_engine).create_session("容器保留", user_id=user.id)
    with httpx.Client(base_url="http://compose.test", transport=httpx.MockTransport(
        lambda request: httpx.Response(200 if request.method == "POST" else 503),
    )) as client, pytest.raises(httpx.HTTPStatusError):
        cleanup_remote_accounts(store_engine, [user], client, {user.id: httpx.Cookies()})
    with Session(store_engine) as db:
        assert db.get(User, user.id) is not None
        assert db.get(TravelSession, saved.id) is not None

"""清理边界测试函数：相同标题不影响按账号编号选择，外部账号和会话保留。"""

def test_browser_cleanup_only_removes_owned_test_data(store_engine, tmp_path):
    auth = AuthService(store_engine)
    tested = auth.create_user("browser-cleanup", "test123")
    other = auth.create_user("browser-preserved", "test123")
    service = TripService(store_engine, attachment_dir=tmp_path)
    removed = service.create_session("同名会话", user_id=tested.id)
    preserved = service.create_session("同名会话", user_id=other.id)
    attachments = AttachmentService(store_engine, tmp_path)
    removed_file = attachments.upload(removed.id, BytesIO(b"private browser test"),
                                      "same-name.txt", "text/plain")
    preserved_file = attachments.upload(preserved.id, BytesIO(b"preserve original"),
                                        "same-name.txt", "text/plain")
    usage_id = uuid4()
    with Session(store_engine) as db, db.begin():
        removed_path = tmp_path / db.get(ConversationAttachment, removed_file.id).storage_name
        preserved_path = tmp_path / db.get(ConversationAttachment, preserved_file.id).storage_name
        db.add(UsageEvent(id=usage_id, request_id="browser-test", user_id=tested.id,
                          session_id=removed.id, started_at=datetime.now(timezone.utc),
                          payload_json={"state": "succeeded"}))
    cleanup_accounts(store_engine, [tested], tmp_path)
    assert not removed_path.exists()
    assert preserved_path.read_bytes() == b"preserve original"
    with Session(store_engine) as db:
        assert db.get(User, tested.id) is None
        assert db.get(TravelSession, removed.id) is None
        assert db.get(User, other.id) is not None
        assert db.get(UsageEvent, usage_id) is not None
        assert db.scalar(select(TravelSession.id).where(
            TravelSession.user_id == other.id)) == preserved.id
