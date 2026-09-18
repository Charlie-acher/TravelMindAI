"""地图查询格式层：保存本轮MCP查询依据，百度坐标不混入高德地点卡片。"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class MapToolEvidence(BaseModel):
    """地图依据类：记录真实工具返回的文本及查询时间，不保存密钥。"""

    id: int = Field(ge=1)
    tool_name: str
    content: str = Field(max_length=48000)
    provider: Literal["baidu"] = "baidu"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MapToolAnswer(BaseModel):
    """地图回答类：回答关联本轮实际返回的依据，随会话一起保存和删除。"""

    reply: str
    source_ids: list[int]
    evidence: list[MapToolEvidence]
