"""模型契约层：统一文本适配器的输入、输出和安全错误类别，供后续网关使用。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

ProviderName = Literal["deepseek", "kimi", "qwen"]
SelectableProvider = ProviderName
Capability = Literal["extract", "research", "plan", "review", "chat"]
model_capability: ContextVar[Capability] = ContextVar("model_capability", default="extract")


"""能力范围函数：标记当前文本任务，退出时恢复，避免并发会话相互串路由。"""

@contextmanager
def capability_scope(capability: Capability) -> Iterator[None]:
    token = model_capability.set(capability)
    try:
        yield
    finally:
        model_capability.reset(token)


class ErrorCode(StrEnum):
    """错误类别枚举：区分配置、临时故障、请求拒绝和不完整输出。"""

    CONFIGURATION = "configuration"
    TIMEOUT = "timeout"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    QUOTA_EXCEEDED = "quota_exceeded"
    INVALID_REQUEST = "invalid_request"
    REFUSED = "refused"
    INVALID_OUTPUT = "invalid_output"


class ProviderError(RuntimeError):
    """提供方异常类：只携带安全分类，不保存密钥、提示词和服务端错误正文。"""

    """初始化方法：started表示正文已经发出，后续网关不能换模型续写。"""

    def __init__(self, provider: ProviderName, code: ErrorCode, *, started: bool = False,
                 retry_after_seconds: float | None = None) -> None:
        self.provider, self.code, self.started = provider, code, started
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"模型调用失败（{provider}/{code}）")


class ModelRequest(BaseModel):
    """模型请求类：本步仅接收文本消息，JSON请求要求返回一个对象。"""

    messages: list[dict[str, str]]
    capability: Capability = "chat"
    json_object: bool = False
    max_output_tokens: int = Field(default=2048, ge=1)


class ModelResult(BaseModel):
    """模型结果类：保留正文与实际提供方信息，缺失的用量保持为空。"""

    provider: ProviderName
    model: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    elapsed_seconds: float
    native_message: AIMessage | None = Field(default=None, exclude=True)


class ModelStreamEvent(BaseModel):
    """模型流事件类：逐段正文与最后的完整结果分开，推理内容不作为正文。"""

    kind: Literal["delta", "completed"]
    delta: str = ""
    result: ModelResult | None = None
