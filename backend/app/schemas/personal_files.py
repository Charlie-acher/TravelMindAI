"""数据格式层：定义个人文件分页、原件详情和行程版本详情。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.attachment import AttachmentView
from app.schemas.itinerary import PlanSnapshot
from app.schemas.requirement.base import TravelRequestExtraction

FileKind = Literal["attachment", "itinerary"]


class PersonalFileItem(BaseModel):
    """文件条目类：记录原有文件身份和来源，行程大小在生成下载内容前保持未知。"""

    id: UUID
    kind: FileKind
    file_name: str
    mime_type: str
    size_bytes: int | None
    session_id: UUID
    session_title: str
    created_at: datetime
    version: int | None = None
    version_count: int | None = None


class PersonalFilePage(BaseModel):
    """文件分页类：总数由数据库按相同过滤条件计算，不受当前页大小影响。"""

    items: list[PersonalFileItem]
    total: int
    offset: int
    limit: int


class PersonalFileDetail(BaseModel):
    """文件详情类：原件复用附件信息，行程复用已保存的版本和完整需求快照。"""

    item: PersonalFileItem
    attachment: AttachmentView | None = None
    itinerary: PlanSnapshot | None = None
    extraction: TravelRequestExtraction | None = None
    versions: list[PersonalFileItem] = Field(default_factory=list)
