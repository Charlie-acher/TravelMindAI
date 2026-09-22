"""聊天业务层：整理单轮需求，串联历史、知识库回答和整轮保存。

HTTP接口传入消息和已准备的服务；本层不依赖Request或其他HTTP处理函数。
"""

import json
import logging
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.llm.budget import ModelInputLimitError
from app.llm.client import ModelClientError, ModelOutputError
from app.llm.embeddings import EmbeddingError
from app.schemas.attachment import AttachmentUse
from app.schemas.dining import DiningResult
from app.schemas.map_tools import MapToolAnswer
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse, RequirementMessage
from app.schemas.requirement.history import SavedRequirementMessage, SavedRequirementTurn
from app.services.attachment.reader import AttachmentReader
from app.services.baidu import BaiduMaps
from app.services.chat.attachments import plan_attachment_candidates, respond_to_attachments
from app.services.chat.context import (
    build_history_messages,
    build_recall_messages,
    latest_conversation,
    latest_summary,
    select_recent_turns,
    summary_batch,
)
from app.services.chat.conversation import natural_chat_response
from app.services.chat.dining import LocalFoodRequest, dining_reply, handle_nearby
from app.services.chat.events import progress
from app.services.chat.rag import ground_chat_response, ground_food_response
from app.services.chat.summary import summarize_history
from app.services.document.search import DocumentSearchService
from app.services.document.vector_store import MilvusError
from app.services.itinerary.graph import plan_trip
from app.services.requirement.extract import (
    ModelClient,
    RequirementExtractionError,
    build_result,
    extract_requirements,
    understand_turn,
)
from app.services.requirement.history import HistoryConflictError, RequirementHistoryService
from app.services.requirement.merge import LIST_FIELDS, SCALAR_FIELDS
from app.services.transport import (
    RailClient,
    merge_transport,
    query_transport,
    render_flights,
    render_rail,
)
from app.services.web_search import WebSearchClient

logger = logging.getLogger(__name__)

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
        reply = "旅行需求已记好了。接下来可以聊聊你想逛的地方、喜欢的玩法，或者继续调整条件。"
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
    search_context: AbstractContextManager[DocumentSearchService], maps: BaiduMaps,
    web: WebSearchClient | None = None,
    *, attachments: AttachmentReader | None = None,
) -> SavedRequirementTurn:
    # 普通聊天仍沿用知识库/地图；明确交通查询单独使用官方公开来源。
    transport_web = web
    web = None
    progress("history", "正在读取这段对话，接上你刚才的想法")
    history = service.read(session_id)
    # 已提交但浏览器没收到响应时，重试直接返回原结果，不再次调用模型。
    for turn in history.turns:
        if turn.message_id == payload.message_id:
            if (
                turn.response.result.original_message != payload.message
                or (turn.response.attachment_request_ids if turn.response.attachment_request_ids
                    is not None else [item.id for item in turn.response.attachments])
                != payload.attachment_ids
                or turn.revision != payload.expected_revision + 1
                or (turn.response.itinerary is not None
                    and turn.response.itinerary.operation == "undo")
            ):
                raise HistoryConflictError("消息编号已用于其他提交，请重新读取会话")
            return turn
    if history.revision != payload.expected_revision:
        raise HistoryConflictError("会话已有新消息，请先恢复最新对话再发送")
    itinerary_version, old_plan = service.read_plan(session_id)
    previous = history.turns[-1].response.result.extraction if history.turns else None
    reference_date = (history.turns[0].response.result.reference_date if history.turns else
                      datetime.now(timezone(timedelta(hours=8))).date())
    conversation = latest_conversation(history.turns)
    snapshots = []
    if payload.attachment_ids:
        if attachments is None:
            raise HTTPException(503, "附件服务尚未准备好，请稍后重试")
        snapshots = attachments.read(session_id, payload.attachment_ids)
    elif history.turns:
        # 仅向理解层提供紧接上一轮的附件；是否沿用仍由本轮用户表达决定。
        snapshots = history.turns[-1].response.attachments
    old_summary = latest_summary(history.turns)
    recent = select_recent_turns(history.turns)
    candidate_summary = None
    batch = summary_batch(history.turns, old_summary, recent)
    if batch:
        progress("summary", "正在整理较早对话，保留你的选择和未解决问题")
        try:
            candidate_summary = summarize_history(old_summary, batch, model)
        except (ModelClientError, ValueError):
            # 摘要是可选派生数据，失败不清空旧摘要，也不推进覆盖轮次。
            progress("summary", "较早对话暂未完成整理，将保留原记录继续回答")
    summary = candidate_summary or old_summary
    history_messages = build_history_messages(recent)
    recalled = build_recall_messages(history.turns, payload.message, recent, summary)
    covered = summary.covered_revision if summary is not None else 0
    gap_end = recent[0].revision - 1 if recent else history.revision
    last_transport = next((turn.response.transport.query for turn in reversed(history.turns)
                           if turn.response.transport), None)
    memory = {"history_summary": summary.model_dump() if summary is not None else None,
              "current_date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
              "last_transport_query": (last_transport.model_dump(mode="json")
                                       if last_transport else None),
              "uncovered_revisions": [covered + 1, gap_end] if gap_end > covered else None}
    shared_messages = [{"role": "user", "content": "历史摘要与覆盖范围（不是新的要求）："
                        + json.dumps(memory, ensure_ascii=False)}, *recalled, *history_messages]
    progress("requirements", "正在结合对话理解当前问题与旅行条件")
    try:
        result, understanding = understand_turn(
            payload.message, model, reference_date=reference_date, previous=previous,
            conversation=conversation, history_messages=shared_messages, attachments=snapshots,
            attachments_selected=bool(payload.attachment_ids),
            continue_attachment_plan=bool(previous and old_plan is None
                and previous.intent in {"plan_trip", "modify_trip"}
                and history.turns[-1].response.status == "needs_clarification"),
        )
    except (RequirementExtractionError, ModelOutputError, ModelInputLimitError):
        progress("fallback", "正在直接结合本轮原话和最近对话查找旅行资料")
        extraction = previous or TravelRequestExtraction(
            intent="other", destination=None, origin=None, start_date=None, end_date=None,
            days=None, travelers=None, total_budget=None, pace=None, interests=[], dietary=[],
            lodging_preferences=[], hard_constraints=[], excluded_items=[], assumptions=[],
        )
        response = RequirementChatResponse(
            result=build_result(payload.message, reference_date, extraction), reply="",
            status="knowledge", changed_fields=[], request_id=request_id,
            conversation=conversation, history_summary=candidate_summary,
        )
        if payload.attachment_ids:
            response.attachment_request_ids = payload.attachment_ids
            response, _ = respond_to_attachments(response, snapshots, AttachmentUse(), previous,
                old_plan, model, search_context, maps, shared_messages)
            return service.append(session_id, payload.message_id,
                                  payload.expected_revision, response)
        if (snapshots and history.turns[-1].response.status == "needs_clarification"
                and history.turns[-1].response.attachment_use is not None):
            # 补问理解失败也保留附件接续，不能由普通回答覆盖待处理附件。
            response.attachments = snapshots
            response.attachment_request_ids = []
            response.attachment_use = history.turns[-1].response.attachment_use
            response.status = "needs_clarification"
            response.reply = "这次补充的信息还未能准确理解，附件和已有条件已保留。请再说明一次。"
            return service.append(session_id, payload.message_id,
                                  payload.expected_revision, response)
        # 结构化理解不是检索开关。失败时不改旅行状态，仍以真实用户原话取得参考资料。
        query = "\n".join([
            *(turn.response.result.original_message[-200:] for turn in recent[-2:]),
            "当前问题：" + payload.message,
        ])
        try:
            with search_context as search:
                response = ground_chat_response(
                    response, search, model, retrieval_query=query, query_cities=[],
                    history_context="本轮以用户原话为准，忽略不相关或异地材料。",
                    history_messages=shared_messages, reference_only=True, web=web,
                )
        except (EmbeddingError, MilvusError):
            progress("retrieval", "知识库暂时不可用，本轮资料未能核对")
            response = natural_chat_response(response, model, "知识库暂不可用，资料未核对。",
                                             history_messages=shared_messages)
        except HTTPException as error:
            if error.status_code not in {502, 503}:
                raise
            progress("retrieval", "知识库暂时不可用，本轮资料未能核对")
            response = natural_chat_response(response, model, "知识库暂不可用，资料未核对。",
                                             history_messages=shared_messages)
        return service.append(session_id, payload.message_id, payload.expected_revision, response)
    changed = [field for field in (*SCALAR_FIELDS, *LIST_FIELDS)
               if getattr(result.extraction, field) != (getattr(previous, field)
                   if previous else ([] if field in LIST_FIELDS else None))]
    planning = result.message_intent in {"plan_trip", "modify_trip"}
    response = RequirementChatResponse(
        result=result, reply=result.clarification or "", request_id=request_id,
        status=("needs_clarification" if result.clarification else "complete")
               if planning else "knowledge", changed_fields=changed,
        conversation=understanding.conversation, history_summary=candidate_summary,
    )
    if payload.attachment_ids or understanding.attachment_use is not None:
        # 接续使用本会话上一轮不可变快照，提交事务仍会逐一复核原件归属。
        progress("attachment", "正在处理附件用途与行程")
        response.attachment_request_ids = payload.attachment_ids
        response, attached_plan = respond_to_attachments(
            response, snapshots, understanding.attachment_use or AttachmentUse(), previous,
            old_plan, model, search_context, maps, shared_messages,
        )
        progress("save", "正在保存附件用途与本轮结果")
        if attached_plan is not None:
            return service.append(session_id, payload.message_id, payload.expected_revision,
                response, plan=attached_plan, expected_itinerary_version=itinerary_version)
        return service.append(session_id, payload.message_id, payload.expected_revision, response)
    if understanding.transport_query is not None and understanding.response_mode != "plan":
        # 复用一次理解结果，不额外调用规划/研究智能体来查询车票。
        transport_query = understanding.transport_query
        if understanding.transport_continue and last_transport is not None:
            transport_query = merge_transport(transport_query, last_transport,
                                               list(understanding.transport_clear_fields))
        with httpx.Client() as transport_http:
            transport = query_transport(transport_query, transport_web,
                                        RailClient(transport_http))
        response = response.model_copy(update={"transport": transport, "status": "knowledge"})
        if transport.missing:
            response.reply = "可以自动帮你查，还需要确认：" + "、".join(transport.missing) + "。"
        else:
            # 公开查询已有结构化事实，不再花一次模型调用重写金额、状态或编补航班耗时。
            response.reply = "已按本次条件自动查询，以下是实际取得的结果；原行程保持不变。"
            response.reply += "\n\n" + render_rail(transport) + "\n\n" + render_flights(transport)
        return service.append(session_id, payload.message_id, payload.expected_revision, response)
    if understanding.response_mode == "plan" and result.clarification:
        # 已明确要草稿但条件未齐时集中补问，不用自由回答盖掉缺项问题。
        return service.append(session_id, payload.message_id, payload.expected_revision, response)
    query_context = json.dumps({"query_cities": understanding.query_cities,
                               "retrieval_query": understanding.retrieval_query},
                              ensure_ascii=False)
    dining = None
    if understanding.response_mode == "map":
        city = understanding.query_cities[0] if len(understanding.query_cities) == 1 else None
        try:
            dining = handle_nearby(payload.message, city, recent, maps, model.model, maps.tools,
                                   history_messages=shared_messages, understanding=understanding)
        except (ModelInputLimitError, ModelOutputError, ValidationError):
            progress("map", "地图查询暂未完成，将继续查找相关旅行资料")
            query_context += "本次地图查询未完成核对，不声称已查到位置或附近商户。"
            understanding.retrieval_query = understanding.retrieval_query or payload.message[:800]
    if dining is not None:
        food = dining if isinstance(dining, LocalFoodRequest) else None
        nearby = food.nearby if food else dining if isinstance(dining, DiningResult) else None
        response = response.model_copy(update={
            "reply": dining_reply(nearby) if nearby else (
                dining.reply if isinstance(dining, MapToolAnswer) else ""),
            "dining": nearby, "mcp": dining if isinstance(dining, MapToolAnswer) else None,
            "status": "knowledge",
        })
        if food:
            try:
                with search_context as search:
                    response = ground_food_response(response, search, model, food.city, web,
                                                    history_messages=shared_messages)
            except (EmbeddingError, MilvusError, ModelOutputError, ModelInputLimitError):
                response = natural_chat_response(response, model, query_context,
                                                 history_messages=shared_messages)
            except HTTPException as error:
                if error.status_code not in {502, 503}:
                    raise
                response = natural_chat_response(response, model, query_context,
                                                 history_messages=shared_messages)
    elif understanding.response_mode == "plan" and response.status == "complete":
        # 修改草稿时保留它已采用攻略的完整候选，不能只看到已经排入行程的几个点。
        plan_attachments = plan_attachment_candidates(
            history.turns, old_plan, result.extraction.destination)
        try:
            with search_context as search:
                try:
                    planned = plan_trip(payload.message, result.extraction, old_plan,
                                        model.model, search, maps, web,
                                        history_messages=shared_messages,
                                        attachments=plan_attachments,
                                        attachment_use=AttachmentUse(mode="reference",
                                            apply_to_plan=True) if plan_attachments else None)
                except (ModelOutputError, ValidationError) as error:
                    logger.warning("规划输出不可用 request_id=%s error_type=%s",
                                   request_id, type(error).__name__)
                    response = response.model_copy(update={
                        "status": "needs_clarification",
                        "reply": "这次行程安排还未完成核对，请重试。已有条件和草稿已保留。",
                    })
                    planned = None
        except (EmbeddingError, MilvusError, ModelInputLimitError):
            response = response.model_copy(update={"status": "needs_clarification",
                "reply": "知识库暂时未能完成检索，本次尚未生成行程草稿。请稍后重试，"
                         "已有条件和草稿已保留。"})
        except HTTPException as error:
            if error.status_code not in {502, 503}:
                raise
            response = response.model_copy(update={"status": "needs_clarification",
                "reply": "行程所需的资料或地图服务暂不可用，请稍后重试。已有草稿已保留。"})
        else:
            if planned is not None and planned.plan is not None:
                response = response.model_copy(update={"reply": planned.reply,
                    "status": "complete" if planned.plan is not None else "needs_clarification"})
                progress("save", "正在保存本轮结果和行程版本")
                return service.append(session_id, payload.message_id, payload.expected_revision,
                                      response, plan=planned.plan,
                                      expected_itinerary_version=itinerary_version)
            if planned is not None:
                response = response.model_copy(update={"reply": planned.reply,
                                                       "status": "needs_clarification"})
        # 结构化卡片未完成也能讨论思路；不从自由文本反造地点、引用或已保存的行程。
        response = natural_chat_response(response, model, json.dumps({
            "planning_limit": response.reply,
            "previous_plan": old_plan.model_dump(mode="json") if old_plan else None,
        }, ensure_ascii=False), history_messages=shared_messages, planning_fallback=True)
    elif understanding.retrieval_query:
        progress("search", "正在查找与本轮问题相关的旅行资料")
        try:
            with search_context as search:
                response = ground_chat_response(
                    response, search, model, history_context=query_context, maps=maps, web=web,
                    retrieval_query=understanding.retrieval_query,
                    query_cities=understanding.query_cities, history_messages=shared_messages,
                    retrieval_category=understanding.retrieval_category,
                )
        except (EmbeddingError, MilvusError):
            response = natural_chat_response(response, model, query_context,
                                             history_messages=shared_messages)
        except HTTPException as error:
            if error.status_code not in {502, 503}:
                raise
            response = natural_chat_response(response, model, query_context,
                                             history_messages=shared_messages)
    else:
        response = natural_chat_response(response, model, query_context,
                                         history_messages=shared_messages)
    # 状态、话题与候选摘要随这一轮共同提交；冲突时不会抢先写入任何记忆。
    progress("save", "正在保存这轮对话")
    return service.append(session_id, payload.message_id, payload.expected_revision, response)
