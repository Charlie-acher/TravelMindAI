"""聊天过程层：向当前请求推送公开步骤及回答草稿，不传递模型内部推理。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic_core import from_json

from app.schemas.document.answer import AnswerPoint

EventSink = Callable[[str, dict[str, object]], None]
event_sink: ContextVar[EventSink | None] = ContextVar("chat_event_sink", default=None)
answer_sources: ContextVar[set[int] | None] = ContextVar("chat_answer_sources", default=None)


"""事件发送函数：普通 JSON 请求没有监听器，仍走同一业务逻辑。"""

def emit(event: str, **data: object) -> None:
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
