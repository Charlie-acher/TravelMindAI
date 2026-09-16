"""测试层：验证登录边界、会话归属和浏览器跨站写入保护。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.config import Settings
from app.main import create_app
from app.models.auth import AuthSession, User
from app.models.trip import TravelSession
from app.services.auth import AuthService

HEADERS = {"X-Requested-With": "TravelMindAI"}
PASSWORD = "test-pass123"


"""账号环境函数：真实临时PostgreSQL里创建两个用户和一个管理员。"""

@pytest.fixture
def accounts(store_engine: Engine):
    service = AuthService(store_engine)
    users = [service.create_user(name, PASSWORD, role) for name, role in (
        ("alice", "user"), ("bob", "user"), ("manager", "admin"),
    )]
    settings = Settings(environment="test", database_url=SecretStr(
        store_engine.url.render_as_string(hide_password=False),
    ))
    return service, users, settings


"""登录辅助函数：通过真实HTTP口令校验取得Cookie，不覆盖认证依赖。"""

def sign_in(client: TestClient, username: str) -> dict:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": PASSWORD,
    }, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


"""未登录测试函数：旧接口也必须拒绝匿名访问，不能漏掉管理和历史入口。"""

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/auth/me", None),
    ("GET", "/sessions", None),
    ("POST", "/sessions", {"title": "新建对话"}),
    ("GET", f"/sessions/{uuid4()}/requirement-messages", None),
    ("GET", "/documents", None),
    ("GET", "/admin/documents", None),
    ("POST", "/requirements/messages", {"message": "杭州"}),
])
def test_anonymous_endpoints_are_closed(method: str, path: str, body: dict | None) -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        response = client.request(method, "/api/v1" + path, json=body)
        assert response.status_code == 401


"""来源检查测试函数：登录也拒绝跨站来源和没有自定义请求头的写入。"""

def test_login_rejects_cross_site_requests() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        payload = {"username": "alice", "password": PASSWORD}
        assert client.post("/api/v1/auth/login", json=payload).status_code == 403
        assert client.post("/api/v1/auth/login", json=payload, headers={
            "X-Requested-With": "TravelMindAI", "Origin": "https://evil.example",
        }).status_code == 403
        assert client.post("/api/v1/auth/login", json=payload, headers={
            "X-Requested-With": "TravelMindAI", "Origin": "http://[",
        }).status_code == 403


"""登录生命周期测试函数：持久令牌、退出、过期和停用均执行真实数据库检查。"""

def test_login_cookie_logout_expiry_and_disabled_user(accounts, store_engine: Engine) -> None:
    service, users, settings = accounts
    with TestClient(create_app(settings)) as client:
        identity = sign_in(client, "alice")
        assert set(identity) == {"id", "username", "role"}
        assert identity["role"] == "user"
        cookie = client.cookies.get(COOKIE_NAME)
        assert cookie and len(cookie) == 43
        assert client.get("/api/v1/auth/me").json() == identity
        with Session(store_engine) as unit:
            row = unit.scalar(select(AuthSession))
            assert row.token_hash != cookie and len(row.token_hash) == 64
            assert unit.get(User, users[0].id).password_hash.startswith("$argon2id$")
        with TestClient(create_app(settings)) as restarted:
            restarted.cookies.set(COOKIE_NAME, cookie)
            assert restarted.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout", headers=HEADERS).status_code == 204
        assert service.authenticate(cookie) is None
        assert client.get("/api/v1/auth/me").status_code == 401
        sign_in(client, "alice")
        with store_engine.begin() as unit:
            unit.execute(update(AuthSession).where(AuthSession.user_id == users[0].id).values(
                expires_at=func.now() - timedelta(days=1),
            ))
        assert client.get("/api/v1/auth/me").status_code == 401
        sign_in(client, "alice")
        with store_engine.begin() as unit:
            unit.execute(update(User).where(User.id == users[0].id).values(is_active=False))
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post("/api/v1/auth/login", json={
            "username": "alice", "password": PASSWORD,
        }, headers=HEADERS).status_code == 401


"""双角色隔离测试函数：用户及管理员都不能读写别人的会话或草稿。"""

def test_two_users_two_sessions_and_admin_isolation(accounts) -> None:
    _, _, settings = accounts
    with TestClient(create_app(settings), headers=HEADERS) as client:
        owners = {}
        for username in ("alice", "bob"):
            sign_in(client, username)
            owners[username] = [client.post("/api/v1/sessions", json={}).json()["session"]["id"]
                                for _ in range(2)]
            page = client.get("/api/v1/sessions?limit=1").json()
            assert len(page["items"]) == 1 and page["next_cursor"]
            second = client.get("/api/v1/sessions", params={
                "limit": 1, "cursor": page["next_cursor"],
            }).json()
            assert {page["items"][0]["id"], second["items"][0]["id"]} == set(owners[username])
            assert second["next_cursor"] is None
        for username in ("bob", "manager"):
            sign_in(client, username)
            for identifier in owners["alice"]:
                base = f"/api/v1/sessions/{identifier}"
                for suffix in ("", "/drafts", "/requirement-messages"):
                    assert client.get(base + suffix).status_code == 404
                assert client.post(base + "/requirement-messages", json={
                    "message": "杭州", "message_id": str(uuid4()), "expected_revision": 0,
                }).status_code == 404
                assert client.post(base + "/drafts", json={
                    "title": "草稿", "requirements": {"days": 3, "travelers": 2,
                                                      "total_budget": "5000"},
                }).status_code == 404
        sign_in(client, "alice")
        for prefix in ("/api/v1/documents", "/api/v1/admin/documents"):
            for suffix in ("", "/cities", "/page", f"/{uuid4()}/chunks", f"/{uuid4()}/job"):
                assert client.get(prefix + suffix).status_code == 403
            assert client.post(prefix, files={"file": ("a.txt", b"hello")}).status_code == 403
        sign_in(client, "manager")
        assert client.get("/api/v1/admin/documents").status_code == 200
        assert client.get("/api/v1/sessions").json()["items"] == []


"""历史认领测试函数：未认领记录不可见，显式认领只影响指定会话。"""

def test_legacy_sessions_require_explicit_claim(accounts, store_engine: Engine) -> None:
    service, users, settings = accounts
    # 仅在过渡迁移0009模拟旧数据，最终0010已禁止空归属。
    from sqlalchemy import text
    with store_engine.begin() as unit:
        unit.execute(text("ALTER TABLE sessions ALTER COLUMN user_id DROP NOT NULL"))
    with Session(store_engine) as unit:
        old = TravelSession(title="旧会话")
        unit.add(old)
        unit.commit()
        old_id = old.id
    with TestClient(create_app(settings), headers=HEADERS) as client:
        sign_in(client, "alice")
        assert client.get(f"/api/v1/sessions/{old_id}").status_code == 404
        service.claim_sessions(users[0].id, [old_id])
        assert client.get(f"/api/v1/sessions/{old_id}").status_code == 200
        with pytest.raises(ValueError, match="其他账号"):
            service.claim_sessions(users[1].id, [old_id])


"""注册测试函数：自助注册只能成为普通用户，并立即用真实Cookie建立归属。"""

def test_registration_cannot_self_promote(accounts, store_engine: Engine) -> None:
    _, _, settings = accounts
    with TestClient(create_app(settings), headers=HEADERS) as client:
        payload = {"username": "New_User", "password": PASSWORD}
        elevated = client.post("/api/v1/auth/register", json=payload | {"role": "admin"})
        assert elevated.status_code == 422
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 201
        assert response.json()["role"] == "user"
        assert response.json()["username"] == "new_user"
        created = client.post("/api/v1/sessions", json={})
        assert created.status_code == 201
        with Session(store_engine) as unit:
            from uuid import UUID
            row = unit.get(TravelSession, UUID(created.json()["session"]["id"]))
            assert str(row.user_id) == response.json()["id"]
        assert client.get("/api/v1/admin/documents").status_code == 403
        assert client.post("/api/v1/auth/register", json=payload).status_code == 409


"""登录频率测试函数：无效账号也计次，不向调用方透露账号是否存在。"""

def test_failed_login_is_rate_limited(accounts) -> None:
    _, _, settings = accounts
    with TestClient(create_app(settings), headers=HEADERS) as client:
        for _ in range(10):
            assert client.post("/api/v1/auth/login", json={
                "username": "missing", "password": PASSWORD,
            }).status_code == 401
        assert client.post("/api/v1/auth/login", json={
            "username": "missing", "password": PASSWORD,
        }).status_code == 429


"""密码边界测试函数：注册和业务服务都接受6及12位，拒绝5及13位。"""

@pytest.mark.parametrize("length,allowed", [(5, False), (6, True), (12, True), (13, False)])
def test_password_length_boundaries(accounts, length: int, allowed: bool) -> None:
    service, _, settings = accounts
    password = "a" * length
    if allowed:
        service.create_user("boundary_cli", password)
    else:
        with pytest.raises(ValueError, match="6到12"):
            service.create_user("boundary_cli", password)
    with TestClient(create_app(settings), headers=HEADERS) as client:
        payload = {"username": "boundary_web", "password": password}
        assert client.post("/api/v1/auth/register", json=payload).status_code == (
            201 if allowed else 422
        )
        assert client.post("/api/v1/auth/login", json=payload).status_code == (
            200 if allowed else 422
        )


"""默认管理员测试函数：部署命令首次创建admin，重复执行不覆盖已有口令。"""

def test_default_admin_initialization(
    accounts, store_engine: Engine, monkeypatch, tmp_path,
) -> None:
    from scripts import manage_accounts
    config = tmp_path / ".env"
    config.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["manage_accounts", "--env-file", str(config), "init-admin"])
    monkeypatch.setattr(manage_accounts, "create_database_engine", lambda _: store_engine)
    service, _, _ = accounts
    assert manage_accounts.main() == 0
    user, token = service.login("admin", "123456")
    assert user.role == "admin"
    service.logout(token)
    from app.services.auth import PASSWORDS
    with store_engine.begin() as unit:
        unit.execute(update(User).where(User.id == user.id).values(
            password_hash=PASSWORDS.hash("changed123"),
        ))
    assert manage_accounts.main() == 0
    assert service.login("admin", "123456") is None
    assert service.login("admin", "changed123") is not None
