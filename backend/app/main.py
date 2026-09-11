"""
应用入口：创建应用、注册接口、管理数据库连接并统一错误响应。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.budget import router as budget_router
from app.api.requirement_history import router as requirement_history_router
from app.api.requirements import router as requirements_router
from app.api.trips import router as sessions_router
from app.config import Settings, load_settings
from app.database import create_database_engine
from app.llm.client import ModelClientError
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession
from app.schemas.common import ErrorResponse
from app.services.budget_service import BudgetValidationError
from app.services.requirement_history import HistoryConflictError
from app.services.requirement_service import RequirementExtractionError
from app.services.trip_service import SessionNotFoundError, TripService

"""应用创建函数：加载配置，注册接口和错误处理。"""

def create_app(settings: Settings | None = None) -> FastAPI:
    # 测试可显式传入配置；Uvicorn工厂调用无参数，读取环境变量，仍不自动寻找.env。
    settings = settings if settings is not None else load_settings()

    """生命周期函数：启动时准备数据库资源，关闭时释放资源。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # 根据配置创建数据库引擎，如果配置了数据库URL
        engine = create_database_engine(settings) if settings.database_url is not None else None
        # 将数据库引擎存储到应用程序状态中
        application.state.database_engine = engine
        # 创建TripService实例，如果引擎存在
        application.state.trip_service = TripService(engine) if engine is not None else None
        try:
            # 启动准备完成，应用开始处理请求。
            yield
        finally:

            # 应用关闭后清理数据库资源。
            application.state.trip_service = None  # 清除存储实例
            application.state.database_engine = None  # 清除数据库引擎
            # 释放连接池。
            if engine is not None:
                await run_in_threadpool(engine.dispose)  # 在线程池中执行引擎的清理方法

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="预算估算、会话和预算草稿存储：固定演示价格，尚未生成逐日行程。",
        lifespan=lifespan,
        # 注册错误文档，避免实际返回统一错误，Swagger 却显示默认422格式。
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    # 配置只保存在后端进程；对话路由通过依赖取得配置，浏览器不会收到密钥。
    app.state.settings = settings

    """请求编号中间件：为每次请求添加编号，方便排查问题。"""

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 为每个请求生成唯一的请求ID并存储在请求状态中
        request.state.request_id = str(uuid4())
        # 将请求传递给下一个中间件或路由处理程序
        response = await call_next(request)
        # 在响应头中添加请求ID，便于追踪和调试
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    """错误响应函数：将错误原因和请求编号整理成统一格式。"""

    def error_response(
        request: Request,
        status: int,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            status_code=status,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "details": details or {},
                },
                "request_id": request_id,
            },
        )

    """模型异常处理函数：返回模型不可用的错误。"""

    @app.exception_handler(ModelClientError)
    async def model_unavailable(request: Request, exc: ModelClientError) -> JSONResponse:
        return error_response(request, 503, "MODEL_UNAVAILABLE", str(exc), retryable=True)

    """提取异常处理函数：返回旅行需求校验失败的错误。"""

    @app.exception_handler(RequirementExtractionError)
    async def extraction_failed(request: Request, exc: RequirementExtractionError) -> JSONResponse:
        return error_response(request, 502, "REQUIREMENT_EXTRACTION_FAILED", str(exc))

    """请求校验异常处理函数：返回参数错误及对应字段。"""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "请求参数校验失败", {"fields": fields}
        )

    """预算异常处理函数：返回不符合预算规则的原因。"""

    @app.exception_handler(BudgetValidationError)
    async def invalid_budget(request: Request, exc: BudgetValidationError) -> JSONResponse:
        return error_response(request, 400, "BUDGET_INVALID", str(exc))

    """会话异常处理函数：返回旅行会话不存在的错误。"""

    @app.exception_handler(SessionNotFoundError)
    async def missing_session(request: Request, exc: SessionNotFoundError) -> JSONResponse:
        return error_response(request, 404, "SESSION_NOT_FOUND", "旅行会话不存在")

    """对话版本冲突：提示先恢复服务端历史，不能用旧需求静默覆盖新结果。"""

    @app.exception_handler(HistoryConflictError)
    async def history_conflict(request: Request, exc: HistoryConflictError) -> JSONResponse:
        return error_response(request, 409, "HISTORY_CONFLICT", str(exc))

    """数据库连接异常处理函数：返回数据库暂不可用的错误。"""

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        return error_response(
            request, 503, "DATABASE_UNAVAILABLE", "数据库暂不可用，请稍后重试", retryable=True
        )

    """数据冲突处理函数：返回保存数据发生冲突的错误。"""

    @app.exception_handler(IntegrityError)
    async def storage_conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        return error_response(
            request, 409, "STORAGE_CONFLICT", "保存发生数据冲突，请重新读取后再操作"
        )

    """数据库结构异常处理函数：处理缺表、缺字段等数据库错误。"""

    @app.exception_handler(ProgrammingError)
    async def database_structure_error(request: Request, exc: ProgrammingError) -> JSONResponse:
        if getattr(exc.orig, "sqlstate", None) in {"42P01", "42703"}:
            return error_response(
                request, 503, "DATABASE_NOT_READY", "数据库表结构未就绪，请检查迁移"
            )
        return error_response(request, 500, "DATABASE_ERROR", "数据库操作失败")

    """数据库异常处理函数：统一返回其他数据库操作错误。"""

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error_response(request, 500, "DATABASE_ERROR", "数据库操作失败")

    """HTTP 异常处理函数：将接口错误整理成统一响应。"""

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(
            request, exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail)
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    """存活检查接口函数：确认应用能够处理请求。"""

    @app.get("/api/v1/health/live", tags=["健康检查"])
    def live(request: Request) -> dict[str, str]:
        return {"status": "alive", "request_id": request.state.request_id}

    """就绪检查接口函数：检查已配置的数据库及所需表结构是否可用。"""

    @app.get(
        "/api/v1/health/ready",
        tags=["健康检查"],
        response_model=None,
        responses={503: {"model": ErrorResponse}},
    )
    def ready(request: Request) -> dict[str, object] | JSONResponse:
        engine: Engine | None = request.app.state.database_engine
        database_status = "disabled"
        if engine is not None:
            try:
                with engine.connect() as connection:
                    for model in [TravelSession, TravelRequest, Itinerary, RequirementTurn]:
                        # LIMIT 0 校验表与映射字段存在，但不返回任何会话或草稿记录。
                        connection.execute(select(model).limit(0))
                database_status = "ready"
            except SQLAlchemyError:
                return error_response(
                    request,
                    503,
                    "DATABASE_NOT_READY",
                    "数据库未就绪，请检查连接和迁移",
                    {"dependencies": {"postgresql": "unavailable"}},
                    retryable=True,
                )
        return {
            "status": "ready",
            "dependencies": {"postgresql": database_status},
            "request_id": request.state.request_id,
        }

    # /api/v1 与 router 的 /budget、函数的 /estimate 拼成完整接口路径。
    app.include_router(budget_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(requirements_router, prefix="/api/v1")
    app.include_router(requirement_history_router, prefix="/api/v1")
    return app
