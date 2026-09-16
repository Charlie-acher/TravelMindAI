"""
数据格式层：定义旅行需求的修改、清空和删除格式。
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.requirement.base import NonBlankText, TravelRequestExtraction

# 限定能被修改的业务字段，不允许模型删除intent或任意内部字段。
ScalarField = Literal[
    "destination", "origin", "start_date", "end_date", "days", "travelers", "total_budget", "pace"
]
ListField = Literal[
    "interests", "dietary", "lodging_preferences", "hard_constraints", "excluded_items"
]


class RequirementUpdate(TravelRequestExtraction):
    """需求更新类：保存本次要修改、清空或删除的内容。"""

    clear_fields: list[ScalarField | ListField] = Field(
        default_factory=list, description="用户明确要求清空的字段名；未提及则[]"
    )
    remove_items: dict[ListField, list[NonBlankText]] = Field(
        default_factory=dict,
        description="按字段删除的旧列表条目，必须复制历史里的准确原文；未提及则{}",
    )


    """修改校验函数：检查新增、清空和删除操作是否冲突。"""
    @model_validator(mode="after")
    def validate_edit_operations(self) -> Self:
        for field in self.clear_fields:
            if getattr(self, field) not in (None, []):
                raise ValueError(f"{field}不能同时清空和赋值")
            if field in self.remove_items:
                raise ValueError(f"{field}不能同时清空整栏和删除个别条目")
        for field, removed in self.remove_items.items():
            if set(removed).intersection(getattr(self, field)):
                raise ValueError(f"{field}中的同一条目不能同时新增和删除")
        return self
