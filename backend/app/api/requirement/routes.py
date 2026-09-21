"""HTTP 接口层：接收聊天请求，调用需求服务并返回回复。"""

from collections.abc import Iterator
from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.config import Settings
from app.llm.contracts import ProviderName
from app.llm.gateway_client import GatewayClient
from app.llm.providers import provider_settings
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
        yield GatewayClient(settings, http, request.app.state.model_gateway,
                            selected_provider=payload.selected_provider)


"""网关统计接口函数：仅管理员查看进程内的安全统计，不返回凭据和用户文本。"""

@router.get("/gateway-status", dependencies=[Depends(require_admin)])
def gateway_status(request: Request) -> dict[str, object]:
    return {
        **request.app.state.model_gateway.snapshot(),
        "routes": request.app.state.settings.model_gateway.routes,
        "scope": "process_model_calls",
        "native_tool_calls_included": True,
        "request_id": request.state.request_id,
    }


"""模型状态接口函数：检查是否配置模型密钥。"""

@router.get("/status")
def model_status(request: Request) -> dict[str, object]:
    # 已配置不代表连接成功，这里不会实际调用模型。
    settings: Settings = request.app.state.settings
    configs = provider_settings(settings)
    candidates = [config for name, config in configs.items()
                  if config.api_key is not None and config.api_key.get_secret_value().strip()
                  and config.base_url and config.model]
    return {
        "configured": bool(candidates),
        "providers": [{"id": name, "name": label,
            "configured": bool((config := configs.get(cast(ProviderName, name))) and config.api_key
                and config.api_key.get_secret_value().strip() and config.base_url and config.model)}
            for name, label in (("deepseek", "DeepSeek"), ("kimi", "Kimi"), ("qwen", "Qwen"))],
        "model": "/".join(config.model or "" for config in candidates) or settings.deepseek_model,
        "request_id": request.state.request_id,
    }


"""聊天接口函数：处理用户消息，返回最新需求和回复文字。"""

@router.post("/messages", response_model=RequirementChatResponse)
def send_message(
    payload: RequirementMessage,
    request: Request,
    model: Annotated[ModelClient, Depends(get_requirement_model)],
) -> RequirementChatResponse:
    response = build_requirement_response(payload, model, request.state.request_id)
    response.selected_provider = payload.selected_provider
    if isinstance(model, GatewayClient):
        response.used_providers = model.gateway.used_providers
    return response
