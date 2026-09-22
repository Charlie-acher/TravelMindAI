"""价格契约层：保存官方公布价与原文，查询时间不等于价格生效日期或库存确认。"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PublishedPrice(BaseModel):
    """公开价格类：未知金额保持空值，单人公布价不自动乘全团人数覆盖预算。"""

    place: str
    travel_date: date | None = None
    status: Literal[
        "published", "unavailable", "unconfigured", "error", "expired", "not_applicable"]
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: Literal["CNY"] = "CNY"
    unit: str = ""
    conditions: str = ""
    evidence: str = ""
    source_url: str | None = None
    source_title: str | None = None
    published_date: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: date | None = None
    valid_until: date | None = None
    date_confirmed: Literal[False] = False  # 公告和网页摘要不保证指定日期可购买。


class PriceExtraction(BaseModel):
    """待核对价格类：模型只从本轮官方片段提取一项基础票价，不提供自由生成的网址。"""

    source_id: int | None = None
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    unit: str = Field(default="", max_length=80)
    conditions: str = Field(default="", max_length=500)
    evidence: str = Field(default="", max_length=500)
    valid_from: date | None = None
    valid_until: date | None = None
