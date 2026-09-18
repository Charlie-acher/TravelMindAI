"""模型输入层：按序列化字符近似控制总量，只允许裁剪可选的普通对话历史。"""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

INPUT_CHAR_BUDGET = 64000  # 字符近似，不代表模型token窗口；输出额度由模型配置另行预留。
_OMISSION = {"role": "user", "content": (
    "[部分较早历史因输入预算省略。当前原话和必要状态未截断；需要旧细节时应回查原文。]"
)}


class ModelInputLimitError(ValueError):
    """输入超限异常类：必留内容或完整工具上下文过大，调用方须停止本次模型调用。"""


"""字符计量函数：计入角色、JSON转义、系统说明和实际开放工具的参数说明。"""


def _input_chars(
    messages: Sequence[dict[str, str] | BaseMessage], *,
    system: str | BaseMessage | None = None,
    tools: Sequence[BaseTool | dict[str, Any]] = (),
) -> int:
    payload: dict[str, Any] = {
        "messages": [item.model_dump(mode="json") if isinstance(item, BaseMessage) else item
                     for item in messages],
    }
    if system is not None:
        payload["system"] = (
            system.model_dump(mode="json") if isinstance(system, BaseMessage) else system
        )
    if tools:
        payload["tools"] = [convert_to_openai_tool(tool) for tool in tools]
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


"""总量检查函数：完整检查Agent消息和工具说明，绝不拆开模型工具调用与返回结果。"""


def ensure_input_budget(
    messages: Sequence[dict[str, str] | BaseMessage], *,
    system: str | BaseMessage | None = None,
    tools: Sequence[BaseTool | dict[str, Any]] = (),
    max_chars: int = INPUT_CHAR_BUDGET,
) -> int:
    size = _input_chars(messages, system=system, tools=tools)
    if size > max_chars:
        raise ModelInputLimitError(f"模型输入约{size}字符，超过本次{max_chars}字符预算")
    return size


"""聊天组装函数：必留前缀及当前问题完整保留，历史只取预算内连续整轮后缀。"""


def budget_messages(
    prefix: list[dict[str, str]], current: dict[str, str],
    history: list[dict[str, str]] | None = None, *, max_chars: int = INPUT_CHAR_BUDGET,
) -> list[dict[str, str]]:
    ensure_input_budget([*prefix, current], max_chars=max_chars)
    blocks: list[list[dict[str, str]]] = []
    for message in history or []:
        if message["role"] == "user":
            blocks.append([message])
        elif message["role"] == "assistant" and blocks:
            blocks[-1].append(message)
        else:
            raise ValueError("可裁剪历史只接受用户消息及其助手回答，工具历史必须整体检查")
    complete = [*prefix, *(history or []), current]
    if _input_chars(complete) <= max_chars:
        return complete
    # 省略说明本身也占预算；即使没有历史可留下，也不能偷偷裁剪必留状态。
    used = ensure_input_budget([*prefix, _OMISSION, current], max_chars=max_chars)
    selected: list[list[dict[str, str]]] = []
    for block in reversed(blocks):
        size = sum(len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 1
                   for item in block)
        if used + size > max_chars:
            break
        selected.append(block)
        used += size
    return [*prefix, dict(_OMISSION),
            *(item for block in reversed(selected) for item in block), current]
