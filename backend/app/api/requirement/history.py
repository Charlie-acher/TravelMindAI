"""
HTTP接口层：接收会话消息，准备连接资源并调用聊天业务。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Engine

from app.api.auth import require_session_owner
from app.api.document.dependencies import get_search_service
from app.llm.client import DeepSeekClient
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.amap import AmapClient
from app.services.chat.events import event_sink
from app.services.chat.service import process_saved_message
from app.services.requirement.extract import ModelClient
from app.services.requirement.history import RequirementHistoryService
from app.services.web_search import WebSearchClient

router = APIRouter(prefix="/sessions", tags=["旅行需求对话"],
                   dependencies=[Depends(require_session_owner)])


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


"""流式发送函数：鉴权后立即返回 SSE，工作线程独立持有模型和检索连接。"""

@router.post("/{session_id}/requirement-messages/stream")
async def stream_saved_message(
    session_id: UUID, payload: SavedRequirementMessage, request: Request,
    service: HistoryDependency,
) -> StreamingResponse:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    connected = True

    """入队函数：断开的浏览器不再积压草稿；线程只通过事件循环操作异步队列。"""

    def enqueue(event: str, data: object) -> None:
        if connected:
            queue.put_nowait((event, data))

    """线程通知函数：把事件交回当前请求的事件循环，不在工作线程修改队列。"""

    def notify(event: str, data: dict[str, object]) -> None:
        loop.call_soon_threadsafe(enqueue, event, data)

    """工作线程函数：沿用版本和幂等校验，任何失败均不发送完成事件。"""

    def run() -> None:
        token = event_sink.set(notify)
        try:
            with httpx.Client() as http:
                settings = request.app.state.settings
                turn = process_saved_message(
                    session_id, payload, DeepSeekClient(settings, http), service,
                    request.state.request_id, contextmanager(get_search_service)(request),
                    AmapClient(settings, http), WebSearchClient(settings, http),
                )
                loop.call_soon_threadsafe(enqueue, "done", turn.model_dump(mode="json"))
        except Exception as error:
            # 不把底层异常直接序列化，继续使用应用已定义的脱敏错误处理器。
            loop.call_soon_threadsafe(enqueue, "failure", error)
        finally:
            event_sink.reset(token)

    """事件输出函数：心跳维持连接，错误事件保留原状态码供前端处理冲突。"""

    async def events() -> AsyncIterator[str]:
        nonlocal connected
        yield 'event: progress\ndata: {"stage":"received","message":"已收到问题，正在准备回答"}\n\n'
        worker = asyncio.create_task(asyncio.to_thread(run))
        # 任务属于本应用；停服时先等它们保存和关闭连接，再释放数据库。
        workers: set[asyncio.Task[None]] = request.app.state.chat_workers
        workers.add(worker)
        worker.add_done_callback(workers.discard)
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == "failure":
                    status, message = 500, "本次回答未完成，请重新读取对话或使用原消息重试。"
                    for kind in type(data).__mro__:
                        handler = request.app.exception_handlers.get(kind)
                        if handler is not None:
                            result = await handler(request, data)
                            status = result.status_code
                            message = json.loads(result.body)["error"]["message"]
                            break
                    event, data = "error", {"status": status, "message": message,
                                            "request_id": request.state.request_id}
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if event in {"done", "error"}:
                    break
        finally:
            connected = False

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
