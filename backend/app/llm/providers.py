"""模型适配层：共用兼容接口，隔离三提供方参数差异；本层不重试或切换模型。"""

import json
from collections.abc import Generator, Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from time import perf_counter
from typing import Any, cast

import httpx
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError

from app.config import ProviderSettings, Settings
from app.llm.budget import ensure_input_budget
from app.llm.contracts import (
    ErrorCode,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ProviderError,
    ProviderName,
)
from app.services.usage import active_call, observe_model, token_usage, usage_call

_RESPONSE_ERRORS = (OpenAIError, httpx.HTTPError, ValueError, KeyError,
                    IndexError, TypeError, AttributeError)


class CheckedChatOpenAI(ChatOpenAI):
    """兼容模型类：在LangChain丢弃扩展字段前，识别提供方拒绝和业务错误。"""

    provider_name: ProviderName

    """流块转换方法：原始refusal在生成正文增量之前处理。"""

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict[str, Any], default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        if chunk.get("usage") and (call := active_call.get()) is not None:
            call.update(token_usage(chunk["usage"]))
            call["model"] = chunk.get("model") or call["model"]
        for choice in chunk.get("choices", []):
            if (choice.get("delta") or {}).get("refusal"):
                raise ProviderError(self.provider_name, ErrorCode.REFUSED)
        return super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )


"""模型构造函数：复用调用方HTTP连接，专用字段只交给对应提供方。"""

def build_chat_model(provider: ProviderName, config: ProviderSettings, http: httpx.Client,
                     *, max_output_tokens: int = 2048,
                     check_provider_response: bool = False) -> ChatOpenAI:
    if (config.api_key is None or not config.api_key.get_secret_value().strip()
            or config.base_url is None or not config.model):
        raise ProviderError(provider, ErrorCode.CONFIGURATION)
    extra: dict[str, Any] = {"max_tokens": max_output_tokens}
    if provider in ("deepseek", "kimi"):
        extra["thinking"] = {"type": "disabled"}
    elif provider == "qwen":
        extra["enable_thinking"] = False
    options: dict[str, Any] = dict(
        model=config.model, api_key=config.api_key, base_url=str(config.base_url),
        timeout=config.timeout_seconds, max_retries=0, http_client=http,
        http_socket_options=(), use_responses_api=False, extra_body=extra,
    )
    # 新适配器使用统一错误；旧DeepSeek业务方法仍保留自己的异常契约。
    if check_provider_response:
        return CheckedChatOpenAI(provider_name=provider, **options)
    return ChatOpenAI(**options)


"""异常归类函数：响应体不进入错误信息，HTTP错误优先按状态码分类。"""

def _error(provider: ProviderName, error: Exception, *, started: bool = False) -> ProviderError:
    retry_after = None
    if isinstance(error, (APITimeoutError, httpx.TimeoutException)):
        code = ErrorCode.TIMEOUT
    elif isinstance(error, (APIConnectionError, httpx.TransportError)):
        code = ErrorCode.NETWORK
    elif isinstance(error, APIStatusError):
        status = error.status_code
        code = (ErrorCode.AUTHENTICATION if status in (401, 403)
                else ErrorCode.QUOTA_EXCEEDED if status == 402
                else ErrorCode.RATE_LIMIT if status == 429
                else ErrorCode.UNAVAILABLE if status >= 500
                else ErrorCode.INVALID_REQUEST)
        header = error.response.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                try:
                    retry_after = max(0, (parsedate_to_datetime(header)
                                          - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
            if retry_after is not None and (not isfinite(retry_after) or retry_after < 0):
                retry_after = None
    else:
        code = ErrorCode.INVALID_OUTPUT
    return ProviderError(provider, code, started=started, retry_after_seconds=retry_after)


class ProviderAdapter:
    """文本提供方适配类：统一普通、JSON和流式结果，保留提供方身份。"""

    """初始化方法：只保存明确配置；构造连接不主动调用远程服务。"""

    def __init__(self, provider: ProviderName, config: ProviderSettings,
                 http: httpx.Client) -> None:
        self.provider, self.config = provider, config
        self.model = build_chat_model(provider, config, http, check_provider_response=True)

    """请求整理方法：JSON提示为三家共用，最后还要本地校验。"""

    def _prepare(self, request: ModelRequest) -> tuple[list[dict[str, str]], dict[str, Any]]:
        messages = list(request.messages)
        options: dict[str, Any] = {"extra_body": {
            **(self.model.extra_body or {}), "max_tokens": request.max_output_tokens,
        }}
        if request.json_object:
            messages.insert(0, {"role": "system", "content":
                "请仅返回一个有效JSON对象，不要输出Markdown代码围栏或额外解释。"})
            options["response_format"] = {"type": "json_object"}
        ensure_input_budget(messages)
        return messages, options

    """结果整理方法：只有正常结束的正文才算成功，JSON必须能解析为对象。"""

    def _result(self, request: ModelRequest, text: Any, finish: Any,
                usage: dict[str, Any] | None, started_at: float,
                response_model: str | None = None) -> ModelResult:
        if finish == "content_filter":
            raise ProviderError(self.provider, ErrorCode.REFUSED)
        if finish != "stop" or not isinstance(text, str) or not text.strip():
            raise ProviderError(self.provider, ErrorCode.INVALID_OUTPUT)
        if request.json_object and not isinstance(json.loads(text), dict):
            raise ProviderError(self.provider, ErrorCode.INVALID_OUTPUT)
        tokens = usage or {}
        return ModelResult(
            provider=self.provider, model=response_model or cast(str, self.config.model), text=text,
            input_tokens=tokens.get("input_tokens"), output_tokens=tokens.get("output_tokens"),
            total_tokens=tokens.get("total_tokens"), elapsed_seconds=perf_counter() - started_at,
        )

    """完整调用方法：发送一次请求并返回统一结果，schema修复由后续业务网关负责。"""

    def complete(self, request: ModelRequest) -> ModelResult:
        messages, options = self._prepare(request)
        started_at = perf_counter()
        try:
            with usage_call(self.provider, str(self.config.model),
                            str(self.config.base_url), "text"):
                response = self.model.invoke(messages, **options)
                observe_model(response)
                if response.additional_kwargs.get("refusal"):
                    raise ProviderError(self.provider, ErrorCode.REFUSED)
                return self._result(request, response.content,
                                    response.response_metadata.get("finish_reason"),
                                    cast(dict[str, Any] | None, response.usage_metadata),
                                    started_at,
                                    response.response_metadata.get("model_name"))
        except _RESPONSE_ERRORS as error:
            raise _error(self.provider, error) from None

    """原生工具调用方法：完整接收调用参数后才返回，保留工具编号与执行结果。"""

    def native(self, messages: list[BaseMessage], options: dict[str, Any]) -> ModelResult:
        ensure_input_budget(messages, tools=options.get("tools", ()))
        started_at = perf_counter()
        options = dict(options)
        options["extra_body"] = {**(self.model.extra_body or {}),
            "max_tokens": options.pop("max_tokens", 2048)}
        try:
            with usage_call(self.provider, str(self.config.model),
                            str(self.config.base_url), "tools"):
                response = self.model.invoke(messages, **options)
                observe_model(response)
                finish = response.response_metadata.get("finish_reason")
                if response.additional_kwargs.get("refusal") or finish == "content_filter":
                    raise ProviderError(self.provider, ErrorCode.REFUSED)
                if (not isinstance(response, AIMessage) or response.invalid_tool_calls
                        or finish not in ("stop", "tool_calls")
                        or (options.get("tool_choice") == "required" and not response.tool_calls)
                        or (finish == "tool_calls" and not response.tool_calls)
                        or (not response.tool_calls and not response.content)):
                    raise ProviderError(self.provider, ErrorCode.INVALID_OUTPUT)
                usage: dict[str, Any] = cast(dict[str, Any], response.usage_metadata or {})
                return ModelResult(provider=self.provider,
                    model=response.response_metadata.get("model_name") or str(self.config.model),
                    text=response.content if isinstance(response.content, str) else "",
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    native_message=response, elapsed_seconds=perf_counter() - started_at)
        except _RESPONSE_ERRORS as error:
            raise _error(self.provider, error) from None

    """流式调用方法：逐段返回正文，结尾校验完整性；中断后不在内部重新请求。"""

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        messages, options = self._prepare(request)
        started_at, sent = perf_counter(), False
        text, finish = "", None
        response_model = None
        usage: dict[str, Any] = {}
        with usage_call(self.provider, str(self.config.model),
                        str(self.config.base_url), "text"):
            chunks = self.model.stream(messages, stream_usage=True, **options)
            try:
                for chunk in chunks:
                    if (chunk.additional_kwargs.get("refusal")
                            or chunk.response_metadata.get("finish_reason") == "content_filter"):
                        raise ProviderError(self.provider, ErrorCode.REFUSED)
                    if not isinstance(chunk.content, str):
                        raise ProviderError(self.provider, ErrorCode.INVALID_OUTPUT)
                    if chunk.content:
                        text += chunk.content
                        sent = True
                        yield ModelStreamEvent(kind="delta", delta=chunk.content)
                    finish = chunk.response_metadata.get("finish_reason") or finish
                    response_model = chunk.response_metadata.get("model_name") or response_model
                    if chunk.usage_metadata:
                        for key in ("input_tokens", "output_tokens", "total_tokens"):
                            usage[key] = usage.get(key, 0) + chunk.usage_metadata[key]
                result = self._result(request, text, finish, usage, started_at, response_model)
                yield ModelStreamEvent(kind="completed", result=result)
            except ProviderError as error:
                error.started = sent
                raise
            except _RESPONSE_ERRORS as error:
                raise _error(self.provider, error, started=sent) from None
            finally:
                if isinstance(chunks, Generator):
                    chunks.close()


"""文本配置读取函数：集中展开旧DeepSeek变量与新增提供方设置，不创建连接。"""

def provider_settings(settings: Settings) -> dict[ProviderName, ProviderSettings]:
    configs: dict[ProviderName, ProviderSettings] = {
        "deepseek": ProviderSettings(
            api_key=settings.deepseek_api_key, model=settings.deepseek_model,
            base_url=settings.deepseek_base_url, timeout_seconds=settings.deepseek_timeout_seconds,
        ),
    }
    for name, config in settings.model_providers.items():
        configs[name] = config
    return configs


"""配置池构造函数：排除未授权或无密钥记录，已授权但不完整的配置明确报错。"""

def configured_adapters(settings: Settings, http: httpx.Client, *,
                        allowed: set[ProviderName] | None = None,
                        ) -> dict[ProviderName, ProviderAdapter]:
    configs = provider_settings(settings)
    return {name: ProviderAdapter(name, config, http) for name, config in configs.items()
            if (allowed is None or name in allowed)
            and config.api_key is not None and config.api_key.get_secret_value().strip()}
