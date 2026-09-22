"""费用业务层：逐次记录外部调用，用官方费率估算原币费用，缺失用量不当作免费。"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.llm.contracts import ProviderError

VERSION = "official-2026-09-21"
# 每百万token原价；不扣赠送额度、折扣或订阅抵扣，不代表供应商实际扣款。
RATES = {
    "kimi-k2.6": ("6.5", "1.3", "27", "https://platform.kimi.com/"),
    "qwen3.8-max": ("12", "1.5", "36", "https://help.aliyun.com/zh/model-studio/qwen3-8-max"),
    "text-embedding-v4": ("0.5", "0.5", "0", "https://help.aliyun.com/zh/model-studio/text-embedding-v4"),
}
active_usage: ContextVar[tuple[str, Callable[[dict[str, Any]], None]] | None] = ContextVar(
    "usage_sink", default=None)
active_call: ContextVar[dict[str, Any] | None] = ContextVar("usage_call", default=None)
session_usage_context: ContextVar[dict[str, str]] = ContextVar("session_usage", default={})


"""会话记账函数：归属取自登录账号和已鉴权路由，不信任模型输出。"""

@contextmanager
def session_usage(user_id: UUID, session_id: UUID, message_id: UUID,
                  *, purpose: str = "conversation") -> Iterator[None]:
    token = session_usage_context.set({"user_id": str(user_id), "session_id": str(session_id),
                                      "message_id": str(message_id), "purpose": purpose})
    try:
        yield
    finally:
        session_usage_context.reset(token)


"""用途范围函数：标题和压缩摘要计入总量，但不替代对话的最近上下文。"""

@contextmanager
def usage_purpose(purpose: str) -> Iterator[None]:
    token = session_usage_context.set({**session_usage_context.get(), "purpose": purpose})
    try:
        yield
    finally:
        session_usage_context.reset(token)


"""用量整理函数：兼容LangChain与提供方原始字段，缓存输入是总输入的子集。"""

def token_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    cached = (usage.get("input_token_details") or {}).get("cache_read")
    if cached is None:
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens", usage.get("cached_tokens"))
    return {"input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
            "total_tokens": usage.get("total_tokens"), "cache_read_tokens": cached}


"""响应记录函数：校验业务输出之前保存用量，无效JSON也可能已经产生费用。"""

def observe_model(message: Any) -> None:
    call = active_call.get()
    if call is not None:
        values = token_usage(message.usage_metadata)
        raw = token_usage(message.response_metadata.get("token_usage"))
        values = {key: value if value is not None else raw[key] for key, value in values.items()}
        call.update(values)
        call["model"] = message.response_metadata.get("model_name") or call["model"]


"""定价函数：按明确官方端点和型号计算，时段、币种和来源跟随本次记录保存。"""

def price_call(call: dict[str, Any]) -> dict[str, Any]:
    cost: dict[str, Any] = {"amount": None, "currency": None, "status": "unknown",
                           "rate_version": VERSION, "source": None,
                           "reason": "费率或用量尚不完整"}
    model, endpoint = str(call["model"]).lower(), call["endpoint"]
    if call["kind"] == "ocr" and endpoint in {"127.0.0.1", "localhost", "::1"}:
        return {**cost, "status": "local", "reason": "本机OCR，无API账单；硬件电力未分摊"}
    if call["kind"] == "map":
        return {**cost, "reason": "已记录地图调用，当前账号的计费规则与免费额度尚未对账"}
    if call["kind"] == "search" and endpoint == "api.tavily.com" and call.get("units") is not None:
        return {**cost, "amount": str(Decimal(str(call["units"])) * Decimal("0.008")),
                "currency": "USD", "status": "estimated", "reason": "按量原价，未扣免费额度或套餐",
                "source": "https://docs.tavily.com/documentation/api-credits"}
    if not ((endpoint == "api.deepseek.com" and call["provider"] == "deepseek")
            or (endpoint == "api.moonshot.cn" and call["provider"] == "kimi")
            or (call["provider"] == "qwen" and (endpoint == "dashscope.aliyuncs.com"
                or re.fullmatch(r"ws-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", endpoint)))):
        return cost
    rates = RATES.get(model)
    if ((call["provider"] == "kimi" and model != "kimi-k2.6")
            or (call["provider"] == "deepseek" and model not in {
                "deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash"})
            or (call["provider"] == "qwen" and not model.startswith(("qwen", "text-embedding")))):
        return cost
    # 已核对的官方别名，不用前缀匹配给未来型号套旧价。
    if model in {"qwen3.8-max-0902", "qwen3.8-max-2026-09-02"}:
        rates = RATES["qwen3.8-max"]
    if model in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash"}:
        local = datetime.fromisoformat(call["started_at"]).astimezone(timezone(timedelta(hours=8)))
        if local.year != 2026:
            return {**cost, "reason": "DeepSeek节假日日历需更新"}
        holidays = (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
                    ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
                    ("10-01", "10-07"))
        holiday = any(start <= local.strftime("%m-%d") <= end for start, end in holidays)
        peak = (local.weekday() < 5 and not holiday
                and (9 <= local.hour < 12 or 14 <= local.hour < 18))
        rates = (*(("2", "0.04", "8") if peak else ("1", "0.02", "4")),
                 "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/")
        cost["period"] = "peak" if peak else "offpeak"
    incoming, outgoing, cached = (call.get(key) for key in (
        "input_tokens", "output_tokens", "cache_read_tokens"))
    if call["kind"] == "embedding":
        outgoing, cached = 0, 0
    if model in {"qwen3-vl-plus", "qwen3-vl-plus-2025-12-19", "qwen3-vl-plus-2025-09-23"}:
        if type(incoming) is int and 0 <= incoming <= 262144:
            tiers = ("1", "0.2", "10") if incoming <= 32768 else (
                ("1.5", "0.3", "15") if incoming <= 131072 else ("3", "0.6", "30"))
            rates = (*tiers, "https://help.aliyun.com/zh/model-studio/qwen3-vl-plus")
    if rates is None:
        return cost
    cost.update(currency="CNY", source=rates[3])
    if cached is None:
        return {**cost, "reason": "提供方未返回缓存用量，无法确定折扣后费用"}
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing, cached)):
        return cost
    incoming, outgoing, cached = cast(int, incoming), cast(int, outgoing), cast(int, cached)
    if cached > incoming:
        return {**cost, "reason": "缓存用量超过总输入"}
    amount = (Decimal(incoming - cached) * Decimal(rates[0])
              + Decimal(cached) * Decimal(rates[1])
              + Decimal(outgoing) * Decimal(rates[2])) / 1000000
    return {**cost, "amount": str(amount), "status": "estimated", "reason": "官方原价估算，未对账",
            "rates_per_million": {"input": rates[0], "cached": rates[1], "output": rates[2]}}


"""记账范围函数：请求和后台任务各自绑定保存函数，退出恢复，线程继承当前上下文。"""

@contextmanager
def usage_scope(request_id: str, save: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    token = active_usage.set((request_id, save))
    try:
        yield
    finally:
        active_usage.reset(token)


"""调用范围函数：每次尝试一条记录，成功、异常和流式中止都走同一收尾。"""

@contextmanager
def usage_call(provider: str, model: str, endpoint: str, kind: str) -> Iterator[dict[str, Any]]:
    call: dict[str, Any] = {"id": str(uuid4()), "provider": provider, "model": model,
        "endpoint": urlsplit(endpoint).hostname if "://" in endpoint else endpoint,
        "kind": kind, "started_at": datetime.now(timezone.utc).isoformat(), "success": False,
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
        "cache_read_tokens": None, "units": None}
    call["state"] = "running"
    call.update(session_usage_context.get())
    call["cost"] = {"amount": None, "currency": None, "status": "unknown",
                    "rate_version": VERSION, "source": None, "reason": "调用尚未结束"}
    scope = active_usage.get()
    if scope:
        call["request_id"] = scope[0]
        scope[1](dict(call))  # 先登记，进程意外退出时仍能看到未完成、费用待核对的调用。
    start = perf_counter()
    token = active_call.set(call)
    try:
        from app.services.chat.events import tool_progress
        names = {"ocr": "解析附件"}
        with tool_progress(kind, names[kind]) if kind in names else nullcontext():
            yield call
        call["success"] = True
    except Exception as error:
        # 仅保存类别与HTTP状态，不把异常正文中的账户、密钥或用户内容入账。
        call["error_type"] = type(error).__name__
        status = getattr(error, "status_code", None)
        call["http_status"] = status if type(status) is int else None
        if isinstance(error, ProviderError):
            call["error_category"] = error.code.value
        raise
    finally:
        active_call.reset(token)
        call["elapsed_seconds"] = round(perf_counter() - start, 3)
        call["state"] = "completed" if call["success"] else "failed"
        call["cost"] = price_call(call)
        if scope:
            scope[1](call)
        # 本轮统计和持久账本共用原始记录，不在网关重复计数。
        from app.services.chat.metrics import active_metrics
        if metrics := active_metrics.get():
            metrics.models.append(call)
