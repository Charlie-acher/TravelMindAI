"""
HTTP接口层：接收会话消息，准备连接资源并调用聊天业务。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from threading import Event
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Engine

from app.api.attachments import get_attachment_service
from app.api.auth import require_session_owner
from app.api.document.dependencies import get_search_service
from app.llm.gateway_client import GatewayClient
from app.llm.vision import QwenVisionClient
from app.schemas.itinerary import UndoDraftRequest
from app.schemas.requirement.history import (
    RequirementHistory,
    SavedRequirementMessage,
    SavedRequirementTurn,
)
from app.services.attachment.mineru import MinerUClient
from app.services.attachment.reader import AttachmentReader
from app.services.baidu import BaiduMaps
from app.services.chat.events import ChatCancelled, event_sink, request_cancelled
from app.services.chat.workflow import run_saved_workflow
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
        yield GatewayClient(request.app.state.settings, http, request.app.state.model_gateway,
                            selected_provider=payload.selected_provider)


HistoryDependency = Annotated[RequirementHistoryService | None, Depends(get_history_service)]
ModelDependency = Annotated[ModelClient, Depends(get_saved_model)]


"""停止接口函数：仅标记当前账号该消息的执行，在调用边界停止，状态由查询接口确认。"""

@router.post("/{session_id}/requirement-messages/{message_id}/stop")
async def stop_message(session_id: UUID, message_id: UUID, request: Request) -> dict[str, bool]:
    signals = request.app.state.chat_cancellations.get((session_id, message_id), set())
    for signal in signals:
        signal.set()
    return {"running": bool(signals)}


"""运行状态接口函数：供停止按钮确认本进程旧执行已释放资源，不调用模型。"""

@router.get("/{session_id}/requirement-messages/{message_id}/execution")
async def message_execution(
    session_id: UUID, message_id: UUID, request: Request,
) -> dict[str, bool]:
    return {"running": bool(request.app.state.chat_cancellations.get((session_id, message_id)))}


"""历史读取函数：只查数据库，恢复回答和引用不会调用模型。"""

@router.get("/{session_id}/requirement-messages", response_model=RequirementHistory)
def read_history(session_id: UUID, service: HistoryDependency) -> RequirementHistory:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    return service.read(session_id)


"""草稿撤销函数：沿用会话归属和跨站保护，恢复上版完整需求及行程。"""

@router.post("/{session_id}/drafts/undo", response_model=SavedRequirementTurn)
def undo_draft(
    session_id: UUID, payload: UndoDraftRequest, request: Request, service: HistoryDependency,
) -> SavedRequirementTurn:
    if service is None:
        raise HTTPException(503, "未启用数据库，请配置 TRAVELMIND_DATABASE_URL 后重启服务")
    return service.undo(session_id, payload, request.state.request_id)


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
    with httpx.Client() as map_http, BaiduMaps(request.app.state.settings) as maps:
        return run_saved_workflow(
            session_id, payload, model, service, request.state.request_id,
            lambda: contextmanager(get_search_service)(request),
            maps,
            WebSearchClient(request.app.state.settings, map_http),
            attachments=AttachmentReader(get_attachment_service(request),
                GatewayClient(request.app.state.settings, map_http, request.app.state.model_gateway,
                              max_output_tokens=8192, selected_provider=payload.selected_provider,
                              selection=model.gateway.selection
                                  if isinstance(model, GatewayClient) else None)
                    if request.app.state.settings.mineru_base_url else model,
                lambda: QwenVisionClient(request.app.state.settings, map_http),
                MinerUClient(str(request.app.state.settings.mineru_base_url), map_http,
                    request.app.state.settings.mineru_timeout_seconds)
                    if request.app.state.settings.mineru_base_url else None)
                if payload.attachment_ids else None,
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
    cancelled = Event()

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
        cancellation_token = request_cancelled.set(cancelled)
        try:
            settings = request.app.state.settings
            with httpx.Client() as http, BaiduMaps(settings) as maps:
                model = GatewayClient(settings, http, request.app.state.model_gateway,
                                      selected_provider=payload.selected_provider)
                turn = run_saved_workflow(
                    session_id, payload, model, service,
                    request.state.request_id, lambda: contextmanager(get_search_service)(request),
                    maps, WebSearchClient(settings, http),
                    attachments=AttachmentReader(get_attachment_service(request),
                        GatewayClient(settings, http, request.app.state.model_gateway,
                                      max_output_tokens=8192, selection=model.gateway.selection)
                            if settings.mineru_base_url else model,
                        lambda: QwenVisionClient(settings, http),
                        MinerUClient(str(settings.mineru_base_url), http,
                            settings.mineru_timeout_seconds) if settings.mineru_base_url else None)
                        if payload.attachment_ids else None,
                )
                loop.call_soon_threadsafe(enqueue, "done", turn.model_dump(mode="json"))
        except ChatCancelled:
            loop.call_soon_threadsafe(enqueue, "cancelled", None)
        except Exception as error:
            # 不把底层异常直接序列化，继续使用应用已定义的脱敏错误处理器。
            loop.call_soon_threadsafe(enqueue, "failure", error)
        finally:
            event_sink.reset(token)
            request_cancelled.reset(cancellation_token)

    """事件输出函数：心跳维持连接，错误事件保留原状态码供前端处理冲突。"""

    async def events() -> AsyncIterator[str]:
        nonlocal connected
        worker = asyncio.create_task(asyncio.to_thread(run))
        key = (session_id, payload.message_id)
        cancellations = request.app.state.chat_cancellations
        cancellations.setdefault(key, set()).add(cancelled)

        """完成回调函数：工作线程结束后才释放停止记录，不以浏览器断开冒充已停止。"""

        def finished(task: asyncio.Task[None]) -> None:
            cancellations[key].discard(cancelled)
            if not cancellations[key]:
                del cancellations[key]

        worker.add_done_callback(finished)
        # 任务属于本应用；停服时先等它们保存和关闭连接，再释放数据库。
        workers: set[asyncio.Task[None]] = request.app.state.chat_workers
        workers.add(worker)
        worker.add_done_callback(workers.discard)
        try:
            yield ('event: progress\ndata: '
                   '{"stage":"received","message":"已收到问题，正在准备回答"}\n\n')
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == "cancelled":
                    event, data = "error", {"status": 499,
                                            "message": "本次生成已停止，已有行程保留。",
                                            "request_id": request.state.request_id}
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
