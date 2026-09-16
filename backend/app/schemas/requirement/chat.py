"""
聊天接口格式：定义前后端传输的请求和响应数据。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.document.answer import AnswerResult
from app.schemas.requirement.base import RequirementResult, TravelRequestExtraction


class RequirementMessage(BaseModel):
    """聊天请求类：接收用户消息和已有旅行需求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=6000)
    previous: TravelRequestExtraction | None = None
    # 固定本次对话的参考日期，避免跨天后改变“明天”等说法的含义。
    reference_date: date | None = None


class RequirementChatResponse(BaseModel):
    """聊天响应类：返回回复文字和最新旅行需求。"""

    result: RequirementResult
    reply: str
    status: Literal["needs_clarification", "complete", "unsupported", "knowledge"]
    changed_fields: list[str]  # 本轮实际发生变化的业务字段，供前端高亮。
    request_id: str  # 排查失败时可对照响应头X-Request-ID。
    knowledge: AnswerResult | None = None  # 本轮RAG回答及原文快照；旧历史没有此字段时为None。
