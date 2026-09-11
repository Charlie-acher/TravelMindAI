"""HTTP 接口层：接收聊天请求，调用需求服务并返回回复。"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Request

from app.config import Settings
from app.llm.client import DeepSeekClient
from app.schemas.requirement_chat import RequirementChatResponse, RequirementMessage
from app.services.requirement_merge import LIST_FIELDS, SCALAR_FIELDS
from app.services.requirement_service import ModelClient, extract_requirements

router = APIRouter(prefix="/requirements", tags=["旅行需求对话"])


"""模型依赖函数：为当前请求创建模型客户端，并在结束后关闭连接。"""

def get_requirement_model(payload: RequirementMessage, request: Request) -> Iterator[ModelClient]:
    # 依赖也声明同一个payload，让FastAPI先完成请求校验，再检查模型配置。
    # 否则无密钥时503可能遮住空消息本应得到的422。
    settings: Settings = request.app.state.settings
    with httpx.Client() as http:
        yield DeepSeekClient(settings, http)


"""模型状态接口函数：检查是否配置模型密钥。"""

@router.get("/status")
def model_status(request: Request) -> dict[str, object]:
    # 已配置不代表连接成功，这里不会实际调用模型。
    settings: Settings = request.app.state.settings
    key = settings.deepseek_api_key
    return {
        "configured": key is not None and bool(key.get_secret_value().strip()),
        "model": settings.deepseek_model,
        "request_id": request.state.request_id,
    }


"""聊天接口函数：处理用户消息，返回最新需求和回复文字。"""

@router.post("/messages", response_model=RequirementChatResponse)
def send_message(
    payload: RequirementMessage,
    request: Request,
    model: Annotated[ModelClient, Depends(get_requirement_model)],
) -> RequirementChatResponse:
    today = datetime.now(timezone(timedelta(hours=8))).date()
    result = extract_requirements(
        payload.message,
        model,
        reference_date=payload.reference_date or today,
        previous=payload.previous,
    )
    changed = [
        field
        for field in (*SCALAR_FIELDS, *LIST_FIELDS)
        if getattr(result.extraction, field)
        != (
            getattr(payload.previous, field)
            if payload.previous is not None
            else ([] if field in LIST_FIELDS else None)
        )
    ]
    status: Literal["needs_clarification", "complete", "unsupported"]
    if result.message_intent not in {"plan_trip", "modify_trip"}:
        status = "unsupported"
        reply = "当前页面用于整理和修改旅行需求。你可以告诉我目的地、时间、人数和预算。"
        changed = []
    elif result.clarification:
        status = "needs_clarification"
        reply = result.clarification
    else:
        status = "complete"
        reply = "旅行需求已整理完整。你可以继续修改条件，右侧会显示最新需求。"
    return RequirementChatResponse(
        result=result,
        reply=reply,
        status=status,
        changed_fields=changed,
        request_id=request.state.request_id,
    )
