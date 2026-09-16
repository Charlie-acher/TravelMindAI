"""多个集成测试共用的 PostgreSQL 临时环境。

pytest 自动发现 conftest.py 中的 fixture，测试文件无需手工导入。
每个用例有独立 schema，清理只作用于本测试创建的数据。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import create_database_engine
from tests.helpers import TEST_USER_ID

"""创建临时 schema 和专用 Engine，保证并发线程各用自己的数据库连接。"""

@pytest.fixture
def store_engine() -> Iterator[Engine]:
    address = os.environ.get("TRAVELMIND_TEST_DATABASE_URL")
    if not address:
        pytest.skip("未设置 TRAVELMIND_TEST_DATABASE_URL，不运行真实 PostgreSQL 测试")
    settings = Settings(database_url=SecretStr(address))
    admin_engine = create_database_engine(settings)
    schema_name = f"travelmind_test_{uuid4().hex}"
    engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        try:
            # 每条测试连接从建立时就只使用本测试的 schema，不能落入 public。
            test_url = make_url(address).update_query_dict(
                {"options": f"-csearch_path={schema_name}"}
            )
            engine = create_database_engine(
                Settings(database_url=SecretStr(test_url.render_as_string(hide_password=False)))
            )
            config = Config(toml_file=str(Path(__file__).resolve().parents[1] / "pyproject.toml"))
            with engine.connect() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            # 旧业务回归也写入真实账号外键；权限测试另外通过真实注册/登录验证。
            from sqlalchemy import insert

            from app.models.auth import User

            with engine.begin() as connection:
                connection.execute(insert(User).values(
                    id=TEST_USER_ID, username="legacy-test", password_hash="test-only",
                    role="admin",
                ))
            yield engine
        finally:
            # 先关闭测试连接，再删除自己创建的 schema；从不删除 public 的表。
            if engine is not None:
                engine.dispose()
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
    finally:
        admin_engine.dispose()


