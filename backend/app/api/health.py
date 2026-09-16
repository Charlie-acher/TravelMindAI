"""HTTP接口层：检查应用存活，以及数据库和向量服务是否就绪。"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import error_response
from app.models.auth import AuthSession, User
from app.models.document import DocumentChunkRecord, DocumentRecord
from app.models.document_job import DocumentJobRecord
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession
from app.schemas.common import ErrorResponse
from app.services.document.vector_store import MilvusError, MilvusStore

router = APIRouter()


"""存活检查接口函数：确认应用能够处理请求。"""

@router.get("/health/live", tags=["健康检查"])
def live(request: Request) -> dict[str, str]:
    return {"status": "alive", "request_id": request.state.request_id}

"""就绪检查接口函数：检查已配置的数据库及所需表结构是否可用。"""

@router.get(
    "/health/ready",
    tags=["健康检查"],
    response_model=None,
    responses={503: {"model": ErrorResponse}},
)
def ready(request: Request) -> dict[str, object] | JSONResponse:
    settings = request.app.state.settings
    engine: Engine | None = request.app.state.database_engine
    database_status = "disabled"
    if engine is not None:
        try:
            with engine.connect() as connection:
                for model in [
                    TravelSession, TravelRequest, Itinerary, RequirementTurn, DocumentRecord,
                    DocumentChunkRecord, DocumentJobRecord, User, AuthSession,
                ]:
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
    milvus_status = "disabled"
    if settings.milvus_url is not None:
        try:
            with httpx.Client() as http:
                # 查询集合是否存在即可验证连接；尚未创建集合也算服务可用。
                MilvusStore(settings, http).exists()
            milvus_status = "ready"
        except MilvusError:
            return error_response(
                request, 503, "MILVUS_NOT_READY", "Milvus未就绪，请检查容器和配置",
                {"dependencies": {"postgresql": database_status, "milvus": "unavailable"}},
                retryable=True,
            )
    return {
        "status": "ready",
        "dependencies": {"postgresql": database_status, "milvus": milvus_status},
        "request_id": request.state.request_id,
    }

