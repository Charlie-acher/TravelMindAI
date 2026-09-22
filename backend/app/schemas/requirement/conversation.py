"""对话状态格式层：保存最近旅行话题、历史摘要和本轮统一理解。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.attachment import AttachmentUse
from app.schemas.requirement.update import RequirementUpdate
from app.schemas.transport import TransportQuery

TopicCity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
TopicPlace = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ConversationState(BaseModel):
    """最近旅行话题类：保存省略表达可接续的城市和地点。"""

    model_config = ConfigDict(extra="forbid")
    topic_cities: list[TopicCity] = Field(default_factory=list, max_length=4)
    topic_places: list[TopicPlace] = Field(default_factory=list, max_length=20)


class HistorySummary(BaseModel):
    """历史摘要类：保存摘要正文和已经完整处理到的轮次。"""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=2000)
    covered_revision: int = Field(ge=0)


class TurnUnderstanding(BaseModel):
    """本轮理解类：让需求、回答路线和检索范围共用一次语义判断。"""

    model_config = ConfigDict(extra="forbid")
    requirement_update: RequirementUpdate
    destination_action: Literal["keep", "set", "clear"] = "keep"
    topic_action: Literal["keep", "set", "clear"] = "keep"
    response_mode: Literal["chat", "map", "plan"] = "chat"
    query_cities: list[TopicCity] = Field(default_factory=list, max_length=4)
    retrieval_query: str = Field(default="", max_length=800)
    # 专类资料与综合攻略一起检索；无法确定类别时不限制，不能因此拒绝回答。
    retrieval_category: Literal["景点", "餐馆", "住宿"] | None = None
    conversation: ConversationState
    attachment_use: AttachmentUse | None = None
    transport_query: TransportQuery | None = None  # 独立交通查询，不因缺预算而阻断。
    transport_continue: bool = False  # 接续上一轮交通条件，未提及项由程序保留。
    transport_clear_fields: list[Literal[
        "origin", "destination", "departure_date", "return_date", "travelers",
        "earliest_departure", "latest_arrival", "return_earliest_departure",
        "return_latest_arrival", "preferences", "previous_day_earliest_departure",
    ]] = Field(default_factory=list)
