"""模型网关层：按能力选择文本提供方，限制并发、重试和切换，保存进程内健康统计。"""

import logging
import random
import time
from collections import deque
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from math import ceil
from threading import Lock
from typing import Any

from app.config import GatewaySettings
from app.llm.client import ModelClientError
from app.llm.contracts import (
    ErrorCode,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ProviderError,
    ProviderName,
    SelectableProvider,
)
from app.llm.providers import ProviderAdapter
from app.services.chat.events import check_cancelled, emit

RETRYABLE = {ErrorCode.TIMEOUT, ErrorCode.NETWORK, ErrorCode.RATE_LIMIT,
             ErrorCode.UNAVAILABLE, ErrorCode.INVALID_OUTPUT}
DISABLED = {ErrorCode.AUTHENTICATION, ErrorCode.QUOTA_EXCEEDED, ErrorCode.CONFIGURATION}
logger = logging.getLogger(__name__)


@dataclass
class ModelSelection:
    """本轮选择类：主回复与附件共享首选、已切换的备用和实际使用记录。"""

    selected: SelectableProvider
    active: ProviderName | None = None
    used: list[ProviderName] = field(default_factory=list)


@dataclass
class ProviderHealth:
    """提供方健康类：只保存短期数值，不保存用户消息或远程错误正文。"""

    in_flight: int = 0
    failures: int = 0
    opened_until: float = 0
    probing: bool = False
    disabled: bool = False
    calls: int = 0
    retries: int = 0
    samples: deque[tuple[bool, float]] = field(default_factory=deque)


class GatewayState:
    """共享网关状态类：一个应用进程共用一份，短锁保护选择与名额释放。"""

    """初始化方法：HTTP连接不放在共享状态中，测试可注入时钟和抽签函数。"""

    def __init__(self, policy: GatewaySettings, *, clock: Callable[[], float] = time.monotonic,
                 random_value: Callable[[], float] = random.random) -> None:
        self.policy, self.clock, self.random = policy, clock, random_value
        self.lock = Lock()
        self.health: dict[ProviderName, ProviderHealth] = {}
        self.fallback_count = 0

    """名额获取方法：过滤未配置、满载和打开的熔断，再按权重与近期健康抽签。"""

    def acquire(self, route: dict[ProviderName, float], available: set[ProviderName],
                excluded: set[ProviderName], *, ordered: bool = False) -> ProviderName | None:
        with self.lock:
            choices: list[tuple[ProviderName, float]] = []
            now = self.clock()
            for name, weight in route.items():
                if name not in available or name in excluded:
                    continue
                health = self.health.setdefault(name, ProviderHealth(
                    samples=deque(maxlen=self.policy.window_size),
                ))
                if (health.disabled or health.in_flight >= self.policy.max_concurrent
                        or health.probing or health.opened_until > now
                        or (health.opened_until and health.in_flight)):
                    continue
                count = len(health.samples)
                success = (1 + sum(ok for ok, _ in health.samples)) / (1 + count)
                latency = sum(elapsed for _, elapsed in health.samples) / max(count, 1)
                choices.append((name, weight * success / (1 + latency)))
            if not choices:
                return None
            draw = self.random() * sum(weight for _, weight in choices)
            chosen = choices[-1][0]
            if ordered:
                chosen = choices[0][0]
            for name, weight in choices:
                if ordered:
                    break
                draw -= weight
                if draw <= 0:
                    chosen = name
                    break
            health = self.health[chosen]
            health.in_flight += 1
            if health.opened_until:
                health.probing = True
            return chosen

    """名额释放方法：一次提供方执行含内部重试；最终结果计入窗口与熔断。"""

    def release(self, name: ProviderName, success: bool | None, code: ErrorCode | None,
                elapsed: float) -> None:
        with self.lock:
            health = self.health[name]
            health.in_flight -= 1
            probing, health.probing = health.probing, False
            if success is None:
                if probing:
                    health.opened_until = self.clock() + self.policy.cooldown_seconds
                return  # 用户关闭流不计远程故障。
            health.calls += 1
            health.samples.append((success, elapsed))
            # 半开前等待旧名额清零，所以probing只属于本次释放者。
            # 打开之前已入场的旧请求仍计指标，但不能提前关掉本轮熔断。
            if health.opened_until and not probing and code not in DISABLED:
                return
            if success or code not in RETRYABLE | DISABLED:
                health.failures, health.opened_until = 0, 0
            elif code in DISABLED:
                health.disabled = True
            else:
                health.failures += 1
                if probing or health.failures >= self.policy.failure_threshold:
                    health.opened_until = self.clock() + self.policy.cooldown_seconds

    """重试计数方法：记录真实发起的额外尝试，不包含SDK自动重试。"""

    def retry(self, name: ProviderName) -> None:
        with self.lock:
            self.health[name].retries += 1

    """降级记录方法：只记录稳定别名与错误类别，不写密钥、地址或正文。"""

    def fallback(self, previous: ProviderName, target: ProviderName, reason: ErrorCode) -> None:
        with self.lock:
            self.fallback_count += 1
        emit("fallback", from_alias=previous, to_alias=target, reason_category=reason.value)
        logger.info("model_fallback from=%s to=%s reason=%s", previous, target, reason.value)

    """统计快照方法：管理员可见近窗口成功率、P95及当前熔断/并发状态。"""

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            providers = {}
            for name, health in self.health.items():
                times = sorted(elapsed for _, elapsed in health.samples)
                providers[name] = {
                    "state": "disabled" if health.disabled else "half_open" if health.probing
                        else "open" if health.opened_until else "closed",
                    "probe_ready": bool(not health.disabled and not health.in_flight
                                        and health.opened_until
                                        and health.opened_until <= self.clock()),
                    "in_flight": health.in_flight, "calls": health.calls, "retries": health.retries,
                    "success_rate": (sum(ok for ok, _ in health.samples) / len(times)
                                     if times else None),
                    "p95_seconds": times[ceil(len(times) * .95) - 1] if times else None,
                    "window_count": len(times),
                }
            return {"providers": providers, "fallback_count": self.fallback_count}


class ModelGateway:
    """文本模型网关类：一次任务只在明确配置的能力候选中有限重试和降级。"""

    """初始化方法：当前请求持有适配器，健康与并发名额使用应用共享状态。"""

    def __init__(self, adapters: dict[ProviderName, ProviderAdapter], state: GatewayState,
                 *, sleep: Callable[[float], None] = time.sleep,
                 selection: ModelSelection | None = None) -> None:
        self.adapters, self.state, self.sleep = adapters, state, sleep
        self.selection = selection

    """实际模型属性：返回本轮已成功使用的提供方，不能用首选冒充实际调用。"""

    @property
    def used_providers(self) -> list[ProviderName]:
        return list(self.selection.used) if self.selection else []

    """完整调用方法：共用执行流程，返回唯一完成结果。"""

    def complete(self, request: ModelRequest) -> ModelResult:
        for event in self._events(request, streaming=False):
            if event.result is not None:
                return event.result
        raise ModelClientError("模型没有返回完整结果，请稍后重试")

    """流调用方法：首段正文之后失败即停止，不跨提供方拼接。"""

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        yield from self._events(request, streaming=True)

    """执行方法：每家最多两次尝试，名额在成功、失败或流被关闭时都释放。"""

    def _events(self, request: ModelRequest, *, streaming: bool,
                complete: Callable[[ProviderAdapter], ModelResult] | None = None,
                ) -> Iterator[ModelStreamEvent]:
        route = self.state.policy.routes.get(request.capability, {})
        if self.selection:
            preferred = self.selection.active or self.selection.selected
            route = dict.fromkeys([preferred, "deepseek", "kimi", "qwen"], 1.0)
        excluded: set[ProviderName] = set()
        previous: ProviderName | None = None
        last_error: ProviderError | None = None
        while name := self.state.acquire(route, set(self.adapters), excluded,
                                         ordered=self.selection is not None):
            excluded.add(name)
            start, outcome, code = self.state.clock(), None, None
            result: ModelResult | None = None
            sent = False
            try:
                check_cancelled()
                if previous is not None and last_error is not None:
                    self.state.fallback(previous, name, last_error.code)
                elif self.selection and name != preferred:
                    self.state.fallback(preferred, name, ErrorCode.UNAVAILABLE)
                current = request
                for attempt in range(2):
                    outcome, code = None, None
                    result = None
                    try:
                        if streaming:
                            events = self.adapters[name].stream(current)
                            try:
                                for event in events:
                                    check_cancelled()
                                    if event.kind == "delta":
                                        sent = sent or bool(event.delta)
                                        yield event
                                    else:
                                        result = event.result
                            finally:
                                if isinstance(events, Generator):
                                    events.close()
                            if result is None:
                                raise ProviderError(name, ErrorCode.INVALID_OUTPUT, started=sent)
                        else:
                            result = (complete(self.adapters[name]) if complete else
                                      self.adapters[name].complete(current))
                        check_cancelled()
                        outcome = True
                        break
                    except ProviderError as error:
                        outcome, code, last_error = False, error.code, error
                        error.started = error.started or sent
                        if error.started or error.code not in RETRYABLE | DISABLED:
                            raise
                        if attempt or error.code not in RETRYABLE:
                            break
                        if self.selection and error.code != ErrorCode.INVALID_OUTPUT:
                            break  # 显式选择模式下先换备用，不等待同一家完整重试。
                        delay = error.retry_after_seconds
                        if delay is not None and delay > self.state.policy.max_retry_after_seconds:
                            break
                        if error.code == ErrorCode.INVALID_OUTPUT:
                            current = request.model_copy(update={"messages": [*request.messages,
                                {"role": "user", "content":
                                 "上一轮响应格式不完整，请按原要求重新输出。"
                                 + ("仅输出有效JSON对象。" if request.json_object else "")}]})
                        backoff = self.state.policy.retry_delay_seconds * (1 + self.state.random())
                        self.sleep(delay if delay is not None else backoff)
                        self.state.retry(name)
            finally:
                self.state.release(name, outcome, code, self.state.clock() - start)
            if result is not None and outcome:
                if self.selection:
                    self.selection.active = name
                    if name not in self.selection.used:
                        self.selection.used.append(name)
                yield ModelStreamEvent(kind="completed", result=result)
                return
            previous = name
        if last_error is not None:
            raise last_error
        raise ModelClientError("当前能力没有可用文本模型（未配置、繁忙或熔断），请稍后重试")
