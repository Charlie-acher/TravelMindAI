"""HTTP认证层：从受保护Cookie读取身份，集中校验角色、会话归属和写请求来源。"""

from collections import OrderedDict
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.trip import TravelSession
from app.services.auth import LOGIN_SECONDS, AuthService, normalize_username

COOKIE_NAME = "travelmind_session"
router = APIRouter(prefix="/auth", tags=["账号登录"])


class LoginRequest(BaseModel):
    """登录请求类：只接收用户名和密码，拒绝客户端传入角色。"""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=12)


class UserView(BaseModel):
    """账号响应类：仅返回页面需要的身份，不返回密码哈希和登录令牌。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    role: Literal["admin", "user"]


class RegisterRequest(BaseModel):
    """注册请求类：普通用户自助注册，角色由服务端固定。"""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=12)


class LoginLimiter:
    """登录限频类：单进程按客户端地址限制口令尝试，记录数量有上限。"""

    """初始化函数：创建时间窗口和线程锁，不在模块导入时启动后台服务。"""

    def __init__(self) -> None:
        self._attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = Lock()

    """限频检查函数：每个地址每分钟最多十次，全表满时拒绝新地址避免绕过限额。"""

    def check(self, address: str) -> None:
        # ponytail: 首版单进程限频；多worker部署前改为共享网关或数据库限频。
        now = monotonic()
        with self._lock:
            while self._attempts and next(iter(self._attempts.values()))[0] <= now - 60:
                self._attempts.popitem(last=False)
            started, count = self._attempts.get(address, (now, 0))
            if count >= 10 or (address not in self._attempts and len(self._attempts) >= 10000):
                raise HTTPException(429, "登录尝试过于频繁，请一分钟后重试")
            self._attempts[address] = (started, count + 1)


"""来源检查函数：浏览器写请求必须来自同源，并使用不可由跨站表单设置的请求头。"""

def check_write_source(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("X-Requested-With") != "TravelMindAI":
        raise HTTPException(403, "写请求缺少来源校验，请刷新页面后重试")
    origin = request.headers.get("Origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, "不允许跨站写入") from None
        expected = urlsplit(str(request.base_url))
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "不允许跨站写入")
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise HTTPException(403, "不允许跨站写入")


"""认证服务获取函数：按请求借用数据库连接池。"""

def get_auth_service(request: Request) -> AuthService:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    return AuthService(engine)


"""当前账号函数：先认证再检查来源，匿名请求统一返回401。"""

def get_current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "请先登录")
    user = get_auth_service(request).authenticate(token)
    if user is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    check_write_source(request)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


"""管理员检查函数：身份来自登录态，不能用请求参数提升权限。"""

def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可管理旅行资料")
    return user


"""会话归属检查函数：不存在、未认领和别人的会话统一不可见。"""

def require_session_owner(session_id: UUID, request: Request, user: CurrentUser) -> None:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, "未启用数据库，请配置后重启服务")
    with Session(engine) as unit:
        exists = unit.scalar(select(TravelSession.id).where(
            TravelSession.id == session_id, TravelSession.user_id == user.id,
        ))
    if exists is None:
        raise HTTPException(404, "旅行会话不存在")


"""登录接口函数：校验来源和频率，再签发仅供服务端读取的Cookie。"""

@router.post("/login", response_model=UserView)
def login(payload: LoginRequest, request: Request, response: Response) -> UserView:
    check_write_source(request)
    request.app.state.login_limiter.check(request.client.host if request.client else "unknown")
    service = get_auth_service(request)
    result = service.login(payload.username, payload.password)
    if result is None:
        raise HTTPException(401, "账号或密码错误，或账号已停用")
    user, token = result
    return set_login_cookie(user, token, request, response, service)


"""登录Cookie函数：登录和注册共用相同的令牌替换与浏览器保护规则。"""

def set_login_cookie(
    user: User, token: str, request: Request, response: Response, service: AuthService,
) -> UserView:
    # 再次登录时撤销这个浏览器原来的令牌，避免保留不再使用的会话。
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        service.logout(previous)
    response.set_cookie(
        COOKIE_NAME, token, max_age=LOGIN_SECONDS, httponly=True, samesite="strict",
        secure=(request.app.state.settings.environment == "production"
                or request.url.scheme == "https"),
        path="/api/v1",
    )
    response.headers["Cache-Control"] = "no-store"
    return UserView.model_validate(user)


"""注册接口函数：只创建普通用户，创建成功后按同一登录流程设置Cookie。"""

@router.post("/register", response_model=UserView, status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response) -> UserView:
    check_write_source(request)
    request.app.state.login_limiter.check(request.client.host if request.client else "unknown")
    try:
        username = normalize_username(payload.username)
        get_auth_service(request).create_user(username, payload.password)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except IntegrityError:
        raise HTTPException(409, "该账号名不可用，请换一个") from None
    service = get_auth_service(request)
    result = service.login(username, payload.password)
    if result is None:
        raise HTTPException(401, "账号已创建，请重新登录")
    return set_login_cookie(*result, request, response, service)


"""当前账号接口函数：让页面恢复身份与导航，不延长登录有效期。"""

@router.get("/me", response_model=UserView)
def me(user: CurrentUser, response: Response) -> UserView:
    response.headers["Cache-Control"] = "no-store"
    return UserView.model_validate(user)


"""退出接口函数：即使令牌已失效也可安全清除本浏览器Cookie。"""

@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    check_write_source(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        get_auth_service(request).logout(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/api/v1")
    response.headers["Cache-Control"] = "no-store"
    return response
