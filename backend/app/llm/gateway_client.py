"""模型业务适配层：把文本网关接回原有字符串接口，原生工具模型共用本轮模型选择。"""

from functools import cached_property

import httpx
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.llm.client import ModelClientError, ModelOutputError, prepare_json_messages
from app.llm.contracts import (
    ErrorCode,
    ModelRequest,
    ProviderError,
    SelectableProvider,
    model_capability,
)
from app.llm.gateway import GatewayState, ModelGateway, ModelSelection
from app.llm.providers import configured_adapters, provider_settings
from app.llm.tool_model import GatewayChatModel
from app.services.chat.events import emit, event_sink


class GatewayClient:
    """网关业务客户端类：保留ModelClient协议，不让业务服务处理提供方连接细节。"""

    """初始化方法：请求持有HTTP连接，应用共享熔断与并发状态。"""

    def __init__(self, settings: Settings, http: httpx.Client, state: GatewayState,
                 *, max_output_tokens: int = 2048,
                 selected_provider: SelectableProvider | None = None,
                 selection: ModelSelection | None = None) -> None:
        self.settings, self.http, self.max_output_tokens = settings, http, max_output_tokens
        try:
            allowed = {name for capability, route in state.policy.routes.items()
                       for name in route}
            if selected_provider is not None or selection is not None:
                allowed = {name for name, config in provider_settings(settings).items()
                           if name in ("deepseek", "kimi", "qwen")
                           and config.base_url and config.model}
            self.gateway = ModelGateway(configured_adapters(settings, http, allowed=allowed), state,
                selection=selection or (ModelSelection(selected_provider)
                                        if selected_provider else None))
        except ProviderError as error:
            raise ModelClientError(str(error)) from None

    """原生模型属性：Agent工具调用与文本共用本轮选择及应用健康状态。"""

    @cached_property
    def model(self) -> BaseChatModel:
        return GatewayChatModel(gateway=self.gateway, max_output_tokens=self.max_output_tokens)

    """JSON调用方法：按当前能力路由；字段与引用校验继续由现有业务服务负责。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        request = ModelRequest(messages=prepare_json_messages(messages), json_object=True,
                               capability=model_capability.get(),
                               max_output_tokens=self.max_output_tokens)
        try:
            return self.gateway.complete(request).text
        except ProviderError as error:
            if error.code == ErrorCode.INVALID_OUTPUT:
                raise ModelOutputError("模型未返回完整JSON，请稍后重试") from None
            raise ModelClientError(str(error)) from None

    """自然回答方法：共用chat路由，流式正文沿用页面的reset/draft事件。"""

    def generate_text(self, messages: list[dict[str, str]]) -> str:
        request = ModelRequest(messages=messages, capability="chat",
                               max_output_tokens=self.max_output_tokens)
        emit("reset")
        try:
            if event_sink.get() is None:
                return self.gateway.complete(request).text
            content, sent = "", 0
            for event in self.gateway.stream(request):
                if event.kind == "delta":
                    content += event.delta
                    if len(content) - sent >= 24:
                        emit("draft", text=content)
                        sent = len(content)
                elif event.result is not None:
                    emit("draft", text=event.result.text)
                    return event.result.text
            raise ModelClientError("模型回答未完成，请稍后重试")
        except ProviderError as error:
            raise ModelClientError(str(error)) from None
