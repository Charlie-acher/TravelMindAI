"""原生模型适配层：把Agent的完整工具消息交给共用网关，沿用选择与熔断。"""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from app.llm.client import ModelClientError
from app.llm.contracts import ModelRequest, ProviderError
from app.llm.gateway import ModelGateway


class GatewayChatModel(BaseChatModel):
    """工具模型类：只切换尚未交付给Agent的完整模型请求，不执行或重放业务工具。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    gateway: ModelGateway = Field(exclude=True)
    max_output_tokens: int = 2048

    """模型类型属性：框架追踪时使用稳定类型，不暴露提供方凭据。"""

    @property
    def _llm_type(self) -> str:
        return "travelmind-gateway"

    """工具绑定方法：保留框架传入的工具选择参数，调用时才决定实际提供方。"""

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        options = {"tools": [convert_to_openai_tool(tool) for tool in tools], **kwargs}
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        return self.bind(**options)

    """完整生成方法：保留AIMessage中的工具编号，外层Agent收到后才执行工具。"""

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        options = {**kwargs, "max_tokens": self.max_output_tokens}
        if stop is not None:
            options["stop"] = stop
        request = ModelRequest(messages=[], capability="plan")
        try:
            for event in self.gateway._events(request, streaming=False,
                    complete=lambda adapter: adapter.native(messages, options)):
                if event.result and event.result.native_message is not None:
                    return ChatResult(generations=[ChatGeneration(
                        message=event.result.native_message)])
        except ProviderError as error:
            raise ModelClientError(str(error)) from None
        raise ModelClientError("工具模型没有返回完整结果，请稍后重试")
