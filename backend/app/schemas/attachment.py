"""附件数据格式层：保存私人原件信息和可回查的路线识别结果。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AttachmentWaypoint(BaseModel):
    """附件途经点类：保留图中文字或文档依据，不冒充已核实的地图地点。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    order: int | None = Field(default=None, ge=1, le=100)
    evidence: str = Field(min_length=1, max_length=1000)
    needs_confirmation: bool = True


class AttachmentAnalysis(BaseModel):
    """附件识别结果类：保存可见内容、地点顺序及不能确认的事项。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=3000)
    waypoints: list[AttachmentWaypoint] = Field(default_factory=list, max_length=300)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    parser_version: str | None = None  # 程序填写；旧快照为空，重新发送时可升级解析。


class AttachmentView(BaseModel):
    """附件展示类：返回当前会话原件与识别状态，不暴露磁盘路径。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    file_name: str
    mime_type: str
    size_bytes: int
    status: Literal["uploaded", "ready", "needs_confirmation", "failed"]
    analysis: AttachmentAnalysis | None = None
    error_message: str | None = None
    created_at: datetime


class AttachmentSnapshot(BaseModel):
    """附件轮次快照类：历史展示使用当时结果，刷新不重新识别。"""

    id: UUID
    file_name: str
    size_bytes: int | None = Field(default=None, gt=0)  # 旧历史可为空，新轮次保留真实大小。
    analysis: AttachmentAnalysis | None = None
    error_message: str | None = None


class AttachmentUse(BaseModel):
    """附件用途类：只属于本轮消息，读取、参考与必须采用的地点分开处理。"""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["read", "reference", "required", "replace", "unclear"] = "unclear"
    apply_to_plan: bool = False  # 只有用户要求生成或修改行程时才允许规划。
    target_days: list[Annotated[int, Field(ge=1, le=5, strict=True)]] = Field(
        default_factory=list, max_length=5,
    )
    target_places: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(
        default_factory=list, max_length=12,
    )  # 单点替换时记录原行程中被换掉的地点，不是新附件地点。
