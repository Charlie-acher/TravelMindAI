"""独立的数据库连接检查命令：在 backend 下运行 python -m scripts.check_db --env-file .env。

只读检查当前数据库和用户；导入此脚本不会自动执行检查。
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.database import check_connection, create_database_engine

"""命令行入口：解析参数、加载配置、检查连接并返回退出码。

argv=None 时从终端读取参数；测试可以传入列表，避免依赖真实命令行。
退出码0表示成功，1表示失败，便于人在终端判断或供脚本调用。
"""

def main(argv: list[str] | None = None) -> int:
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
