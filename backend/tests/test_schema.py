"""真实 PostgreSQL 的表结构与迁移测试。

默认不读取个人 .env；只有设置 TRAVELMIND_TEST_DATABASE_URL 才运行。
每个测试创建自己随机命名的 schema（表的命名空间），结束后只删除该 schema。
移除外键、唯一约束、正版本号检查或破坏迁移，都应该让这些测试失败。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import create_database_engine

# 给本文件全部测试标记 postgres；标记本身不负责跳过，下面的 fixture 负责。
pytestmark = pytest.mark.postgres


"""在随机且独立的命名空间里验收迁移，不接触 public 下的业务表。"""

@pytest.fixture
def migrated_database() -> Iterator[tuple[Connection, Config]]:
    address = os.environ.get("TRAVELMIND_TEST_DATABASE_URL")
    if not address:
        pytest.skip("未设置 TRAVELMIND_TEST_DATABASE_URL，不运行真实 PostgreSQL 测试")

    engine = create_database_engine(Settings(database_url=SecretStr(address)))
    # 名称完全由本测试生成，只包含固定前缀和十六进制字符，不接受用户输入。
    schema_name = f"travelmind_test_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    config = Config(toml_file=str(backend / "pyproject.toml"))
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.commit()
            try:
                connection.execute(text(f'SET search_path TO "{schema_name}"'))
                connection.commit()
                # 迁移复用这条连接，因此只在当前测试 schema 中创建表。
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                yield connection, config
            finally:
                # 先撤销可能失败的事务，再清理本测试创建的 schema。
                connection.rollback()
                connection.execute(text("SET search_path TO public"))
                connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
                connection.commit()
    finally:
        engine.dispose()


"""首次建表、重复升级、回滚、再次升级都可执行；ORM 声明须与迁移结果一致。"""

def test_migration_round_trip_matches_models(migrated_database: tuple[Connection, Config]) -> None:
    from app.models.trip import Base

    connection, config = migrated_database
    expected = {
        "sessions", "travel_requests", "itineraries", "requirement_turns", "documents",
        "document_chunks", "document_jobs",
        "alembic_version"
    }
    assert set(inspect(connection).get_table_names()) == expected
    differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []
    connection.commit()

    command.upgrade(config, "head")  # 已经是最新版，再执行不应重复创建表。
    command.downgrade(config, "base")
    assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    connection.commit()
    command.upgrade(config, "head")
    assert set(inspect(connection).get_table_names()) == expected


"""从M1升级新增对话表时，已有会话数据仍可读取；回滚也只在临时schema执行。"""

def test_chat_migration_preserves_existing_session(
    migrated_database: tuple[Connection, Config],
) -> None:
    from app.models.trip import TravelSession

    connection, config = migrated_database
    connection.commit()
    command.downgrade(config, "0001_business_tables")
    with Session(connection) as unit:
        trip = TravelSession(title="升级前已有的会话")
        unit.add(trip)
        unit.commit()
        session_id = trip.id
    connection.commit()
    command.upgrade(config, "head")
    with Session(connection) as reader:
        assert reader.get(TravelSession, session_id).title == "升级前已有的会话"
    assert "requirement_turns" in inspect(connection).get_table_names()


"""用 ORM 写入三张表后，换一个 ORM Session 仍能读到关联数据和精确金额字符串。"""

def test_orm_rows_survive_session_close(migrated_database: tuple[Connection, Config]) -> None:
    from app.models.trip import Itinerary, TravelRequest, TravelSession

    connection, _ = migrated_database
    with Session(connection) as unit:
        trip = TravelSession(title="杭州三日游")
        unit.add(trip)
        unit.flush()  # 把 INSERT 发给数据库，得到默认生成的 UUID；此时尚未提交。
        requirement = TravelRequest(
            session_id=trip.id, version=1, request_json={"days": 3, "total_budget": "5000.01"}
        )
        unit.add(requirement)
        unit.flush()
        draft = Itinerary(
            session_id=trip.id,
            request_id=requirement.id,
            version=1,
            itinerary_json={"title": "西湖行程", "total": "2442.00"},
        )
        unit.add(draft)
        unit.flush()
        trip_id, requirement_id, draft_id = trip.id, requirement.id, draft.id
        unit.commit()

    with Session(connection) as reader:
        saved = reader.get(Itinerary, draft_id)
        saved_request = reader.get(TravelRequest, requirement_id)
        saved_trip = reader.get(TravelSession, trip_id)
        assert saved is not None and saved_request is not None and saved_trip is not None
        assert saved.session_id == trip_id
        assert saved.request_id == requirement_id
        assert saved.status == "draft"
        assert saved.itinerary_json == {"title": "西湖行程", "total": "2442.00"}
        assert saved_request.request_json["total_budget"] == "5000.01"
        assert saved_trip.title == "杭州三日游"
        assert saved_trip.thread_id is not None
        assert saved.created_at.tzinfo is not None


"""数据库自身拒绝重复版本、非法版本和不存在的会话，不只依赖 Python 校验。"""

@pytest.mark.parametrize(
    "case", ["duplicate_version", "zero_version", "missing_session", "json_array"]
)
def test_request_constraints(migrated_database: tuple[Connection, Config], case: str) -> None:
    from app.models.trip import TravelRequest, TravelSession

    connection, _ = migrated_database
    with Session(connection) as unit:
        trip = TravelSession(title="约束测试")
        unit.add(trip)
        unit.flush()
        unit.add(TravelRequest(session_id=trip.id, version=1, request_json={"days": 3}))
        unit.commit()
        trip_id = trip.id
        unit.rollback()
        # begin_nested 是 SAVEPOINT；预期的 SQL 错误只回滚到保存点。
        with pytest.raises(IntegrityError), unit.begin_nested():
            if case == "json_array":
                unit.execute(
                    text(
                        "INSERT INTO travel_requests (id,session_id,version,request_json) "
                        "VALUES (:id,:session_id,2,'[]'::jsonb)"
                    ),
                    {"id": uuid4(), "session_id": trip_id},
                )
            else:
                unit.add(
                    TravelRequest(
                        session_id=uuid4() if case == "missing_session" else trip_id,
                        version=0 if case == "zero_version" else 1,
                        request_json={},
                    )
                )
                unit.flush()


"""草稿不能引用另一个会话的需求；单独检查两个普通外键无法阻止这种串会话。"""

def test_itinerary_cannot_reference_another_session(
    migrated_database: tuple[Connection, Config],
) -> None:
    from app.models.trip import Itinerary, TravelRequest, TravelSession

    connection, _ = migrated_database
    with Session(connection) as unit:
        first, second = TravelSession(title="甲"), TravelSession(title="乙")
        unit.add_all([first, second])
        unit.flush()
        requirement = TravelRequest(session_id=first.id, version=1, request_json={})
        unit.add(requirement)
        unit.flush()
        with pytest.raises(IntegrityError), unit.begin_nested():
            unit.add(
                Itinerary(
                    session_id=second.id, request_id=requirement.id, version=1, itinerary_json={}
                )
            )
            unit.flush()


"""行程版本和内容在数据库层受约束，错误写入不能绕过这些规则。"""

@pytest.mark.parametrize(
    "case", ["duplicate_version", "zero_version", "invalid_status", "json_array"]
)
def test_itinerary_constraints(migrated_database: tuple[Connection, Config], case: str) -> None:
    from app.models.trip import Itinerary, TravelRequest, TravelSession

    connection, _ = migrated_database
    with Session(connection) as unit:
        trip = TravelSession(title="行程约束测试")
        unit.add(trip)
        unit.flush()
        requirement = TravelRequest(session_id=trip.id, version=1, request_json={})
        unit.add(requirement)
        unit.flush()
        unit.add(
            Itinerary(session_id=trip.id, request_id=requirement.id, version=1, itinerary_json={})
        )
        unit.flush()
        with pytest.raises(IntegrityError), unit.begin_nested():
            if case == "json_array":
                unit.execute(
                    text(
                        "INSERT INTO itineraries (id,session_id,request_id,version,itinerary_json) "
                        "VALUES (:id,:session_id,:request_id,2,'[]'::jsonb)"
                    ),
                    {"id": uuid4(), "session_id": trip.id, "request_id": requirement.id},
                )
            else:
                unit.add(
                    Itinerary(
                        session_id=trip.id,
                        request_id=requirement.id,
                        version=0
                        if case == "zero_version"
                        else (1 if case == "duplicate_version" else 2),
                        status="unknown" if case == "invalid_status" else "draft",
                        itinerary_json={},
                    )
                )
                unit.flush()


"""会话状态只能取允许值，thread_id 不能在不同会话间重复。"""

@pytest.mark.parametrize("case", ["duplicate_thread", "invalid_status"])
def test_session_constraints(migrated_database: tuple[Connection, Config], case: str) -> None:
    from app.models.trip import TravelSession

    connection, _ = migrated_database
    with Session(connection) as unit:
        trip = TravelSession(title="第一场会话")
        unit.add(trip)
        unit.flush()
        with pytest.raises(IntegrityError), unit.begin_nested():
            unit.add(
                TravelSession(
                    title="第二场会话",
                    thread_id=trip.thread_id if case == "duplicate_thread" else uuid4(),
                    status="unknown" if case == "invalid_status" else "active",
                )
            )
            unit.flush()
