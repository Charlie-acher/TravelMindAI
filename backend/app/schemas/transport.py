"""交通格式层：保存本轮出行查询和官方结果，不把票价查询改成旅行规划。"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document.answer import WebSearchResult


class TransportQuery(BaseModel):
    """交通查询类：由已有理解模型结合用户原话与历史补全，未知条件留空。"""

    origin: str | None = Field(default=None, max_length=80)
    destination: str | None = Field(default=None, max_length=80)
    departure_date: date | None = None
    return_date: date | None = None
    travelers: int | None = Field(default=None, ge=1, le=100)
    modes: list[Literal["rail", "flight"]] = Field(
        default=["rail", "flight"], min_length=1, max_length=2)
    earliest_departure: time | None = None
    # 额外查询原出发日前一晚，不替换原日期，也不限制原日期的出发时间。
    previous_day_earliest_departure: time | None = None
    latest_arrival: time | None = None
    return_earliest_departure: time | None = None
    return_latest_arrival: time | None = None
    preferences: str = Field(default="", max_length=300)


class RailOption(BaseModel):
    """铁路候选类：来自同次官方余票与票价响应，金额为单程成人二等座人民币。"""

    code: str
    from_station: str
    to_station: str
    departure: datetime
    arrival: datetime
    duration_minutes: int
    seats: str
    price: Decimal | None = None


class RailResult(BaseModel):
    """铁路结果类：区分未开售、筛选为空和查询失败，保留实际查询时间。"""

    status: Literal["found", "empty", "unavailable", "not_on_sale", "past_date", "unknown_station"]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_url: str = "https://kyfw.12306.cn/otn/leftTicket/init"
    options: list[RailOption] = Field(default_factory=list)
    note: str = ""


class TransportLeg(BaseModel):
    """单程结果类：往返各自保存路线、日期、铁路结果和航司公开原文。"""

    origin: str
    destination: str
    date: date
    rail: RailResult | None = None
    flights: WebSearchResult = Field(
        default_factory=lambda: WebSearchResult(status="not_requested"))


class TransportResult(BaseModel):
    """交通快照类：随本轮对话保存，不覆盖原行程或将成人参考价记为全团实际支出。"""

    query: TransportQuery
    legs: list[TransportLeg] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
