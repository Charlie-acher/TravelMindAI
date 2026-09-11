"""Alembic 执行迁移时加载的入口，不是 Web 应用的启动代码。

命令示例：python -m alembic -x env_file=.env upgrade head
从明确指定的配置文件读取地址，复用现有数据库连接工厂。
测试通过 config.attributes 传入临时 schema 的连接，不读取个人配置。
"""

from pathlib import Path

from alembic import context
from sqlalchemy import Connection

from app.config import load_settings
from app.database import create_database_engine
from app.models import Base

# context.config 是 Alembic 传给本脚本的配置，含 pyproject.toml 和命令行参数。
config = context.config


"""在指定连接上执行迁移，用事务包住一批 DDL；PostgreSQL 支持事务化建表。"""

def run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


"""普通命令创建连接；测试复用传入连接；--sql 模式仅输出 SQL，不执行它。"""

def run_migrations() -> None:
    if context.is_offline_mode():
        # 只生成 PostgreSQL SQL，无需数据库密码，也无需联网。
        context.configure(
            dialect_name="postgresql",
            target_metadata=Base.metadata,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        run_with_connection(supplied_connection)
        return

    # -x 是 Alembic 的扩展参数，这里约定 env_file 表示需要读取的文件。
    options = context.get_x_argument(as_dictionary=True)
    env_file = Path(options["env_file"]) if "env_file" in options else None
    if env_file is not None and not env_file.is_file():
        raise ValueError("指定的 env_file 不存在，请检查路径")
    engine = create_database_engine(load_settings(env_file))
    try:
        with engine.connect() as connection:
            run_with_connection(connection)
    finally:
        engine.dispose()


# 此文件只由 Alembic 加载；Web 应用不能导入它，否则会执行迁移。
run_migrations()
