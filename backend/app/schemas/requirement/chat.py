"""
聊天接口格式：定义前后端传输的请求和响应数据。
"""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.llm.contracts import SelectableProvider
from app.schemas.attachment import AttachmentSnapshot, AttachmentUse
from app.schemas.dining import DiningResult
from app.schemas.document.answer import AnswerResult
from app.schemas.itinerary import PlanSnapshot
from app.schemas.map_tools import MapToolAnswer
from app.schemas.requirement.base import RequirementResult, TravelRequestExtraction
from app.schemas.requirement.conversation import ConversationState, HistorySummary
from app.schemas.transport import TransportResult
from app.schemas.workflow import WorkflowResume, WorkflowSnapshot


class RequirementMessage(BaseModel):
    """聊天请求类：接收用户消息和已有旅行需求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=6000)
    previous: TravelRequestExtraction | None = None
    # 固定本次对话的参考日期，避免跨天后改变“明天”等说法的含义。
    reference_date: date | None = None
    selected_provider: SelectableProvider = "deepseek"


class RequirementChatResponse(BaseModel):
    """聊天响应类：返回回复文字和最新旅行需求。"""

    result: RequirementResult
    reply: str
    status: Literal["needs_clarification", "complete", "unsupported", "knowledge"]
    changed_fields: list[str]  # 本轮实际发生变化的业务字段，供前端高亮。
    request_id: str  # 排查失败时可对照响应头X-Request-ID。
    knowledge: AnswerResult | None = None  # 本轮RAG回答及原文快照；旧历史没有此字段时为None。
    dining: DiningResult | None = None  # 商户事实来自地图查询，连同锚点保存在原有JSON快照。
    itinerary: PlanSnapshot | None = None  # 仅成功提交的逐日草稿携带版本及修改摘要。
    mcp: MapToolAnswer | None = None  # 百度工具依据随原有JSON保存；旧记录不必迁移。
    conversation: ConversationState | None = None  # None是旧版缺字段，空话题是明确清除。
    history_summary: HistorySummary | None = None  # 仅摘要更新轮写入，覆盖范围随整轮事务提交。
    attachments: list[AttachmentSnapshot] = Field(default_factory=list, max_length=3)
    attachment_use: AttachmentUse | None = None
    # 补问可沿用上轮附件；独立保留浏览器实际提交的编号，避免重试误判冲突。
    attachment_request_ids: list[UUID] | None = Field(default=None, max_length=3)
    workflow: WorkflowSnapshot | None = None
    workflow_request: WorkflowResume | None = None  # 保存原选择，重试不得换成另一动作。
    selected_provider: SelectableProvider = "deepseek"  # 老历史按原固定模型兼容读取。
    used_providers: list[SelectableProvider] = Field(default_factory=list)
    agent_tasks: list[dict[str, Any]] = Field(default_factory=list)  # 程序生成的研究委派摘要。
    transport: TransportResult | None = None  # 官方交通查询快照，旧记录没有时为None。
