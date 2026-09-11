"""
持久化对话入口：读历史 → 抽取合并 → 提交整轮，成功保存后才返回成功。
"""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Engine

from app.api.requirements import send_message
from app.llm.client import DeepSeekClient
from app.schemas.requirement_chat import RequirementMessage
from app.schemas.requirement_history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.requirement_history import HistoryConflictError, RequirementHistoryService
from app.services.requirement_service import ModelClient

router = APIRouter(prefix="/sessions", tags=["旅行需求对话"])


"""读取应用管理的连接池；数据库未配置时给出明确说明，不退回临时聊天。"""

def get_history_service(request: Request) -> RequirementHistoryService | None:
    engine: Engine | None = request.app.state.database_engine
    # 依赖阶段不抛503，让FastAPI先汇总请求体校验错误；接口执行时再检查配置。
    return RequirementHistoryService(engine) if engine is not None else None


"""先验证同一份消息体，再准备模型；依赖声明顺序保证422不被配置错误遮住。"""

def get_saved_model(payload: SavedRequirementMessage, request: Request) -> Iterator[ModelClient]:
    with httpx.Client() as http:
        yield DeepSeekClient(request.app.state.settings, http)


HistoryDependency = Annotated[RequirementHistoryService | None, Depends(get_history_service)]
ModelDependency = Annotated[ModelClient, Depends(get_saved_model)]


"""读取完整对话：只访问数据库，恢复页面不消耗模型调用。"""

@router.get("/{session_id}/requirement-messages", response_model=RequirementHistory)
def read_history(session_id: UUID, service: HistoryDependency) -> RequirementHistory:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    return service.read(session_id)


"""基于服务端历史发送；短事务检查版本，避免慢模型答案覆盖其他请求的新需求。"""

@router.post("/{session_id}/requirement-messages", response_model=SavedRequirementTurn)
def send_saved_message(
    session_id: UUID,
    payload: SavedRequirementMessage,
    request: Request,
    model: ModelDependency,
    service: HistoryDependency,
) -> SavedRequirementTurn:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    history = service.read(session_id)
    # 已提交但浏览器没收到响应时，重试直接返回原结果，不再次调用模型。
    for turn in history.turns:
        if turn.message_id == payload.message_id:
            if (
                turn.response.result.original_message != payload.message
                or turn.revision != payload.expected_revision + 1
            ):
                raise HistoryConflictError("消息编号已用于其他提交，请重新读取会话")
            return turn
    if history.revision != payload.expected_revision:
        raise HistoryConflictError("会话已有新消息，请先恢复最新对话再发送")
    # 不支持的闲聊也保存文字，但上下文取最近一次有效的旅行需求。
    previous = next(
        (
            turn.response.result.extraction
            for turn in reversed(history.turns)
            if turn.response.status != "unsupported"
        ),
        None,
    )
    reference_date = history.turns[0].response.result.reference_date if history.turns else None
    # 复用原对话入口的普通Python函数，保留同样的抽取、追问和变化字段规则。
    response = send_message(
        RequirementMessage(
            message=payload.message, previous=previous, reference_date=reference_date
        ),
        request,
        model,
    )
    # 等待模型时没有持有数据库事务；这里再锁行检查，处理两个页面同时发消息。
    return service.append(session_id, payload.message_id, payload.expected_revision, response)
