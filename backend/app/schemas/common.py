"""
数据格式层：定义各接口共用的错误响应格式。
"""

from pydantic import BaseModel, Field

"""错误信息类：保存错误代码、原因和补充说明。"""
class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] = Field(default_factory=dict)

"""错误响应类：返回错误信息和请求编号。"""
class ErrorResponse(BaseModel):
    error: ErrorInfo
    request_id: str
