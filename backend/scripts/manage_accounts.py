"""部署命令层：初始化管理员、创建账号和显式认领旧会话，不提供网页提权入口。"""

import argparse
import getpass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import load_settings
from app.database import create_database_engine
from app.models.auth import User
from app.models.trip import TravelSession
from app.services.auth import AuthService, normalize_username

"""命令执行函数：密码隐藏输入，不把口令或连接地址写进命令行历史。"""

def main() -> int:
    parser = argparse.ArgumentParser(description="TravelMindAI账号与历史归属管理")
    parser.add_argument("--env-file", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-admin", help="首次创建默认管理员admin，已有账号不会重置")
    create = commands.add_parser("create-user", help="创建普通用户或管理员")
    create.add_argument("username")
    create.add_argument("--role", choices=["admin", "user"], default="user")
    commands.add_parser("list-unclaimed", help="只列出未认领会话的编号和标题")
    claim = commands.add_parser("claim-sessions", help="认领明确列出的旧会话")
    claim.add_argument("username")
    claim.add_argument("session_ids", nargs="+", type=UUID)
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("指定的配置文件不存在")
    engine = create_database_engine(load_settings(args.env_file))
    try:
        service = AuthService(engine)
        if args.command == "init-admin":
            with Session(engine) as unit:
                existing = unit.scalar(select(User).where(User.username == "admin"))
                if existing is not None and existing.role != "admin":
                    raise ValueError("admin名称已被普通用户占用，不自动提升权限")
            if existing is None:
                service.create_user("admin", "123456", "admin")
                print("已初始化管理员admin，初始密码为123456")
            else:
                print("管理员admin已存在，保留现有密码")
        elif args.command == "create-user":
            password = getpass.getpass("密码（6到12个字符）：")
            if password != getpass.getpass("再次输入密码："):
                raise ValueError("两次密码不一致")
            user = service.create_user(args.username, password, args.role)
            print(f"已创建账号：{user.username}，角色：{user.role}，编号：{user.id}")
        elif args.command == "list-unclaimed":
            with Session(engine) as unit:
                rows = unit.scalars(select(TravelSession).where(
                    TravelSession.user_id.is_(None),
                ).order_by(TravelSession.created_at)).all()
                for row in rows:
                    print(f"{row.id}\t{row.title}")
                print(f"未认领：{len(rows)}个")
        else:
            with Session(engine) as unit:
                user_id = unit.scalar(select(User.id).where(
                    User.username == normalize_username(args.username),
                ))
            if user_id is None:
                raise ValueError("账号不存在")
            count = service.claim_sessions(user_id, args.session_ids)
            print(f"已核对并认领{count}个指定会话")
    except ValueError as error:
        print(str(error))
        return 1
    except SQLAlchemyError:
        print("账号操作未完成，请检查迁移、数据库连接或重复账号；没有输出连接凭据")
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
