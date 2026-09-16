"""数据格式层：规定资料问答的问题、回答要点和可追溯的原文引用。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.schemas.document.search import SearchHit


class AnswerPoint(BaseModel):
    """回答要点类：一句或一段回答必须同时给出它使用的资料编号。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=1200)
    # strict阻止把True或字符串“1”悄悄转换成有效引用编号。
    source_ids: list[Annotated[int, Field(strict=True, ge=1, le=25)]] = Field(
        min_length=1, max_length=5,
    )


class MapQuery(BaseModel):
    """地图查询条件类：只查询本轮证据里出现的城市与景点，最多核对三个地点。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    city: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=80)
    source_id: Annotated[int, Field(strict=True, ge=1, le=25)]


class GeoPoint(BaseModel):
    """地图坐标类：保存高德坐标系和经纬度，拒绝越界或无穷大的数字。"""

    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"


class MapLookup(BaseModel):
    """地图结果类：保留查询时间、匹配状态与人均消费，不把消费字段冒充门票。"""

    city: str
    name: str
    status: Literal["found", "no_match", "ambiguous", "unconfigured", "error"]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: Literal["amap"] = "amap"
    poi_id: str | None = None
    matched_name: str | None = None  # 地图实际返回的名称，保留别名核对结果。
    match_kind: Literal["poi", "administrative"] | None = None  # 行政中心不是景点入口。
    address: str | None = None
    reference_cost: str | None = None  # 高德business.cost原含义为人均消费，不是票价。
    location: GeoPoint | None = None  # 景点中心；没有有效坐标时不能在地图上打点。
    entrance: GeoPoint | None = None  # 入口单独保存，避免把景点中心当导航入口。


class WebEvidence(BaseModel):
    """网页证据类：保存搜索返回的原文摘要和时间，不接收模型编造的网页。"""

    id: int = Field(ge=6, le=25)  # 1至5留给本地片段，其余用于首查和逐景点补查。
    title: str
    url: HttpUrl
    content: str = Field(min_length=1, max_length=2000)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_date: str | None = None  # 提供方返回的发布日期，不是本次查询时间。


class WebSearchAttempt(BaseModel):
    """定向补查记录类：保存每个景点的实际查询词和状态，方便核对缺项。"""

    query: str = Field(max_length=500)
    status: Literal["found", "empty", "unconfigured", "error", "not_requested"]


class WebSearchResult(BaseModel):
    """网页查询结果类：区分有结果、无结果、未启用和失败，并随回答保存。"""

    status: Literal["found", "empty", "unconfigured", "error", "not_requested"]
    items: list[WebEvidence] = Field(default_factory=list, max_length=20)
    supplemental_queries: list[WebSearchAttempt] = Field(default_factory=list, max_length=3)


class TicketInfo(BaseModel):
    """门票信息类：保存票种、适用说明和证据原句，公开信息只作为参考。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["unknown", "free", "paid", "partial"] = "unknown"
    summary: str = Field(default="门票暂无法确认", max_length=400)
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: Literal["CNY"] = "CNY"
    ticket_type: str | None = Field(default=None, max_length=80)
    applicable_date: str | None = Field(default=None, max_length=100)
    source_id: Annotated[int, Field(strict=True, ge=1, le=25)] | None = None
    quote: str | None = Field(default=None, min_length=4, max_length=400)
    basis: Literal["unknown", "reference"] = "unknown"

    """票据校验函数：有收费结论必须带原句；未知时清空价格，避免误导。"""

    @model_validator(mode="after")
    def check_evidence(self) -> Self:
        if self.status == "unknown":
            self.amount = None
            self.summary = "门票暂无法确认"
            self.basis = "unknown"
        else:
            if self.source_id is None or self.quote is None:
                raise ValueError("门票说明缺少原句依据")
            if self.status == "free" and self.amount not in (None, Decimal(0)):
                raise ValueError("免费不能同时填写收费金额")
            self.basis = "reference"
        return self


class AddressEvidence(BaseModel):
    """地址原文类：保存可逐字核对的地址及来源，不能替代地图匹配或坐标。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=5, max_length=250)
    source_id: Annotated[int, Field(strict=True, ge=1, le=25)]
    quote: str = Field(min_length=5, max_length=500)


class AttractionDraft(BaseModel):
    """景点内容类：模型只填写有依据的介绍和推荐理由，坐标由地图工具提供。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    city: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=80)
    source_ids: list[Annotated[int, Field(strict=True, ge=1, le=25)]] = Field(
        min_length=1, max_length=5,
    )
    description: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=300)
    ticket: TicketInfo = Field(default_factory=TicketInfo)
    address_evidence: AddressEvidence | None = None  # 地图无详细地址时使用可追溯的原文地址。


class AttractionCard(AttractionDraft):
    """景点卡片类：把介绍、门票和真实定位绑定，供聊天及后续行程地图共同使用。"""

    location: MapLookup


class AnswerContent(BaseModel):
    """回答正文类：模型输出与最终结果共用状态、要点和一致性检查。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "insufficient"]
    points: list[AnswerPoint] = Field(max_length=6)
    clarification: str | None = Field(default=None, min_length=1, max_length=400)

    """状态校验函数：有答案就必须有要点，资料不足时不得夹带无依据的回答。"""

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if (self.status == "answered") != bool(self.points):
            raise ValueError("回答状态与要点不一致")
        return self


class ModelAnswer(AnswerContent):
    """模型回答类：允许申请地图补查，不让模型提供文件名或伪造原文。"""

    map_queries: list[MapQuery] = Field(default_factory=list, max_length=3)
    attractions: list[AttractionDraft] = Field(default_factory=list, max_length=3)


class AnswerSource(BaseModel):
    """回答来源类：把本次引用编号关联到后端检索得到的原文和位置。"""

    id: int  # 本次问题内从1开始编号，不是数据库中的固定片段编号。
    hit: SearchHit  # 文件名、正文、页码和固定编号均由后端提供。


class AnswerResult(AnswerContent):
    """问答结果类：将校验后的回答与实际引用的原文一起交给前端。"""

    sources: list[AnswerSource]
    map_lookups: list[MapLookup] = Field(default_factory=list)  # 随旧有JSON快照保存，无需新表。
    attractions: list[AttractionCard] = Field(default_factory=list, max_length=3)
    web_search: WebSearchResult = Field(
        default_factory=lambda: WebSearchResult(status="not_requested"),
    )

    """旧快照读取函数：忽略旧版保存的空工具请求，保留回答和实际查询结果。"""

    @model_validator(mode="before")
    @classmethod
    def read_legacy_snapshot(cls, value: object) -> object:
        # 只兼容旧结果固定携带的空数组；其他未知字段仍由extra="forbid"拒绝。
        if isinstance(value, dict) and value.get("map_queries") == []:
            return {key: item for key, item in value.items() if key != "map_queries"}
        return value
