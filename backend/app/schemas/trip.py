"""
数据格式层：定义旅行会话和草稿的请求与响应数据。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.schemas.budget import BudgetInput


class SessionCreate(BaseModel):
    """会话创建请求类：接收新会话的标题。"""

    # 去掉首尾空白后检查长度，规则与 TripService.create_session 一致。
    title: str = Field(default="新建对话", min_length=1, max_length=200, examples=["杭州三日游"])
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftCreate(BaseModel):
    """草稿保存请求类：接收旅行条件、标题和说明。"""

    # 复用已有天数、人数、金额和日期校验，而不是在新接口另写一套规则。
    requirements: BudgetInput
    # 草稿标题与说明来自用户，当前尚未生成逐日行程。
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=5000)
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "title": "杭州三日预算草稿",
                    "notes": "先确认预算，再安排景点",
                    "requirements": {
                        "days": 3,
                        "travelers": 2,
                        "total_budget": "5000.00",
                        "lodging": "economy",
                    },
                }
            ]
        },
    )


class SessionView(BaseModel):
    """会话信息类：保存会话编号、标题、状态和时间。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID         # 业务会话编号，后续查询使用这个值。
    thread_id: UUID  # 独立的图执行线程编号，尚未启用 LangGraph 检查点。
    title: str       # 会话标题。
    status: Literal["active", "archived"]   # 使用中或已归档。
    created_at: datetime  # 创建时刻，序列化为带时区的 ISO 时间。
    updated_at: datetime  # 最近一次保存后的修改时刻。


class RequirementView(BaseModel):
    """需求版本信息类：保存某个版本的旅行需求。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID  # 这份需求快照的编号。
    session_id: UUID  # 所属旅行会话。
    version: int  # 会话内需求版本。
    request_json: dict[str, JsonValue]  # 原始需求快照，金额已经转换为字符串。
    created_at: datetime  # 版本创建时刻。


class ItineraryView(BaseModel):
    """行程版本信息类：保存某个版本的行程草稿。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID  # 行程版本编号。
    session_id: UUID  # 所属旅行会话。
    request_id: UUID  # 指向 RequirementView.id，不是 HTTP 请求跟踪编号。
    version: int  # 会话内行程版本。
    status: Literal["draft", "confirmed"]  # 新保存的一律为草稿。
    itinerary_json: dict[str, JsonValue]  # 草稿内容，本接口保存标题、说明、空活动和预算。
    created_at: datetime  # 草稿版本创建时刻。


class DraftView(BaseModel):
    """草稿详情类：组合行程草稿和它对应的旅行需求。"""

    model_config = ConfigDict(from_attributes=True)
    requirement: RequirementView  # 对应 SavedDraft.requirement。
    itinerary: ItineraryView  # 对应 SavedDraft.itinerary。


class SessionResponse(BaseModel):
    """会话响应类：返回会话信息和请求编号。"""

    session: SessionView  # 会话数据。
    request_id: str  # 本次 HTTP 请求编号，与响应头 X-Request-ID 相同。


class SessionPage(BaseModel):
    """历史分页类：返回当前账号会话和下一页游标。"""

    items: list[SessionView]
    next_cursor: str | None


class DraftResponse(BaseModel):
    """草稿响应类：返回草稿详情和请求编号。"""

    draft: DraftView  # 完整需求与行程快照。
    request_id: str  # 本次 HTTP 请求编号。
