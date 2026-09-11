"""
HTTP 接口层：提供旅行会话和草稿的创建、保存与查询接口。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.schemas.budget import BudgetSummary
from app.schemas.common import ErrorResponse
from app.schemas.trip import (
    DraftCreate,
    DraftResponse,
    DraftView,
    SessionCreate,
    SessionResponse,
    SessionView,
)
from app.services.budget_service import calculate_budget
from app.services.trip_service import TripService

router = APIRouter(
    prefix="/sessions",
    tags=["会话与草稿"],
    responses={status: {"model": ErrorResponse} for status in [404, 409, 500, 503]},
)

"""存储依赖函数：获取旅行存储服务，未配置数据库时返回错误。"""

def get_trip_service(request: Request) -> TripService:
    # getattr 按属性名获取对象属性
    service: TripService | None = getattr(request.app.state, "trip_service", None)
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    return service


"""存储依赖类型：让接口自动取得旅行存储服务。"""
TripServiceDependency = Annotated[TripService, Depends(get_trip_service)]


"""会话创建接口函数：创建旅行会话并返回会话信息。"""

@router.post("", response_model=SessionResponse, status_code=201, summary="创建旅行会话")
def create_session(
    body: SessionCreate, request: Request, service: TripServiceDependency
) -> SessionResponse:
    trip = service.create_session(body.title)
    return SessionResponse(
        session=SessionView.model_validate(trip), request_id=request.state.request_id
    )


"""会话查询接口函数：按编号返回旅行会话信息。"""

@router.get("/{session_id}", response_model=SessionResponse, summary="读取旅行会话")
def get_session(
    session_id: UUID, request: Request, service: TripServiceDependency
) -> SessionResponse:
    # 从存储中获取指定session_id的旅行会话
    trip = service.get_session(session_id)
    # 如果获取到的旅行会话不存在，则抛出404异常
    if trip is None:
        raise HTTPException(404, "旅行会话不存在")
    # 返回SessionResponse对象，包含会话视图和请求ID
    return SessionResponse(
        session=SessionView.model_validate(trip), request_id=request.state.request_id
    )


"""草稿保存接口函数：计算预算并保存旅行需求和草稿。"""

@router.post(
    "/{session_id}/drafts",
    response_model=DraftResponse, # 指定响应结构
    status_code=201,
    summary="计算预算并保存草稿",
)
def save_draft(
    session_id: UUID,  # 会话ID，用于标识特定的会话
    body: DraftCreate,  # 请求体，包含创建草稿所需的数据
    request: Request,  # 请求对象，包含请求相关信息
    service: TripServiceDependency,  # 存储依赖，用于数据持久化
) -> DraftResponse:  # 返回草稿响应对象
    # 从请求体中获取需求信息
    inputs = body.requirements
    # 计算预算
    estimate = calculate_budget(
        days=inputs.days,  # 旅行天数
        travelers=inputs.travelers,  # 旅行人数
        total_budget=inputs.total_budget,  # 总预算
        lodging=inputs.lodging,  # 住宿信息
    )
    # 保存草稿到存储
    saved = service.save_draft(
        session_id,  # 会话ID
        request_json=inputs.model_dump(mode="json"),  # 请求数据的JSON表示
        itinerary_json={          # 行程数据的JSON表示
            "title": body.title,  # 行程标题
            "notes": body.notes,  # 行程备注
            "days": [],           # 尚未生成逐日活动；这份数据只是预算草稿。
            "budget": BudgetSummary.model_validate(estimate).model_dump(mode="json"),
        },
    )
    return DraftResponse(draft=DraftView.model_validate(saved), request_id=request.state.request_id)


"""草稿查询接口函数：返回最新或指定版本的草稿。"""

@router.get(
    "/{session_id}/drafts",  # 路由路径，用于获取指定会话的草稿
    response_model=DraftResponse,  # 响应模型，定义返回数据的结构
    summary="读取最新或历史草稿"  # 接口功能简述
)
def get_draft(
    session_id: UUID,  # 会话ID，用于标识特定的会话
    request: Request,  # 请求对象，包含请求相关信息
    service: TripServiceDependency,  # 存储依赖，用于数据访问
    version: Annotated[int | None, Query(ge=1, description="行程版本，省略时返回最新")] = None,  # 可选的版本参数，指定要获取的草稿版本
) -> DraftResponse:
    saved = service.get_draft(session_id, version=version)  # 从存储中获取指定会话和版本的草稿
    if saved is None:  # 检查是否找到草稿
        raise HTTPException(404, "没有找到该会话的对应草稿版本")
    return DraftResponse(draft=DraftView.model_validate(saved), request_id=request.state.request_id)  # 返回草稿数据和请求ID
