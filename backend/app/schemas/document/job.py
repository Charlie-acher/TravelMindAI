"""数据格式层：定义后台处理的输入与可见任务状态。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentJobRequest(BaseModel):
    """任务请求类：明确选择重读原件或建立索引。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["parse", "index"]


class DocumentJobView(BaseModel):
    """任务结果类：展示已持久化状态，进度仅计入确认写入的片段。"""

    model_config = ConfigDict(from_attributes=True)
    document_id: UUID
    run_id: UUID
    kind: Literal["parse", "index"]
    status: Literal["queued", "running", "completed", "failed", "paused"]
    indexed: int
    total: int
    error_message: str | None
    updated_at: datetime
