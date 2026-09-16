"""HTTP 接口层：接收聊天请求，调用需求服务并返回回复。"""

from collections.abc import Iterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request

from app.config import Settings
from app.llm.client import DeepSeekClient
from app.schemas.requirement.chat import RequirementChatResponse, RequirementMessage
from app.services.chat.service import build_requirement_response
from app.services.requirement.extract import ModelClient

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
    return build_requirement_response(payload, model, request.state.request_id)
