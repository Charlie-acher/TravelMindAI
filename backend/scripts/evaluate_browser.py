"""验收入口层：创建专用账号运行真实浏览器链，成功后按归属清理，保留调用费用。"""

import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.config import Settings, load_settings
from app.database import create_database_engine
from app.models.auth import AuthSession, User
from app.models.trip import TravelSession
from app.services.auth import AuthService
from app.services.trip_service import TripService

"""清理函数：仅清理本次创建的账号和所属会话，调用费用无级联删除。"""

def cleanup_accounts(engine: Engine, users: list[User], attachment_dir: Path) -> None:
    service = TripService(engine, attachment_dir=attachment_dir)
    for user in users:
        with Session(engine) as db:
            identifiers = list(db.scalars(select(TravelSession.id).where(
                TravelSession.user_id == user.id)))
        for identifier in identifiers:
            service.delete_session(identifier, user_id=user.id)
        with Session(engine) as db, db.begin():
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            db.execute(delete(User).where(User.id == user.id, User.username == user.username))


"""容器配置函数：只替换独立数据库地址；模型与业务配置仍来自正式.env。"""

def compose_target(env_file: Path) -> tuple[Settings, str]:
    values = {**dotenv_values(env_file), **os.environ}
    password = values.get("TRAVELMIND_COMPOSE_DB_PASSWORD") or ""
    if not password.isascii() or not password.isalnum():
        raise ValueError("Compose数据库密码必须为字母数字；请先配置backend/.env")
    db_port = int(values.get("TRAVELMIND_COMPOSE_POSTGRES_PORT") or "15432")
    web_port = int(values.get("TRAVELMIND_COMPOSE_PORT") or "8080")
    if not all(1 <= port <= 65535 for port in (db_port, web_port)):
        raise ValueError("Compose端口必须在1到65535之间")
    settings = load_settings(env_file).model_copy(update={"database_url": SecretStr(
        f"postgresql+psycopg://travelmind:{password}@127.0.0.1:{db_port}/travelmind")})
    return settings, f"http://127.0.0.1:{web_port}"


"""容器清理函数：先由服务删除私有原件，再清账号；本机不直接访问命名卷。"""

def cleanup_remote_accounts(
    engine: Engine, users: list[User], client: httpx.Client, cookies: dict[UUID, httpx.Cookies],
) -> None:
    for user in users:
        with Session(engine) as db:
            identifiers = list(db.scalars(select(TravelSession.id).where(
                TravelSession.user_id == user.id)))
        if identifiers:
            # 复用预检登录态，不让清理再次消耗代理IP的登录限额。
            client.cookies.clear()
            client.cookies.update(cookies[user.id])
        for identifier in identifiers:
            client.delete(f"/api/v1/sessions/{identifier}").raise_for_status()
        with Session(engine) as db, db.begin():
            # 确認服务确实删完，才移除账号；失败时留下编号供人工排查。
            if db.scalar(select(TravelSession.id).where(TravelSession.user_id == user.id)):
                raise RuntimeError("容器会话清理未完成，保留验收账号")
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            db.execute(delete(User).where(User.id == user.id, User.username == user.username))


"""主函数：默认只说明步骤，live才创建账号并发起有费用的模型查询。"""

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="真实浏览器与后端全链验收")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--provider", choices=["deepseek", "kimi", "qwen"], default="deepseek")
    parser.add_argument("--scenario", choices=["journey", "uploads", "recovery", "ocr",
                                               "failover", "all"], default="all")
    parser.add_argument("--compose", action="store_true", help="使用独立Compose网页及数据库")
    args = parser.parse_args()
    if not args.live:
        print("--live调用真实模型；--compose改用独立Compose。成功清理专用账号，保留费用。")
        return 0
    node = shutil.which("node")
    cli = root.parent / "frontend/node_modules/@playwright/test/cli.js"
    if not node or not cli.exists():
        parser.error("请先在frontend执行npm ci及npx playwright install chromium")
    frontend = root.parent / "frontend"
    checked = subprocess.run([node, str(frontend / "node_modules/typescript/bin/tsc"),
                              "-p", "tsconfig.browser.json"], cwd=frontend, check=False)
    if checked.returncode:
        return checked.returncode
    settings, api_url = (compose_target(args.env_file) if args.compose
                         else (load_settings(args.env_file), "http://127.0.0.1:8000"))
    engine = create_database_engine(settings)
    users: list[User] = []
    cookies: dict[UUID, httpx.Cookies] = {}
    launched, passed = False, False
    password = secrets.token_hex(5)
    try:
        with httpx.Client(base_url=api_url, timeout=30,
                          headers={"X-Requested-With": "TravelMindAI"}) as client:
            client.get("/api/v1/health/ready").raise_for_status()
            env = os.environ.copy()
            env["TRAVELMIND_E2E_PASSWORD"] = password
            env["TRAVELMIND_E2E_PROVIDER"] = args.provider
            if args.compose:
                env["TRAVELMIND_E2E_BASE_URL"] = api_url
            else:
                env.pop("TRAVELMIND_E2E_BASE_URL", None)
            for role in ("OWNER", "OTHER", "ADMIN"):
                user = AuthService(engine).create_user(
                    "qa-browser-" + uuid4().hex[:16], password,
                    role="admin" if role == "ADMIN" else "user")
                users.append(user)
                env[f"TRAVELMIND_E2E_{role}"] = user.username
                # 核对服务与.env连接的是同一数据库，避免在错误环境中开始验收。
                # 切换账号前清空客户端Cookie，避免登录接口撤销上一个账号的预检令牌。
                client.cookies.clear()
                response = client.post("/api/v1/auth/login", json={
                    "username": user.username, "password": password})
                response.raise_for_status()
                if response.json()["id"] != str(user.id):
                    raise RuntimeError("运行服务与验收配置身份不一致")
                cookies[user.id] = httpx.Cookies(client.cookies)
            print("开始真实浏览器验收；模型请求有费用，失败不自动重跑。", flush=True)
            launched = True
            # 故障注入仅在独立配置下显式运行，普通全链不改当前模型入口。
            selected = ([f"{name}.spec.ts" for name in
                         ("journey", "uploads", "recovery", "ocr")]
                        if args.scenario == "all" else [f"{args.scenario}.spec.ts"])
            result = subprocess.run(
                [node, str(cli), "test", "--config=playwright.live.config.ts", *selected],
                cwd=frontend, env=env, check=False)
            passed = result.returncode == 0
            return result.returncode
    finally:
        try:
            if passed or not launched:
                if args.compose:
                    with httpx.Client(base_url=api_url, timeout=30,
                                      headers={"X-Requested-With": "TravelMindAI"}) as client:
                        cleanup_remote_accounts(engine, users, client, cookies)
                else:
                    cleanup_accounts(engine, users, settings.document_upload_dir / "conversations")
                print("专用账号及会话已清理，真实调用费用保留。", flush=True)
            else:
                # 页面超时后服务可能仍在执行，不删除在途任务依赖的数据。
                print("验收失败，保留专用账号用于核查；确认任务结束后按编号清理：", flush=True)
                for user in users:
                    print(f"{user.id} {user.username}", flush=True)
        except Exception:
            print("清理未完成，保留未删除的数据，请按本次账号编号核查：", flush=True)
            for user in users:
                print(f"{user.id} {user.username}", flush=True)
            raise
        finally:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
