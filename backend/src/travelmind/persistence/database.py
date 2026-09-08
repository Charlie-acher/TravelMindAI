"""PostgreSQL 连接入口，类似 Java 项目中创建 DataSource 的位置。

先从 Settings 取得地址，再创建 SQLAlchemy Engine，最后按需借用连接执行 SQL。
Engine 管理连接池，并不是一条始终独占的数据库连接。
本阶段提供只读连通性检查，不创建业务表、不运行迁移、不改变预算接口。

在 backend 目录运行：
    ./.venv/Scripts/python.exe -m travelmind.persistence.database --env-file .env
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from travelmind.settings import Settings, load_settings


def create_database_engine(settings: Settings) -> Engine:
    """根据显式传入的配置创建连接池管理器，此时不会发起网络连接。

    settings：由 settings.py 加载的配置对象，数据库地址可能还没有填写。
    返回值 Engine：调用方负责复用，并在使用结束时调用 dispose() 释放连接池。
    """
    if settings.database_url is None:
        raise ValueError("请配置 TRAVELMIND_DATABASE_URL 后再检查数据库")

    try:
        # SecretStr 默认隐藏内容；只有交给数据库驱动时才显式取出真实地址。
        # make_url 解析协议、用户名、密码、主机、端口和数据库名，不执行网络请求。
        address = make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        # 解析异常可能含原始地址；替换成安全说明，不把密码带到终端或日志。
        raise ValueError("TRAVELMIND_DATABASE_URL 不是有效的数据库连接地址") from None

    # +psycopg 明确选用本项目安装的 psycopg 3 驱动，避免误用其他驱动。
    if address.drivername != "postgresql+psycopg":
        raise ValueError("TRAVELMIND_DATABASE_URL 必须以 postgresql+psycopg:// 开头")

    return create_engine(
        address,
        pool_pre_ping=True,  # 借用旧连接前检查它是否存活，处理数据库重启后的失效连接。
        connect_args={"connect_timeout": 5},  # 单次连接建立最多等待5秒，避免无期限卡住。
        hide_parameters=True,  # SQL 执行异常和日志不显示绑定参数中的业务数据。
        echo=False,  # 不主动打印每条 SQL；也不要自行打印完整数据库地址。
    )


def check_connection(engine: Engine) -> dict[str, str]:
    """执行只读 SQL，确认 Python 实际连到的数据库和用户。

    engine.connect() 才会真正向 PostgreSQL 建立或借用连接。
    with 退出时会归还连接；即使 SQL 报错，也会执行清理。
    """
    with engine.connect() as connection:
        # text() 把 SQL 字符串包装成 SQLAlchemy 可以执行的对象。
        # 只查询当前数据库、用户名，不读业务表，也不返回密码。
        row = connection.execute(text("SELECT current_database(), current_user")).one()
        return {"database": str(row[0]), "user": str(row[1])}


def main(argv: list[str] | None = None) -> int:
    """命令行入口：解析参数、加载配置、检查连接并返回退出码。

    argv=None 时从终端读取参数；测试可以传入列表，避免依赖真实命令行。
    退出码0表示成功，1表示失败，便于人在终端判断或供脚本调用。
    """
    parser = argparse.ArgumentParser(description="只读检查 TravelMindAI 的 PostgreSQL 连接")
    parser.add_argument("--env-file", type=Path, help="显式指定配置文件，不传则只读环境变量")
    arguments = parser.parse_args(argv)

    # 保持 M0 规则：不自动查找 .env。明确指定的文件不存在时及时报告，避免误解。
    if arguments.env_file is not None and not arguments.env_file.is_file():
        print("配置文件不存在，请检查 --env-file 路径。", file=sys.stderr)
        return 1

    engine: Engine | None = None  # 创建前先用 None 占位，方便 finally 判断是否需要清理。
    try:
        settings = load_settings(arguments.env_file)
        engine = create_database_engine(settings)
        result = check_connection(engine)
        print(f"连接成功：database={result['database']}, user={result['user']}")
        return 0
    except (ValueError, SQLAlchemyError):
        # 驱动异常可能携带连接信息；这里不打印异常原文或完整堆栈。
        print(
            "连接失败：请检查 TRAVELMIND_DATABASE_URL 的格式、用户名、密码，以及容器状态和端口。",
            file=sys.stderr,
        )
        return 1
    finally:
        # dispose() 关闭池中空闲连接。此命令是一次性检查，结束后不再保留连接池。
        if engine is not None:
            engine.dispose()


# 只有 python -m ... 直接执行本模块时才运行 main；import 时不会执行。
if __name__ == "__main__":
    raise SystemExit(main())
