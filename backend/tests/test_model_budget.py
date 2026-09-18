"""模型预算测试层：验证整轮裁剪、必留状态保护和工具上下文总量检查。"""

import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.llm.budget import ModelInputLimitError, budget_messages, ensure_input_budget


def test_budget_preserves_complete_input_when_it_fits() -> None:
    prefix = [{"role": "system", "content": "规则"}]
    history = [{"role": "user", "content": "去苏州"},
               {"role": "assistant", "content": "看看园林"}]
    current = {"role": "user", "content": "推荐热门景点"}
    assert budget_messages(prefix, current, history) == [*prefix, *history, current]


def test_budget_drops_old_blocks_but_keeps_latest_whole_turn_and_current_json() -> None:
    prefix = [{"role": "system", "content": "规则"}]
    history = [
        {"role": "user", "content": "甲" * 500},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "去苏州"},
        {"role": "assistant", "content": "看看园林"},
    ]
    current = {"role": "user", "content": '{"question":"热门景点","constraint":"不爬山"}'}
    before = copy.deepcopy((prefix, history, current))
    result = budget_messages(prefix, current, history, max_chars=400)
    assert result[0] == prefix[0]
    assert "省略" in result[1]["content"]
    assert result[-3:] == [*history[-2:], current]
    assert len(json.dumps({"messages": result}, ensure_ascii=False,
                          separators=(",", ":"))) <= 400
    assert json.loads(result[-1]["content"])["constraint"] == "不爬山"
    assert (prefix, history, current) == before


def test_budget_does_not_skip_large_recent_turn_to_resurrect_older_history() -> None:
    history = [{"role": "user", "content": "旧问题"},
               {"role": "assistant", "content": "旧回答"},
               {"role": "user", "content": "近期问题"},
               {"role": "assistant", "content": "甲" * 500}]
    current = {"role": "user", "content": "继续"}
    result = budget_messages([], current, history, max_chars=250)
    assert result[-1] == current
    assert len(result) == 2
    assert "省略" in result[0]["content"]


@pytest.mark.parametrize("field", ["prefix", "current"])
def test_budget_rejects_oversized_required_state_or_current_without_truncating(field) -> None:
    prefix = [{"role": "system", "content": "规则"}]
    current = {"role": "user", "content": "继续"}
    if field == "prefix":
        prefix.append({"role": "user", "content": json.dumps({"hard_constraints": ["禁" * 64000]})})
    else:
        current["content"] = "问" * 64000
    with pytest.raises(ModelInputLimitError):
        budget_messages(prefix, current)


def test_guard_counts_agent_tool_results_and_leaves_pairs_unchanged() -> None:
    messages = [HumanMessage(content="查询"),
                AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "a"}]),
                ToolMessage(content="结果" * 300, tool_call_id="a")]
    before = copy.deepcopy(messages)
    with pytest.raises(ModelInputLimitError):
        ensure_input_budget(messages, system="规则", max_chars=500)
    assert messages == before


def test_guard_counts_system_and_tool_schemas_not_only_message_text() -> None:
    tool = StructuredTool.from_function(lambda city: city, name="lookup", description="描述" * 300)
    with pytest.raises(ModelInputLimitError):
        ensure_input_budget([], tools=[tool], max_chars=500)
    with pytest.raises(ModelInputLimitError):
        ensure_input_budget([], system="规" * 600, max_chars=500)
    assert ensure_input_budget([{"role": "user", "content": "你好"}]) < 100


def test_budget_refuses_to_trim_agent_tool_history() -> None:
    with pytest.raises(ValueError):
        budget_messages([], {"role": "user", "content": "继续"},
                        [{"role": "tool", "content": "结果"}])
