"""附件聊天层：按本轮用途调用既有规划图，失败保留草稿，读取不改变旅行条件。"""

import logging
from contextlib import AbstractContextManager

from fastapi import HTTPException
from pydantic import ValidationError

from app.llm.budget import ModelInputLimitError
from app.llm.client import ModelClientError
from app.llm.embeddings import EmbeddingError
from app.schemas.attachment import AttachmentSnapshot, AttachmentUse
from app.schemas.itinerary import TravelPlan
from app.schemas.requirement.base import TravelRequestExtraction
from app.schemas.requirement.chat import RequirementChatResponse
from app.schemas.requirement.history import SavedRequirementTurn
from app.services.attachment.reader import attachment_reply
from app.services.baidu import BaiduMaps
from app.services.document.search import DocumentSearchService
from app.services.document.vector_store import MilvusError
from app.services.itinerary.attachments import resolve_attachment_use
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.rules import requested_days
from app.services.requirement.extract import ModelClient, build_result

logger = logging.getLogger(__name__)

"""已用附件回查函数：按当前草稿的来源回查完整识别快照，供后续修改选择其他景点。"""

def plan_attachment_candidates(
    turns: list[SavedRequirementTurn], old: TravelPlan | None, destination: str | None,
) -> list[AttachmentSnapshot]:
    if old is None or old.destination != destination:
        return []
    identifiers = {source.attachment_id for day in old.days for activity in day.activities
                   for source in activity.place.sources if source.kind == "attachment"}
    found = {}
    for turn in reversed(turns):
        for item in turn.response.attachments:
            if item.id in identifiers and item.id not in found and item.analysis is not None:
                found[item.id] = item
    return list(found.values())

"""附件用途执行函数：用途不明先补问；规划成功后才将草稿交给原有事务保存。"""

def respond_to_attachments(
    response: RequirementChatResponse, items: list[AttachmentSnapshot], use: AttachmentUse,
    previous: TravelRequestExtraction | None, old: TravelPlan | None,
    model: ModelClient, search_context: AbstractContextManager[DocumentSearchService],
    maps: BaiduMaps, history_messages: list[dict[str, str]],
) -> tuple[RequirementChatResponse, TravelPlan | None]:
    wants_plan = bool(items) and use.apply_to_plan and use.mode in {
        "reference", "required", "replace",
    }
    try:
        use = resolve_attachment_use(use, old)
    except ValueError as error:
        response.attachments, response.attachment_use = items, use
        response.reply, response.status = str(error), "needs_clarification"
        if previous is not None:
            response.result = build_result(response.result.original_message,
                                           response.result.reference_date, previous)
            response.changed_fields = []
        return response, None
    if wants_plan and not use.target_days:
        target = requested_days(response.result.original_message, old, response.result.extraction)
        if target is not None:
            use = use.model_copy(update={"target_days": sorted(target)})
    response.attachments, response.attachment_use = items, use
    plan = None
    if wants_plan:
        # 明确规划时统一核对必填条件，不能因模型误标知识问答而跳过缺项检查。
        requirements = response.result.extraction.model_copy(update={"intent": "plan_trip"})
        checked = build_result(response.result.original_message,
                               response.result.reference_date, requirements)
        response.result = checked
        if checked.clarification:
            response.reply = checked.clarification
        else:
            try:
                with search_context as search:
                    planned = plan_trip(checked.original_message, requirements, old,
                        model.model, search, maps, None, history_messages=history_messages,
                        attachments=items, attachment_use=use)
                response.reply, plan = planned.reply, planned.plan
            except (ModelClientError, ModelInputLimitError, EmbeddingError, MilvusError,
                    ValidationError) as error:
                # 记录错误类别供后台排查，不将模型或工具异常变成用户的地点核对任务。
                logger.warning("附件规划服务失败 request_id=%s error_type=%s",
                               response.request_id, type(error).__name__)
                response.reply = "行程生成服务暂时出错，请稍后重试；附件和已有安排已保留。"
            except HTTPException as error:
                if error.status_code not in {502, 503}:
                    raise
                response.reply = "附件地点服务暂不可用，请稍后重试；本次没有修改行程。"
        response.status = "complete" if plan is not None else "needs_clarification"
    else:
        response.reply = attachment_reply(items)
        response.status = "knowledge"
        if not items:
            response.reply = "当前没有可接续的附件，请重新选择要采用的文件，再说明用途或目标日。"
            response.status = "needs_clarification"
        elif use.mode == "unclear" or use.mode in {"required", "replace"}:
            response.reply += "\n\n这份附件是供参考、地点都要去，还是替换某一天？"
            response.status = "needs_clarification"
    if not wants_plan or (plan is None and old is not None):
        # 读取、参考及失败修改不把模型猜出的附件条件写成用户已确认条件。
        extraction = previous or TravelRequestExtraction(
            intent="other", destination=None, origin=None, start_date=None, end_date=None,
            days=None, travelers=None, total_budget=None, pace=None, interests=[], dietary=[],
            lodging_preferences=[], hard_constraints=[], excluded_items=[], assumptions=[],
        )
        response.result = build_result(response.result.original_message,
                                       response.result.reference_date, extraction)
        response.changed_fields = []
    if wants_plan and plan is None and old is not None:
        response.reply += "\n已有旅行条件和行程草稿已保留。"
    return response, plan
