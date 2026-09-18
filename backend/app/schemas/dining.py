"""数据格式层：保存附近餐饮或住宿原值和查询条件，沿用历史dining字段。"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document.answer import GeoPoint, MapLookup


class DiningItem(BaseModel):
    """商户结果类：只保存地图返回且评分至少四分的餐馆或住宿地点。"""

    poi_id: str
    name: str
    address: str | None = None
    location: GeoPoint
    rating: float = Field(ge=4, le=5, allow_inf_nan=False)
    distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost: str | None = None  # 地图原始参考消费，不等于实际账单或酒店每晚房价。


class DiningResult(BaseModel):
    """周边结果类：沿用餐饮历史字段，新增类别和消费条件均有兼容默认值。"""

    status: Literal[
        "found", "empty", "ratings_unavailable", "unconfigured", "error", "needs_clarification",
    ]
    items: list[DiningItem] = Field(default_factory=list, max_length=5)
    anchor: MapLookup | None = None
    radius_m: int = Field(default=2000, ge=1, le=50000)  # 圆形查询半径，不是步行距离。
    preference: str | None = None
    clarification: str | None = None
    rating_missing_count: int = Field(default=0, ge=0)
    category: Literal["dining", "lodging"] = "dining"
    max_cost: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    provider: Literal["amap", "baidu"] = "amap"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
