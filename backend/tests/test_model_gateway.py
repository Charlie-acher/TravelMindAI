"""网关测试层：用确定性时钟和调用计数验证重试、切换、熔断与流式边界。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from app.config import GatewaySettings
from app.llm.client import ModelClientError
from app.llm.contracts import ErrorCode, ModelRequest, ModelResult, ModelStreamEvent, ProviderError
from app.llm.gateway import GatewayState, ModelGateway


class FakeAdapter:
    """测试适配器类：按队列返回结果或异常，并记录真实尝试次数。"""

    """初始化方法：每个队列元素代表一次远程请求结果。"""

    def __init__(self, provider, outcomes):
        self.provider, self.outcomes, self.calls = provider, list(outcomes), 0

    """完整请求方法：出队结果，使重复请求与跨模型切换可直接检查。"""

    def complete(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResult(provider=self.provider, model="test", text=outcome,
                           elapsed_seconds=0.1)

    """流请求方法：允许正文已经发出后发生异常。"""

    def stream(self, request):
        self.calls += 1
        for outcome in self.outcomes.pop(0):
            if isinstance(outcome, Exception):
                raise outcome
            yield outcome


"""网关准备函数：固定抽签值，确保先选配置中的第一家。"""

def gateway(first, second, **policy):
    config = GatewaySettings(routes={"chat": {"deepseek": 1, "qwen": 1}}, **policy)
    state = GatewayState(config, random_value=lambda: 0)
    return ModelGateway({"deepseek": first, "qwen": second}, state, sleep=lambda seconds: None)


"""主模型仅重试一次，之后进入明确配置的备用模型。"""

@pytest.mark.parametrize("code", [ErrorCode.TIMEOUT, ErrorCode.NETWORK,
                                  ErrorCode.RATE_LIMIT, ErrorCode.UNAVAILABLE])
def test_retry_then_fallback(code):
    first = FakeAdapter("deepseek", [ProviderError("deepseek", code)] * 2)
    second = FakeAdapter("qwen", ["备用回答"])
    gw = gateway(first, second)
    assert gw.complete(ModelRequest(messages=[])).text == "备用回答"
    assert (first.calls, second.calls) == (2, 1)
    status = gw.state.snapshot()
    assert status["fallback_count"] == 1
    assert status["providers"]["deepseek"]["in_flight"] == 0


"""用户请求拒绝和非法参数不能通过换提供方规避。"""

@pytest.mark.parametrize("code", [ErrorCode.REFUSED, ErrorCode.INVALID_REQUEST])
def test_terminal_error_never_retries_or_switches(code):
    first = FakeAdapter("deepseek", [ProviderError("deepseek", code)])
    second = FakeAdapter("qwen", [])
    gw = gateway(first, second)
    with pytest.raises(ProviderError):
        gw.complete(ModelRequest(messages=[]))
    assert (first.calls, second.calls) == (1, 0)


"""鉴权失败禁用提供方，后续请求不再重复撞同一密钥错误。"""

def test_authentication_disables_provider_across_requests():
    first = FakeAdapter("deepseek", [ProviderError("deepseek", ErrorCode.AUTHENTICATION)])
    second = FakeAdapter("qwen", ["one", "two"])
    gw = gateway(first, second)
    gw.complete(ModelRequest(messages=[]))
    assert gw.complete(ModelRequest(messages=[])).text == "two"
    assert first.calls == 1
    assert gw.state.snapshot()["providers"]["deepseek"]["state"] == "disabled"


"""正文已发后，即使适配器未标started，网关也必须停止切换与重试。"""

def test_stream_never_switches_after_text():
    first = FakeAdapter("deepseek", [[ModelStreamEvent(kind="delta", delta="一半"),
                                     ProviderError("deepseek", ErrorCode.NETWORK)]])
    second = FakeAdapter("qwen", [])
    gw = gateway(first, second)
    stream = gw.stream(ModelRequest(messages=[]))
    assert next(stream).delta == "一半"
    with pytest.raises(ProviderError) as caught:
        next(stream)
    assert caught.value.started and first.calls == 1 and second.calls == 0
    assert gw.state.snapshot()["providers"]["deepseek"]["in_flight"] == 0


"""主动关闭草稿迭代器不会泄漏并发名额，也不计服务失败。"""

def test_close_stream_releases_slot():
    first = FakeAdapter("deepseek", [[ModelStreamEvent(kind="delta", delta="草稿")]])
    gw = gateway(first, FakeAdapter("qwen", []))
    stream = gw.stream(ModelRequest(messages=[]))
    next(stream)
    stream.close()
    status = gw.state.snapshot()["providers"]["deepseek"]
    assert status["in_flight"] == 0 and status["calls"] == 0


"""冷却后的半开探测只允许一个线程进入，成功后重新关闭熔断。"""

def test_circuit_half_open_probe_is_exclusive():
    now = [0.0]
    state = GatewayState(GatewaySettings(failure_threshold=1, cooldown_seconds=30),
                         clock=lambda: now[0], random_value=lambda: 0)
    route = {"deepseek": 1}
    assert state.acquire(route, {"deepseek"}, set()) == "deepseek"
    state.release("deepseek", False, ErrorCode.TIMEOUT, 1)
    assert state.acquire(route, {"deepseek"}, set()) is None
    now[0] = 31
    barrier = Barrier(3)

    """同时竞争函数：用屏障代替休眠，确保两个线程同时尝试半开。"""

    def compete():
        barrier.wait()
        return state.acquire(route, {"deepseek"}, set())

    with ThreadPoolExecutor(2) as executor:
        jobs = [executor.submit(compete) for _ in range(2)]
        barrier.wait()
        assert [job.result() for job in jobs].count("deepseek") == 1
    state.release("deepseek", True, None, 0.2)
    assert state.snapshot()["providers"]["deepseek"]["state"] == "closed"


"""达到并发上限时立刻选其他候选；不能超过限制或无限排队。"""

def test_concurrency_limit_and_unconfigured_route():
    state = GatewayState(GatewaySettings(max_concurrent=1), random_value=lambda: 0)
    route = {"deepseek": 1, "qwen": 1}
    assert state.acquire(route, {"deepseek", "qwen"}, set()) == "deepseek"
    assert state.acquire(route, {"deepseek", "qwen"}, set()) == "qwen"
    assert state.acquire(route, {"deepseek", "qwen"}, set()) is None
    with pytest.raises(ModelClientError):
        ModelGateway({}, state).complete(ModelRequest(messages=[]))


"""尊重短Retry-After，长等待直接切换，不能截短后提前打同一家。"""

@pytest.mark.parametrize("delay,attempts,waited", [(2, 2, [2]), (60, 1, [])])
def test_retry_after_wait_is_bounded(delay, attempts, waited):
    first = FakeAdapter("deepseek", [ProviderError(
        "deepseek", ErrorCode.RATE_LIMIT, retry_after_seconds=delay,
    )] * 2)
    gw = gateway(first, FakeAdapter("qwen", ["备用"]))
    waits = []
    gw.sleep = waits.append
    assert gw.complete(ModelRequest(messages=[])).text == "备用"
    assert first.calls == attempts and waits == waited


"""连续失败跨客户端共用，窗口只保留最近N次，P95不会被旧样本无限累积。"""

def test_shared_state_window_and_failed_probe():
    now = [0.0]
    policy = GatewaySettings(failure_threshold=2, window_size=2)
    state = GatewayState(policy, clock=lambda: now[0])
    for duration in (1, 3):
        assert state.acquire({"deepseek": 1}, {"deepseek"}, set()) == "deepseek"
        state.release("deepseek", False, ErrorCode.TIMEOUT, duration)
    with pytest.raises(ModelClientError):
        ModelGateway({"deepseek": FakeAdapter("deepseek", [])}, state).complete(
            ModelRequest(messages=[]),
        )
    now[0] = 31
    assert state.acquire({"deepseek": 1}, {"deepseek"}, set()) == "deepseek"
    state.release("deepseek", False, ErrorCode.TIMEOUT, 2)
    status = state.snapshot()["providers"]["deepseek"]
    assert status["state"] == "open" and status["window_count"] == 2
    assert status["p95_seconds"] == 3 and status["success_rate"] == 0


"""权重与实测延迟参与抽签；未授权的能力不能沿用其他能力的候选。"""

def test_weight_latency_and_capability_boundaries():
    state = GatewayState(GatewaySettings(), random_value=lambda: .3)
    route = {"deepseek": 3, "qwen": 1}
    assert state.acquire(route, {"deepseek", "qwen"}, set()) == "deepseek"
    state.release("deepseek", True, None, 20)
    assert state.acquire(route, {"deepseek", "qwen"}, set()) == "qwen"
    with pytest.raises(ModelClientError):
        gateway(FakeAdapter("deepseek", []), FakeAdapter("qwen", [])).complete(
            ModelRequest(messages=[], capability="review"),
        )


"""路由权重与并发参数必须有效，不能因零权重、无限等待或错拼提供方绕过边界。"""

@pytest.mark.parametrize("changes", [
    {"routes": {"chat": {"deepseek": 0}}}, {"routes": {"chat": {"deepseek": float("nan")}}},
    {"routes": {"chat": {"unknown": 1}}}, {"max_concurrent": 0},
    {"cooldown_seconds": float("inf")},
])
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValidationError):
        GatewaySettings(**changes)


"""熔断前的旧请求不能清除新探测状态；旧名额清零后才允许唯一半开。"""

def test_old_inflight_completion_cannot_close_new_circuit():
    now = [0.0]
    state = GatewayState(GatewaySettings(failure_threshold=1), clock=lambda: now[0])
    route = {"deepseek": 1}
    state.acquire(route, {"deepseek"}, set())  # 旧请求A。
    state.acquire(route, {"deepseek"}, set())  # 旧请求B。
    state.release("deepseek", False, ErrorCode.TIMEOUT, 1)
    now[0] = 31
    assert state.acquire(route, {"deepseek"}, set()) is None
    state.release("deepseek", True, None, 31)
    assert state.snapshot()["providers"]["deepseek"]["state"] == "open"
    assert state.acquire(route, {"deepseek"}, set()) == "deepseek"
    assert state.acquire(route, {"deepseek"}, set()) is None
    state.release("deepseek", True, None, .1)
    assert state.snapshot()["providers"]["deepseek"]["state"] == "closed"
