"""Agent回归测试层：使用原生工具消息检查框架循环、额度、异常与公开进度。"""

import json
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APITimeoutError
from pydantic import SecretStr

from app.config import Settings
from app.llm.client import DeepSeekClient, ModelClientError
from app.schemas.document.search import SearchResult
from app.services.chat.events import event_sink
from app.services.itinerary.graph import plan_trip
from app.services.itinerary.rules import build_plan
from tests.helpers import ToolCallingModel, evidence
from tests.test_itinerary_agent import places, proposal, requirements

"""原生调用构造函数：每个工具调用都有独立编号，框架负责关联返回结果。"""


def calls(*items, finish="tool_calls"):
    return AIMessage(content="", tool_calls=[
        {"id": f"call-{i}", "name": name, "args": args} for i, (name, args) in enumerate(items)
    ], response_metadata={"finish_reason": finish})


"""旧草稿构造函数：提供两天已核对的地点，修改无需重新查询。"""


def old_plan():
    return build_plan(requirements(), [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)


"""正式连接测试函数：create_agent直接绑定ChatOpenAI，不经过自写行动转换器。"""


def test_native_model_is_bound_by_agent(monkeypatch):
    bound = Mock()
    bound.invoke.return_value = calls(("submit_plan", {"days": [
        proposal(2, "p2", "10:00").model_dump()]}))
    bind = Mock(return_value=bound)
    monkeypatch.setattr(ChatOpenAI, "bind_tools", bind)
    with httpx.Client() as http:
        client = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        result = plan_trip("第二天改十点", requirements(), old_plan(), client.model,
                           Mock(), Mock(), None)
    assert result.plan.days[1].activities[0].start_time == "10:00"
    assert bind.call_count == 1
    assert bind.call_args.kwargs["tool_choice"] == "required"


"""响应保护测试函数：截断、未知工具、混合提交与查询都不能变成已完成规划。"""


@pytest.mark.parametrize("response", [
    AIMessage(content="我已经规划完成", response_metadata={"finish_reason": "stop"}),
    calls(("delete_file", {})),
    calls(("knowledge_search", {"query": "杭州"}), finish="length"),
    calls(("submit_plan", {"days": []}), ("knowledge_search", {"query": "杭州"})),
    AIMessage(content="", invalid_tool_calls=[{
        "id": "bad", "name": "submit_plan", "args": "{", "error": "invalid JSON"}],
        response_metadata={"finish_reason": "tool_calls"}),
])
def test_unusable_response_never_executes_tools(response):
    search, maps = Mock(), Mock()
    with pytest.raises(ModelClientError):
        plan_trip("修改", requirements(), old_plan(), ToolCallingModel(messages=iter([response])),
                  search, maps, None)
    search.search.assert_not_called()
    maps.lookup.assert_not_called()


"""修正规划测试函数：规则错误经ToolMessage反馈，修正后退出，不再额外调用模型。"""


def test_rule_failure_repairs_with_native_messages_and_sse_progress():
    original = old_plan()
    bad = {"days": [proposal(2, "invented").model_dump()]}
    good = {"days": [proposal(2, "p2", "10:00").model_dump()]}
    model = ToolCallingModel(messages=iter([
        calls(("submit_plan", bad)), calls(("submit_plan", good))]))
    events = []
    token = event_sink.set(lambda event, data: events.append((event, data)))
    try:
        result = plan_trip("第二天十点", requirements(), original, model, Mock(), Mock(), None)
    finally:
        event_sink.reset(token)
    assert len(model.seen_messages) == 2
    feedback = model.seen_messages[1][-1]
    assert isinstance(feedback, ToolMessage) and feedback.status == "error"
    assert feedback.tool_call_id == "call-0" and "地点" in feedback.content
    assert result.plan.days[0] == original.days[0]
    assert result.plan.days[1].activities[0].start_time == "10:00"
    assert len(events) >= 4 and all(event == "progress" for event, data in events)


"""决策额度测试函数：连续六次不合格提交后停止，旧草稿不受影响。"""


def test_model_limit_keeps_old_plan_after_six_invalid_submissions():
    original = old_plan()
    snapshot = original.model_dump()
    model = ToolCallingModel(messages=iter([
        calls(("submit_plan", {"days": []})) for _ in range(6)]))
    result = plan_trip("第二天修改", requirements(), original, model, Mock(), Mock(), None)
    assert len(model.seen_messages) == 6 and result.plan is None
    assert "已有行程已保留" in result.reply
    assert original.model_dump() == snapshot
    assert model.bindings[-1][0] == ["submit_plan", "ask_clarification"]
    context = json.loads(model.seen_messages[-1][0].content.split("本轮可用工具和证据：")[1])
    assert context["remaining_decisions"] == 1
    assert set(context["available_actions"]) == {"submit_plan", "ask_clarification"}
    assert {p["id"] for p in context["places"]} == {"p1", "p2"}


"""查询额度测试函数：批量调用只执行前12次，额度耗尽后仍可自然说明资料不足。"""


def test_external_budget_and_sequential_source_ids():
    search = Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    model = ToolCallingModel(messages=iter([
        calls(*[("knowledge_search", {"query": "杭州景点"}) for _ in range(13)]),
        calls(("ask_clarification", {"clarification": "目前资料不足，想重点去哪里？"})),
    ]))
    result = plan_trip("安排两天", requirements(), None, model, search, Mock(), None)
    assert search.search.call_count == 12 and result.plan is None
    feedback = [m for m in model.seen_messages[1] if isinstance(m, ToolMessage)]
    assert len(feedback) == 13 and feedback[-1].status == "error"
    assert [json.loads(m.content)["sources"][0]["id"] for m in feedback[:-1]] == [
        f"k{i}" for i in range(1, 13)]
    assert model.bindings[-1][0] == ["ask_clarification"]


"""来源保护测试函数：即使地图工具已开放，伪造地点也不能调用高德。"""


def test_map_source_guard_and_extra_arguments():
    search, maps = Mock(), Mock()
    model = ToolCallingModel(messages=iter([
        calls(("map_lookup", {"name": "编造景点", "source_ids": ["k1"]})),
        calls(("knowledge_search", {"query": "杭州", "unexpected": "不该接受"})),
        calls(("ask_clarification", {"clarification": "需要补充可靠的景点资料。"})),
    ]))
    result = plan_trip("修改第二天", requirements(), old_plan(), model, search, maps, None)
    assert result.plan is None and maps.lookup.call_count == 0
    assert search.search.call_count == 0


"""提供方异常测试函数：超时由公开错误边界处理，不暴露请求与密钥。"""


def test_provider_timeout_is_safe(monkeypatch):
    bound = Mock()
    bound.invoke.side_effect = APITimeoutError(request=httpx.Request("POST", "https://example.com"))
    monkeypatch.setattr(ChatOpenAI, "bind_tools", lambda *a, **kw: bound)
    with httpx.Client() as http, pytest.raises(ModelClientError, match="超时"):
        client = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        plan_trip("安排两天", requirements(), None, client.model, Mock(), Mock(), None)
