"""FastAPI 应用工厂：集中组装接口，相当于后端应用的启动装配点。

启动命令：uvicorn travelmind.main:create_app --factory
--factory 让服务器调用 create_app() 获得应用；仅 import 本文件不会启动应用。
M1-A 没有数据库/模型连接，所以不需要额外的连接初始化或 lifespan 清理。
"""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from travelmind.api.v1.budget import router as budget_router
from travelmind.domain.budget import BudgetValidationError
from travelmind.schemas import ErrorResponse
from travelmind.settings import load_settings


def create_app() -> FastAPI:
    """每次调用创建独立应用，测试和 Uvicorn 共用这一装配入口。"""
    settings = load_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="M1-A 确定性预算 API：固定演示价格，不调用模型。",
        # 注册错误文档，避免实际返回统一错误，Swagger 却显示默认422格式。
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """中间件在路由前后执行，类似 Servlet Filter。

        UUID 是服务端生成的请求编号；同一请求的响应体和响应头使用同一个值。
        call_next 把请求继续交给路由，await 等待它处理完成后再加响应头。
        """
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(
        request: Request,
        status: int,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> JSONResponse:
        """几个异常处理器共用一个错误结构，前端无需分别处理三套JSON。"""
        request_id = request.state.request_id
        return JSONResponse(
            status_code=status,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "details": details or {},
                },
                "request_id": request_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic/JSON校验失败返回422；只提取字段路径和说明，不回显用户原文。"""
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "请求参数校验失败", {"fields": fields}
        )

    @app.exception_handler(BudgetValidationError)
    async def invalid_budget(request: Request, exc: BudgetValidationError) -> JSONResponse:
        """领域拒绝返回400；领域异常本身不依赖 HTTP，转换只发生在这一层。"""
        return error_response(request, 400, "BUDGET_INVALID", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        """404/405等框架错误也统一格式，同时保留 Allow 等协议要求的响应头。"""
        response = error_response(
            request, exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail)
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.get("/api/v1/health/live", tags=["健康检查"])
    def live(request: Request) -> dict[str, str]:
        """存活检查：应用能处理请求即可，不发送任何外部请求。"""
        return {"status": "alive", "request_id": request.state.request_id}

    @app.get("/api/v1/health/ready", tags=["健康检查"])
    def ready(request: Request) -> dict[str, object]:
        """就绪检查：当前配置已加载且预算不依赖外部服务，所以依赖列表为空。

        后续引入数据库/Milvus后，需要在对应阶段扩展检查，不能沿用此结论。
        """
        return {"status": "ready", "dependencies": {}, "request_id": request.state.request_id}

    # /api/v1 与 router 的 /budget、函数的 /estimate 拼成完整接口路径。
    app.include_router(budget_router, prefix="/api/v1")
    return app
