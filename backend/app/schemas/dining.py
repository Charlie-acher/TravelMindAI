"""数据格式层：保存高德附近餐厅原值、查询锚点和澄清问题，供聊天历史复用。"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document.answer import GeoPoint, MapLookup


class DiningItem(BaseModel):
    """餐厅结果类：只保存真实地图返回且评分至少四分的餐饮地点。"""

    poi_id: str
    name: str
    address: str | None = None
    location: GeoPoint
    rating: float = Field(ge=4, le=5, allow_inf_nan=False)
    distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost: str | None = None  # 高德原始人均消费，不承诺实际账单金额。


class DiningResult(BaseModel):
    """附近餐饮结果类：区分无餐厅、缺评分、工具失败和需要用户明确地点。"""

    status: Literal[
        "found", "empty", "ratings_unavailable", "unconfigured", "error", "needs_clarification",
    ]
    items: list[DiningItem] = Field(default_factory=list, max_length=5)
    anchor: MapLookup | None = None
    radius_m: int = Field(default=2000, ge=1, le=50000)  # 圆形查询半径，不是步行距离。
    preference: str | None = None
    clarification: str | None = None
    rating_missing_count: int = Field(default=0, ge=0)
    provider: Literal["amap"] = "amap"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
