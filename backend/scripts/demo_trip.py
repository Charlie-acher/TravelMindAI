"""存储演示的命令行入口，不是 Web API 或自动行程规划器。

在 backend 目录执行：
    python -m scripts.demo_trip --env-file .env
不传 --session-id 时，会向指定数据库新增一个会话、一份需求和一份演示草稿。
传入 --session-id 时，只读取该会话的最新草稿；可以关闭程序后重新运行验证持久化。
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.database import create_database_engine
from app.schemas.budget import BudgetInput, BudgetSummary
from app.services.budget_service import calculate_budget
from app.services.trip_service import TripService

"""读取配置后执行一次保存或读取，成功返回0，失败返回1；不打印连接密码。

argv 是可选命令行参数列表，便于测试直接调用；没有传入时由 argparse 读取终端参数。
"""

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="保存演示草稿，或按会话 ID 读取已有草稿")
    parser.add_argument("--env-file", type=Path, help="显式指定数据库配置文件")
    parser.add_argument("--session-id", type=UUID, help="只读取这个会话的最新草稿，不新增数据")
    parser.add_argument("--user-id", type=UUID, help="新建演示会话必须指定已注册账号")
    arguments = parser.parse_args(argv)
    if arguments.env_file is not None and not arguments.env_file.is_file():
        print("配置文件不存在，请检查 --env-file 路径。", file=sys.stderr)
        return 1

    engine: Engine | None = None
    try:
        engine = create_database_engine(load_settings(arguments.env_file))
        store = TripService(engine)
        if arguments.session_id is not None:
            saved = store.get_draft(arguments.session_id)
            if saved is None:
                print("没有找到该会话的草稿，请检查 session-id。", file=sys.stderr)
                return 1
        else:
            if arguments.user_id is None:
                print("新建会话须指定已注册账号的--user-id。", file=sys.stderr)
                return 1
            # 复用已有输入校验和预算公式，不另写一套金额计算逻辑。
            request = BudgetInput(days=3, travelers=2, total_budget=Decimal("5000.00"))
            estimate = calculate_budget(
                days=request.days,
                travelers=request.travelers,
                total_budget=request.total_budget,
                lodging=request.lodging,
            )
            trip = store.create_session("M1-B 存储演示：两人三天", user_id=arguments.user_id)
            saved = store.save_draft(
                trip.id,
                # mode="json" 把 Decimal、date 等转换成可以写进 JSONB 的字符串。
                request_json=request.model_dump(mode="json"),
                itinerary_json={
                    "title": "存储演示草稿（非 AI 生成）",
                    "days": [],  # 尚未生成逐日活动，不把预算演示伪装成完整行程。
                    "budget": BudgetSummary.model_validate(estimate).model_dump(mode="json"),
                },
            )
        # UUID 也需要转成字符串才能作为普通 JSON 输出；金额已经是字符串。
        print(
            json.dumps(
                {
                    "session_id": str(saved.itinerary.session_id),
                    "itinerary_id": str(saved.itinerary.id),
                    "version": saved.itinerary.version,
                    "request_version": saved.requirement.version,
                    "budget": saved.itinerary.itinerary_json.get("budget"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (ValueError, SQLAlchemyError):
        # 不回显异常原文，其中可能含连接信息；存储方法会自行回滚失败的事务。
        print("存储操作失败，请检查数据库配置、容器状态，以及是否执行了迁移。", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


# 仅直接运行模块时执行 main；import 不读取配置或写入数据库。
if __name__ == "__main__":
    raise SystemExit(main())
