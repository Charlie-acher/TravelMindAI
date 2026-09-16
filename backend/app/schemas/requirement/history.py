"""
持久化对话的HTTP格式：客户端只提交原话与版本，不再提交旧需求表。
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.requirement.chat import RequirementChatResponse


class SavedRequirementMessage(BaseModel):
    """一次发送的身份和依据；重试时保持message_id及expected_revision不变。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=6000)
    message_id: UUID
    expected_revision: int = Field(ge=0, strict=True)


class SavedRequirementTurn(BaseModel):
    """已提交的一轮；revision从1开始，response保留原接口的完整结果。"""

    message_id: UUID
    revision: int
    response: RequirementChatResponse


class RequirementHistory(BaseModel):
    """按顺序返回会话历史；空会话revision为0，方便直接发送第一条。"""

    session_id: UUID
    revision: int
    turns: list[SavedRequirementTurn]
