"""会话/草稿 HTTP 契约与生命周期测试。

使用 TestClient 请求完整应用；postgres 标记的用例使用临时 schema 内的真实数据库。
未标记的测试不需要 Docker，验证未配置和数据库故障时的明确响应。
"""

from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.trip import Itinerary
from tests.helpers import authenticated_client as TestClient

"""把临时数据库连接地址交给应用，应用自身创建并管理连接池。"""

@pytest.fixture
def configured_settings(store_engine: Engine) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(store_engine.url.render_as_string(hide_password=False)),
    )


"""未配置数据库时预算仍可用，存储请求得到503，并保留可关联的请求编号。"""

def test_budget_only_mode_reports_storage_disabled() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        response = client.post("/api/v1/sessions", json={"title": "测试会话"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "HTTP_503"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json()["dependencies"] == {"postgresql": "disabled", "milvus": "disabled"}
        assert (
            client.post(
                "/api/v1/budget/estimate",
                json={
                    "days": 3,
                    "travelers": 2,
                    "total_budget": "5000",
                },
            ).status_code
            == 200
        )


"""数据库断开不影响存活接口；就绪和存储返回脱敏的503，应用仍可退出。"""

def test_unreachable_database_is_not_ready_and_does_not_expose_password() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://u:example_secret@127.0.0.1:1/no_database")
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        for response in [
            client.get("/api/v1/health/ready"),
            client.post("/api/v1/sessions", json={"title": "失败测试"}),
        ]:
            assert response.status_code == 503
            assert response.json()["error"]["retryable"] is True
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
            assert "example_secret" not in response.text
            assert "Traceback" not in response.text


"""应用重建后仍能通过 HTTP 读回原草稿；最新/历史版本和服务端金额都正确。"""

@pytest.mark.postgres
def test_http_save_versions_and_restore_in_new_app(configured_settings: Settings) -> None:
    with TestClient(create_app(configured_settings)) as client:
        created = client.post("/api/v1/sessions", json={"title": "  杭州三日游  "})
        assert created.status_code == 201
        trip = created.json()["session"]
        session_id = trip["id"]
        assert trip["title"] == "杭州三日游"
        UUID(trip["thread_id"])
        path = f"/api/v1/sessions/{session_id}/drafts"
        for days in [3, 4]:
            saved = client.post(
                path,
                json={
                    "title": "预算草稿",
                    "notes": "先记录预算，尚未安排每日活动",
                    "requirements": {"days": days, "travelers": 2, "total_budget": "5000"},
                },
            )
            assert saved.status_code == 201
            assert saved.json()["request_id"] == saved.headers["X-Request-ID"]
        latest = client.get(path).json()["draft"]
        first = client.get(path, params={"version": 1}).json()["draft"]
        assert latest["itinerary"]["version"] == 2
        assert latest["requirement"]["request_json"]["days"] == 4
        assert first["itinerary"]["itinerary_json"]["budget"]["total"] == "2442.00"
        assert first["itinerary"]["itinerary_json"]["days"] == []
        assert first["itinerary"]["request_id"] == first["requirement"]["id"]
    with TestClient(create_app(configured_settings)) as restarted:
        assert restarted.get(path).json()["draft"] == latest
        assert restarted.get(f"/api/v1/sessions/{session_id}").json()["session"]["id"] == session_id
        ready = restarted.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json()["dependencies"] == {"postgresql": "ready", "milvus": "disabled"}


"""入口拒绝客户端预算结果、非法天数和错误日期，且不写入草稿。"""

@pytest.mark.postgres
@pytest.mark.parametrize(
    "patch",
    [
        {"total": "1.00"},
        {"status": "confirmed"},
        {"requirements": {"days": 0, "travelers": 2, "total_budget": "5000"}},
        {"requirements": {"days": 3, "travelers": 2, "total_budget": True}},
        {
            "requirements": {
                "days": 3,
                "travelers": 2,
                "total_budget": "5000",
                "start_date": "2026-10-03",
                "end_date": "2026-10-01",
            }
        },
        {"title": "   "},
        {"notes": "字" * 5001},
    ],
)
def test_invalid_draft_is_not_saved(
    configured_settings: Settings,
    store_engine: Engine,
    patch: dict,
) -> None:
    with TestClient(create_app(configured_settings)) as client:
        session_id = client.post("/api/v1/sessions", json={"title": "输入检查"}).json()["session"][
            "id"
        ]
        body = {
            "title": "预算草稿",
            "requirements": {"days": 3, "travelers": 2, "total_budget": "5000"},
        }
        response = client.post(f"/api/v1/sessions/{session_id}/drafts", json=body | patch)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with Session(store_engine) as reader:
        assert reader.scalar(select(func.count()).select_from(Itinerary)) == 0


"""不存在的资源返回404；错误 UUID、版本和标题返回422。"""

@pytest.mark.postgres
def test_missing_resources_and_invalid_paths(configured_settings: Settings) -> None:
    with TestClient(create_app(configured_settings)) as client:
        missing = uuid4()
        for path in [f"/api/v1/sessions/{missing}", f"/api/v1/sessions/{missing}/drafts"]:
            assert client.get(path).status_code == 404
        assert (
            client.post(
                f"/api/v1/sessions/{missing}/drafts",
                json={
                    "title": "草稿",
                    "requirements": {"days": 3, "travelers": 2, "total_budget": "5000"},
                },
            ).status_code
            == 404
        )
        assert client.get("/api/v1/sessions/not-uuid").status_code == 422
        assert client.get(f"/api/v1/sessions/{missing}/drafts?version=0").status_code == 422
        for body in [{"title": "  "}, {"title": "字" * 201}, {"title": "会话", "user_id": "1"}]:
            assert client.post("/api/v1/sessions", json=body).status_code == 422


"""表缺失时就绪检查不能只凭 SELECT 1 宣称可用；退出 lifespan 必须释放连接池。"""

@pytest.mark.postgres
def test_readiness_checks_tables_and_shutdown_disposes_engine(
    configured_settings: Settings,
    store_engine: Engine,
) -> None:
    app = create_app(configured_settings)
    disposed: list[bool] = []
    with TestClient(app) as client:
        engine = app.state.database_engine
        event.listen(engine, "engine_disposed", lambda _engine: disposed.append(True))
        with store_engine.begin() as connection:
            connection.execute(text("DROP TABLE itineraries"))  # 仅当前用例的临时 schema。
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_NOT_READY"
        assert client.get("/api/v1/health/live").status_code == 200
        session_id = client.post("/api/v1/sessions", json={"title": "缺表测试"}).json()["session"][
            "id"
        ]
        saved = client.post(
            f"/api/v1/sessions/{session_id}/drafts",
            json={
                "title": "草稿",
                "requirements": {"days": 3, "travelers": 2, "total_budget": "5000"},
            },
        )
        assert saved.status_code == 503
        assert saved.json()["error"]["code"] == "DATABASE_NOT_READY"
    assert disposed == [True]
    assert app.state.database_engine is None
    assert app.state.trip_service is None


"""只注入数据库异常来验证HTTP映射；实际事务和约束已经由数据库集成测试覆盖。"""

@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (IntegrityError("private SQL", {}, Exception("private detail")), 409, "STORAGE_CONFLICT"),
        (SQLAlchemyError("private detail"), 500, "DATABASE_ERROR"),
    ],
)
def test_database_errors_use_safe_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    failure: SQLAlchemyError,
    status: int,
    code: str,
) -> None:
    from app.services.trip_service import TripService

    """模拟存储边界抛异常；本用例不会连接地址所指向的数据库。"""

    def reject_create(self: TripService, title: str, *, user_id: UUID | None = None) -> None:
        raise failure

    monkeypatch.setattr(TripService, "create_session", reject_create)
    settings = Settings(database_url=SecretStr("postgresql+psycopg://u:unused@127.0.0.1:1/db"))
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/sessions", json={"title": "异常映射"})
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert "private" not in response.text
        assert response.json()["request_id"] == response.headers["X-Request-ID"]


"""Swagger声明与真实响应契约一致，包含四个存储操作及统一错误结构。"""

def test_openapi_describes_storage_endpoints() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        schema = client.get("/openapi.json").json()
        assert client.get("/docs").status_code == 200
        paths = schema["paths"]
        assert "post" in paths["/api/v1/sessions"]
        assert "get" in paths["/api/v1/sessions/{session_id}"]
        drafts = paths["/api/v1/sessions/{session_id}/drafts"]
        assert "get" in drafts and "post" in drafts
        for status in ["404", "409", "422", "500", "503"]:
            assert drafts["post"]["responses"][status]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("/ErrorResponse")
