"""
HTTP接口层：接收会话消息，准备连接资源并调用聊天业务。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Engine

from app.api.document.dependencies import get_search_service
from app.llm.client import DeepSeekClient
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.amap import AmapClient
from app.services.chat.service import process_saved_message
from app.services.requirement.extract import ModelClient
from app.services.requirement.history import RequirementHistoryService
from app.services.web_search import WebSearchClient

router = APIRouter(prefix="/sessions", tags=["旅行需求对话"])


"""历史服务获取函数：借用应用连接池；未配置时让接口明确报告错误。"""

def get_history_service(request: Request) -> RequirementHistoryService | None:
    engine: Engine | None = request.app.state.database_engine
    # 依赖阶段不抛503，让FastAPI先汇总请求体校验错误；接口执行时再检查配置。
    return RequirementHistoryService(engine) if engine is not None else None


"""模型依赖函数：先验证消息体再准备模型，请求结束后关闭连接。"""

def get_saved_model(payload: SavedRequirementMessage, request: Request) -> Iterator[ModelClient]:
    with httpx.Client() as http:
        yield DeepSeekClient(request.app.state.settings, http)


HistoryDependency = Annotated[RequirementHistoryService | None, Depends(get_history_service)]
ModelDependency = Annotated[ModelClient, Depends(get_saved_model)]


"""历史读取函数：只查数据库，恢复回答和引用不会调用模型。"""

@router.get("/{session_id}/requirement-messages", response_model=RequirementHistory)
def read_history(session_id: UUID, service: HistoryDependency) -> RequirementHistory:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    return service.read(session_id)


"""消息发送函数：基于历史处理需求和RAG，检查版本后保存回答与引用。"""

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
    with httpx.Client() as map_http:
        return process_saved_message(
            session_id, payload, model, service, request.state.request_id,
            contextmanager(get_search_service)(request),
            AmapClient(request.app.state.settings, map_http),
            WebSearchClient(request.app.state.settings, map_http),
        )
