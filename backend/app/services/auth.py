"""账号业务层：校验口令、签发随机登录令牌和查询有效账号，不接收客户端角色。"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.models.auth import AuthSession, User

PASSWORDS = PasswordHasher()
# 不存在的账号也执行口令校验，避免通过明显的耗时差直接枚举账号。
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$YWJjZGVmZ2hpamtsbW5vcA$" + "A" * 43
LOGIN_SECONDS = 7 * 24 * 60 * 60


"""登录名整理函数：统一大小写，只接受易辨认的账号字符。"""

def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("账号须为3到64位英文字母、数字、点、下划线或连字符")
    return value


class AuthService:
    """账号服务类：通过数据库保存账号和登录态，浏览器仅持有随机令牌。"""

    """初始化函数：借用应用连接池，每次操作独立管理事务。"""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    """账号创建函数：供注册接口和部署命令调用，公开注册仅由接口传入普通用户角色。"""

    def create_user(self, username: str, password: str, role: str = "user") -> User:
        username = normalize_username(username)
        if not 6 <= len(password) <= 12:
            raise ValueError("密码长度须为6到12个字符")
        if role not in {"admin", "user"}:
            raise ValueError("角色只能是admin或user")
        hashed = PASSWORDS.hash(password)
        with self._sessions.begin() as unit:
            user = User(username=username, password_hash=hashed, role=role)
            unit.add(user)
            unit.flush()
        return user

    """登录函数：密码正确且账号启用时签发新令牌，数据库不保存明文令牌。"""

    def login(self, username: str, password: str) -> tuple[User, str] | None:
        with self._sessions.begin() as unit:
            user = unit.scalar(select(User).where(User.username == username.strip().lower()))
            try:
                PASSWORDS.verify(user.password_hash if user else DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                return None
            if user is None or not user.is_active:
                return None
            if PASSWORDS.check_needs_rehash(user.password_hash):
                user.password_hash = PASSWORDS.hash(password)
                user.updated_at = func.now()
            token = secrets.token_urlsafe(32)
            unit.execute(delete(AuthSession).where(AuthSession.expires_at <= func.now()))
            unit.add(AuthSession(
                user_id=user.id, token_hash=sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(seconds=LOGIN_SECONDS),
            ))
        return user, token

    """身份查询函数：有效期、启用状态和令牌哈希必须同时匹配。"""

    def authenticate(self, token: str) -> User | None:
        if len(token) != 43:
            return None
        with self._sessions() as unit:
            return unit.scalar(select(User).join(AuthSession).where(
                AuthSession.token_hash == sha256(token.encode()).hexdigest(),
                AuthSession.expires_at > func.now(), User.is_active.is_(True),
            ))

    """退出函数：撤销当前浏览器令牌，其他设备登录态保持不变。"""

    def logout(self, token: str) -> None:
        with self._sessions.begin() as unit:
            unit.execute(delete(AuthSession).where(
                AuthSession.token_hash == sha256(token.encode()).hexdigest(),
            ))

    """历史认领函数：部署人员明确指定会话，已归属的记录不得转给另一账号。"""

    def claim_sessions(self, user_id: UUID, identifiers: list[UUID]) -> int:
        from app.models.trip import TravelSession

        if not identifiers:
            raise ValueError("请明确提供需要认领的会话编号")
        with self._sessions.begin() as unit:
            if unit.get(User, user_id) is None:
                raise ValueError("账号不存在")
            rows = unit.scalars(select(TravelSession).where(
                TravelSession.id.in_(identifiers),
            ).order_by(TravelSession.id).with_for_update()).all()
            if len(rows) != len(set(identifiers)):
                raise ValueError("包含不存在的会话；没有修改任何归属")
            if any(row.user_id not in {None, user_id} for row in rows):
                raise ValueError("包含已属于其他账号的会话；没有修改任何归属")
            for row in rows:
                row.user_id = user_id
        return len(rows)
