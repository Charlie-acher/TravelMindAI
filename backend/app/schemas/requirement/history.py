"""
持久化对话的HTTP格式：客户端只提交原话与版本，不再提交旧需求表。
"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.requirement.chat import RequirementChatResponse


class SavedRequirementMessage(BaseModel):
    """一次发送的身份和依据；重试时保持message_id及expected_revision不变。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(max_length=6000)
    message_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=3)

    """消息内容校验函数：只发附件时保留空原话，文字和附件不能同时为空。"""

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if not self.message and not self.attachment_ids:
            raise ValueError("请填写消息或选择附件")
        return self

    """附件编号校验函数：一次消息不得重复引用同一原件。"""

    @field_validator("attachment_ids")
    @classmethod
    def unique_attachments(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("同一附件只能选择一次")
        return value


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
