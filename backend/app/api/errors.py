"""HTTP接口层：将业务异常转换成统一错误响应，保留请求编号。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from starlette.exceptions import HTTPException

from app.llm.budget import ModelInputLimitError
from app.llm.client import ModelClientError
from app.llm.embeddings import EmbeddingError
from app.services.budget_service import BudgetValidationError
from app.services.document.vector_store import MilvusError
from app.services.requirement.extract import RequirementExtractionError
from app.services.requirement.history import HistoryConflictError
from app.services.trip_service import SessionNotFoundError

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

"""错误注册函数：把每类异常与对应的HTTP处理函数连接起来。"""


def register_error_handlers(app: FastAPI) -> None:
    """上下文超限处理函数：不截断必要旅行条件，保留原历史并明确本轮未完成。"""

    @app.exception_handler(ModelInputLimitError)
    async def input_too_large(request: Request, exc: ModelInputLimitError) -> JSONResponse:
        return error_response(request, 413, "CONTEXT_TOO_LARGE",
                              "本轮待处理内容过长，已保存的对话保持不变。请缩小这次查询范围。")

    """模型异常处理函数：返回模型不可用的错误。"""

    @app.exception_handler(ModelClientError)
    async def model_unavailable(request: Request, exc: ModelClientError) -> JSONResponse:
        return error_response(request, 503, "MODEL_UNAVAILABLE", str(exc), retryable=True)

    """向量服务异常处理函数：明确返回未完成操作，页面保留已有进度供重试。"""

    @app.exception_handler(EmbeddingError)
    @app.exception_handler(MilvusError)
    async def vector_unavailable(
        request: Request, exc: EmbeddingError | MilvusError,
    ) -> JSONResponse:
        return error_response(request, 503, "VECTOR_UNAVAILABLE", str(exc), retryable=True)

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

    """对话冲突处理函数：提示重新读取最新对话，避免旧内容覆盖新结果。"""

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

