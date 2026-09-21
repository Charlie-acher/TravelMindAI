"""编排接口层：描述待选择状态与恢复请求，不向浏览器暴露内部检查点。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.itinerary import TravelPlan


class WorkflowResume(BaseModel):
    """恢复请求类：同一执行只接受当前页面明确选择的动作。"""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    action: Literal["continue", "accept", "cancel"]


class WorkflowSnapshot(BaseModel):
    """编排快照类：随历史保存审查次数、问题和可选动作。"""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    status: Literal["waiting", "completed", "cancelled"]
    attempts: int = Field(ge=0, le=3)
    issues: list[str] = Field(default_factory=list)
    can_accept: bool = False
    preview: TravelPlan | None = None
