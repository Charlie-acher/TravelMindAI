"""聊天过程层：向当前请求推送公开步骤及回答草稿，不传递模型内部推理。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Event
from time import perf_counter
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from pydantic_core import from_json

from app.schemas.document.answer import AnswerPoint
from app.services.chat.metrics import active_metrics

EventSink = Callable[[str, dict[str, object]], None]
event_sink: ContextVar[EventSink | None] = ContextVar("chat_event_sink", default=None)
answer_sources: ContextVar[set[int] | None] = ContextVar("chat_answer_sources", default=None)
request_cancelled: ContextVar[Event | None] = ContextVar("chat_cancelled", default=None)
research_tasks: ContextVar[list[dict[str, Any]] | None] = ContextVar("research_tasks", default=None)
P = ParamSpec("P")
T = TypeVar("T")


"""工具记录函数：只公开工具名称和执行结果，不发送参数、异常正文或模型内部推理。"""

@contextmanager
def tool_progress(stage: str, name: str) -> Iterator[dict[str, str]]:
    call_id, started = str(uuid4()), perf_counter()
    emit("progress", stage=stage, message=name, call_id=call_id, status="running")
    outcome = {"status": "completed"}
    try:
        yield outcome
    except ChatCancelled:
        outcome["status"] = "cancelled"
        raise
    except Exception:
        outcome["status"] = "failed"
        raise
    finally:
        data: dict[str, object] = {"stage": stage, "message": name, "call_id": call_id,
            "status": outcome["status"], "elapsed_seconds": round(perf_counter() - started, 3)}
        if outcome.get("summary"):
            data["summary"] = outcome["summary"]
        # 结束记录必须能在取消后发布，且不再次抛取消异常覆盖原有结果。
        if (metrics := active_metrics.get()) is not None:
            metrics.observe("progress", data)
        if (sink := event_sink.get()) is not None:
            sink("progress", data)


"""工具装饰函数：在原调用边界记录开始和结束，保留函数签名及异常行为。"""

def traced_tool(stage: str, name: str, summarize: Callable[[Any], str] | None = None
                ) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """装饰函数：绑定公开名称，不暴露函数参数。"""

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        """执行函数：复用原调用返回值，计时仅覆盖真实执行。"""

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with tool_progress(stage, name) as outcome:
                result = function(*args, **kwargs)
                if getattr(result, "status", None) in {"error", "unavailable", "unconfigured"}:
                    outcome["status"] = "failed"
                if summarize is not None:
                    outcome["summary"] = summarize(result)
                return result
        return wrapped
    return decorate


class ChatCancelled(RuntimeError):
    """停止异常类：取消当前执行，不计入模型故障，也不提交未完成候选。"""


"""停止检查函数：外部调用前后和发布前检查，已进入事务的提交仍按幂等恢复。"""

def check_cancelled() -> None:
    signal = request_cancelled.get()
    if signal is not None and signal.is_set():
        raise ChatCancelled("本次生成已停止，已有行程保留。")


"""事件发送函数：普通 JSON 请求没有监听器，仍走同一业务逻辑。"""

def emit(event: str, **data: object) -> None:
    check_cancelled()
    if (metrics := active_metrics.get()) is not None:
        metrics.observe(event, data)
    sink = event_sink.get()
    if sink is not None:
        sink(event, data)


"""步骤通知函数：只在实际进入业务步骤时调用，不模拟百分比。"""

def progress(stage: str, message: str) -> None:
    emit("progress", stage=stage, message=message)


"""回答范围函数：只有资料回答可以生成公开草稿，需求抽取 JSON 不向页面展示。"""

@contextmanager
def public_answer(sources: set[int]) -> Iterator[None]:
    token = answer_sources.set(sources)
    emit("reset")
    try:
        yield
    finally:
        answer_sources.reset(token)


"""草稿读取函数：只取有效引用的公开回答字段，残缺 JSON、工具参数和推理均不展示。"""

def draft_text(raw: str) -> str:
    sources = answer_sources.get()
    if sources is None:
        return ""
    try:
        partial = from_json(raw, allow_partial="trailing-strings")
    except ValueError:
        return ""
    if not isinstance(partial, dict) or partial.get("status") != "answered":
        return ""
    points = partial.get("points")
    if not isinstance(points, list):
        return ""
    texts = []
    for item in points[:6]:
        # JSON字段按出现顺序保留。先读完引用再开始text，才能确认引用数组已闭合；
        # 不遵守输出顺序的模型结果等待最终校验，避免把[1未完部分误当作完整编号。
        if (not isinstance(item, dict) or "source_ids" not in item or "text" not in item
                or list(item).index("source_ids") > list(item).index("text")):
            continue
        try:
            point = AnswerPoint.model_validate(item)
        except ValueError:
            continue
        if set(point.source_ids).issubset(sources):
            texts.append(point.text)
    return "\n\n".join(texts)
