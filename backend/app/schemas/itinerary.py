"""数据格式层：定义逐日草稿、可追溯地点、版本快照和撤销请求。"""

from datetime import date as CalendarDate
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.budget import BudgetSummary
from app.schemas.document.answer import MapLookup


class PlanSource(BaseModel):
    """行程来源类：保存工具实际返回的原文，便于历史回查。"""

    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["knowledge", "web"]
    title: str
    text: str
    url: HttpUrl | None = None


class PlanPlace(BaseModel):
    """行程地点类：名称和位置来自地图，原文来自检索工具。"""

    model_config = ConfigDict(extra="forbid")
    id: str
    map: MapLookup
    sources: list[PlanSource] = Field(min_length=1, max_length=5)


class PlannedActivity(BaseModel):
    """活动安排类：时间是规划建议，交通预留不代表已查路线耗时。"""

    model_config = ConfigDict(extra="forbid")
    place: PlanPlace
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(ge=30, le=240, strict=True)
    transport: Literal["walk", "transit", "taxi"] = "transit"
    transfer_minutes: int = Field(ge=0, le=120, strict=True)


class PlanDay(BaseModel):
    """逐日安排类：保存当天活动顺序和可选的出游日期。"""

    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=5, strict=True)
    date: CalendarDate | None = None
    activities: list[PlannedActivity] = Field(min_length=1, max_length=5)


class TravelPlan(BaseModel):
    """行程草稿类：保存经程序校验的逐日活动和明确标注的预算估算。"""

    model_config = ConfigDict(extra="forbid")
    format: Literal["daily-plan-v1"] = "daily-plan-v1"
    title: str = Field(min_length=1, max_length=100)
    destination: str
    days: list[PlanDay] = Field(min_length=2, max_length=5)
    budget: BudgetSummary
    warnings: list[str]


class PlanSnapshot(BaseModel):
    """行程版本快照类：把草稿和实际修改摘要放在同一聊天消息中。"""

    model_config = ConfigDict(extra="forbid")
    itinerary_id: UUID
    version: int = Field(ge=1)
    previous_version: int | None = None
    operation: Literal["create", "modify", "undo"]
    plan: TravelPlan
    changes: list[str]
    # 撤销目标使用本轮message_id；第一版和撤销结果均不可再次撤销。
    can_undo: bool = False


class UndoDraftRequest(BaseModel):
    """撤销请求类：用操作编号保证幂等，用双版本拒绝过期页面。"""

    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    target_message_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    expected_itinerary_version: int = Field(ge=1, strict=True)
