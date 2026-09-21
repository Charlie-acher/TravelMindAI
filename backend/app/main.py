"""
应用入口层：创建FastAPI应用，连接各接口和服务，管理数据库生命周期并注册统一错误处理。
"""

from asyncio import Semaphore, gather
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.attachments import router as attachments_router
from app.api.auth import LoginLimiter, get_current_user, require_admin
from app.api.auth import router as auth_router
from app.api.budget import router as budget_router
from app.api.document.jobs import router as document_jobs_router
from app.api.document.routes import router as documents_router
from app.api.document.search import router as document_search_router
from app.api.document.upload_limits import DocumentUploadLimitMiddleware
from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.requirement.history import router as requirement_history_router
from app.api.requirement.routes import router as requirements_router
from app.api.trips import router as sessions_router
from app.config import Settings, load_settings
from app.database import create_database_engine
from app.llm.gateway import GatewayState
from app.schemas.common import ErrorResponse
from app.services.document.jobs import DocumentJobService
from app.services.knowledge_scope import KNOWLEDGE_SCOPE
from app.services.trip_service import TripService

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
        application.state.trip_service = TripService(
            engine, attachment_dir=settings.document_upload_dir / "conversations",
        ) if engine is not None else None
        application.state.document_jobs_recovered = False
        # 等待的后台任务不占线程或数据库连接，单进程最多同时处理两份资料。
        application.state.document_job_slots = Semaphore(2)
        application.state.document_job_recovery_lock = Lock()
        application.state.document_job_failures = set()
        try:
            if engine is not None:
                # 只把上次中断的任务标为可重试；启动服务不会自动产生模型费用。
                try:
                    await run_in_threadpool(
                        DocumentJobService(engine, KNOWLEDGE_SCOPE).recover_interrupted,
                    )
                    application.state.document_jobs_recovered = True
                except SQLAlchemyError:
                    # 数据库不可用时仍提供存活检查，由就绪接口报告连接/迁移问题。
                    pass
            # 启动准备完成，应用开始处理请求。
            yield
        finally:
            # 已断开浏览器的流式任务仍可能保存；先等其连接退出，再释放共享数据库。
            if application.state.chat_workers:
                await gather(*tuple(application.state.chat_workers))
            # 应用关闭后清理数据库资源。
            application.state.trip_service = None  # 清除存储实例
            application.state.database_engine = None  # 清除数据库引擎
            # 释放连接池。
            if engine is not None:
                await run_in_threadpool(engine.dispose)  # 在线程池中执行引擎的清理方法

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="旅行需求、资料问答和逐日行程草稿；预算采用演示单价，交通与开放时间待核实。",
        lifespan=lifespan,
        # 注册错误文档，避免实际返回统一错误，Swagger 却显示默认422格式。
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    # 配置只保存在后端进程；对话路由通过依赖取得配置，浏览器不会收到密钥。
    app.state.settings = settings
    app.state.model_gateway = GatewayState(settings.model_gateway)
    app.state.chat_workers = set()
    app.state.chat_cancellations = {}
    app.state.login_limiter = LoginLimiter()
    # 中间件后注册的先执行：先生成请求编号，再检查上传大小，报错时也能查到编号。
    app.add_middleware(DocumentUploadLimitMiddleware)

    """请求编号中间件函数：为每次请求添加编号，方便排查问题。"""

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 为每个请求生成唯一的请求ID并存储在请求状态中
        request.state.request_id = str(uuid4())
        # 将请求传递给下一个中间件或路由处理程序
        response = await call_next(request)
        # 在响应头中添加请求ID，便于追踪和调试
        response.headers["X-Request-ID"] = request.state.request_id
        # 登录与私人历史不得留在浏览器或代理共享缓存中。
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    register_error_handlers(app)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")

    # /api/v1 与 router 的 /budget、函数的 /estimate 拼成完整接口路径。
    app.include_router(budget_router, prefix="/api/v1")
    for router in (sessions_router, requirements_router, requirement_history_router,
                   attachments_router):
        app.include_router(router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    # 注册资料接口，使用应用已有的数据库连接和统一错误格式。
    for prefix in ("/api/v1", "/api/v1/admin"):
        for router in (documents_router, document_search_router, document_jobs_router):
            app.include_router(router, prefix=prefix, dependencies=[Depends(require_admin)])
    return app
