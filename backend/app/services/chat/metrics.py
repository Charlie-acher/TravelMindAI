"""运行记录层：按真实过程事件计时，汇总外部调用用量，不记录原话或推理。"""

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)
active_metrics: ContextVar["RunMetrics | None"] = ContextVar("chat_metrics", default=None)


class RunMetrics:
    """单次运行统计类：阶段耗时为相邻公开步骤间隔，模型调用单独汇总。"""

    """初始化函数：每个HTTP执行独立计时，重试不混入上一次运行。"""

    def __init__(self) -> None:
        self.started = self.changed = perf_counter()
        self.stage = "received"
        self.stages: dict[str, float] = {}
        self.first_draft: float | None = None
        self.models: list[dict[str, Any]] = []

    """事件记录函数：正文只用于确定首段到达时间，不保存正文内容。"""

    def observe(self, event: str, data: dict[str, object]) -> None:
        if event == "progress":
            now = perf_counter()
            self.stages[self.stage] = self.stages.get(self.stage, 0) + now - self.changed
            self.stage, self.changed = str(data["stage"]), now
        elif event == "draft" and data.get("text") and self.first_draft is None:
            self.first_draft = perf_counter() - self.started

    """完成函数：未知token和费用保持未知，阶段间隔不冒充独立工具纯执行时间。"""

    def finish(self) -> dict[str, Any]:
        now = perf_counter()
        stages = dict(self.stages)
        stages[self.stage] = stages.get(self.stage, 0) + now - self.changed
        costs: dict[str, Decimal] = {}
        unknown = 0
        for call in self.models:
            cost = call.get("cost", {})
            if cost.get("amount") is not None:
                currency = cost["currency"]
                costs[currency] = costs.get(currency, Decimal(0)) + Decimal(cost["amount"])
            elif cost.get("status") != "local":
                unknown += 1
        return {"total_seconds": round(now - self.started, 3),
                "first_draft_seconds": (round(self.first_draft, 3)
                                        if self.first_draft is not None else None),
                "stages_seconds": {key: round(value, 3) for key, value in stages.items()},
                "model_calls": self.models,
                "known_total_tokens": sum(item["total_tokens"] or 0 for item in self.models),
                "unknown_usage_calls": sum(item["total_tokens"] is None for item in self.models),
                "cost_cny": str(costs.get("CNY", Decimal(0)))
                    if not unknown and not costs.get("USD") else None,
                "known_costs": {key: str(value) for key, value in costs.items()},
                "unknown_cost_calls": unknown,
                "unpersisted_calls": sum(item.get("persisted") is False for item in self.models),
                "usage_scope": "external_api_calls",
                "cost_basis": "官方原价估算，原币种；本机硬件、电力及套餐优惠未计入"}


"""运行范围函数：成功、失败和取消都记录，退出恢复上下文，兼容非流式请求。"""

@contextmanager
def measure_run(request_id: str, publish: Callable[[str, dict[str, object]], None] | None = None
                ) -> Iterator[RunMetrics]:
    run = RunMetrics()
    token = active_metrics.set(run)
    outcome = "failed"
    try:
        yield run
        outcome = "completed"
    finally:
        active_metrics.reset(token)
        report = {**run.finish(), "request_id": request_id, "outcome": outcome}
        logger.info("chat_metrics %s", json.dumps(report, ensure_ascii=False))
        if publish is not None:
            publish("metrics", report)
