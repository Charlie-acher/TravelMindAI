"""聊天业务层：整理单轮需求，串联历史、知识库回答和整轮保存。

HTTP接口传入消息和已准备的服务；本层不依赖Request或其他HTTP处理函数。
"""

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from app.schemas.requirement.chat import RequirementChatResponse, RequirementMessage
from app.schemas.requirement.history import SavedRequirementMessage, SavedRequirementTurn
from app.services.amap import AmapClient
from app.services.chat.rag import build_chat_context, ground_chat_response
from app.services.document.search import DocumentSearchService
from app.services.document.search import search_context as document_topic
from app.services.requirement.extract import ModelClient, extract_requirements
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.requirement.merge import LIST_FIELDS, SCALAR_FIELDS
from app.services.web_search import WebSearchClient

"""需求回复函数：提取旅行条件，生成补问、状态和本轮改变的字段。"""


def build_requirement_response(
    payload: RequirementMessage, model: ModelClient, request_id: str,
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
        # 非规划意图不更新卡片；把目前能处理的范围说清楚，方便用户继续调整。
        # 这里只展示程序维护的范围，不把模型assumptions直接当作面向用户的回复。
        reply = (
            "当前页面可以帮你整理单个目的地、2～5天、1～8人的旅行需求，预算按人民币计算。"
            "你可以调整旅行条件，或先告诉我想去哪里，我们一起补充。"
        )
        if payload.previous is not None:
            reply += "之前的旅行需求已保留。"
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
        request_id=request_id,
    )


"""持久化聊天函数：先检查重试和版本，再生成回答，最后用短事务保存。

检索连接延后进入：已保存消息的重试即使当前向量配置失效，也能直接返回历史。
"""


def process_saved_message(
    session_id: UUID, payload: SavedRequirementMessage, model: ModelClient,
    service: RequirementHistoryService, request_id: str,
    search_context: AbstractContextManager[DocumentSearchService], maps: AmapClient,
    web: WebSearchClient | None = None,
) -> SavedRequirementTurn:
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
            if turn.response.status in {"needs_clarification", "complete"}
        ),
        None,
    )
    reference_date = history.turns[0].response.result.reference_date if history.turns else None
    # 两种HTTP入口都调用同一业务函数，采用相同的抽取、追问和变化字段规则。
    response = build_requirement_response(
        RequirementMessage(
            message=payload.message, previous=previous, reference_date=reference_date
        ),
        model,
        request_id,
    )
    context = build_chat_context(history.turns)
    # 回答景点追问的短偏好仍是知识问答，不因抽取器误判而索要整份旅行计划。
    last = history.turns[-1].response if history.turns else None
    if (last is not None and last.status == "knowledge" and last.knowledge is not None
            and last.knowledge.clarification and document_topic(payload.message, context)
            and not any(word in payload.message for word in ("规划", "行程", "安排"))
            and response.result.extraction.days is None):
        response = response.model_copy(update={"status": "unsupported", "changed_fields": []})
    # 每轮主聊天必须走检索；不配置或检索失败时禁止退回自由回答。
    # 放在幂等检查之后，已保存消息的重试无需再次访问知识库。
    destination = response.result.extraction.destination or (
        previous.destination if previous else None
    )
    with search_context as search:
        response = ground_chat_response(
            response, search, model, destination, context,
            maps=maps, web=web,
        )
    # 等待模型时没有持有数据库事务；这里再锁行检查，处理两个页面同时发消息。
    return service.append(session_id, payload.message_id, payload.expected_revision, response)
