"""测试层：使用真实内存MCP服务验证框架工具装配、查询依据和连接边界。"""

import json
from unittest.mock import Mock

import pytest
from fastmcp import FastMCP
from langchain_core.messages import ToolMessage

from app.config import Settings
from app.services.baidu_mcp import BaiduMCPClient

"""未配置测试函数：未填AK时不连接外部服务，保持原有聊天能力。"""


def test_unconfigured_does_not_connect():
    client = BaiduMCPClient(Settings())
    with client.open_tools() as bundle:
        assert bundle.status == "unconfigured"
        assert bundle.tools == []


"""原生工具测试函数：服务器生成参数结构，框架完成发现、调用和清理。"""


def test_real_mcp_adapter_and_readonly_allowlist(monkeypatch):
    server = FastMCP("百度测试替身")

    """天气替身函数：真实MCP协议返回有来源的天气事实。"""

    @server.tool
    def map_weather(district_id: str) -> dict:
        return {"status": 0, "result": {"district_id": district_id, "weather": "晴", "temp": 25}}

    """分享替身函数：模拟不允许自动调用的写入工具。"""

    @server.tool
    def map_poi_extract(text_content: str) -> str:
        raise AssertionError("分享工具不应开放")

    client = BaiduMCPClient(Settings(baidu_map_api_key="test-key"))
    monkeypatch.setattr(client, "_adapter", lambda: __import__(
        "langchain.mcp", fromlist=["MCPAdapter"]).MCPAdapter(server))
    with client.open_tools() as bundle:
        assert [tool.name for tool in bundle.tools] == ["map_weather"]
        result = bundle.tools[0].invoke({"name":"map_weather", "args":{"district_id":"330100"},
                                         "id":"call1", "type":"tool_call"})
        assert isinstance(result, ToolMessage) and result.status == "success"
        assert "晴" in str(result.content)
        assert bundle.evidence[0].tool_name == "map_weather"
        assert "test-key" not in bundle.evidence[0].model_dump_json()
        assert "district_id" in bundle.tools[0].args_schema["properties"]


"""连接失败测试函数：失败可降级且不把带AK的网址暴露给页面或模型。"""


def test_connection_failure_is_sanitized(monkeypatch):
    client = BaiduMCPClient(Settings(baidu_map_api_key="do-not-leak"))
    monkeypatch.setattr(client, "_adapter", Mock(side_effect=ValueError(
        "https://mcp.map.baidu.com/mcp?ak=do-not-leak")))
    with client.open_tools() as bundle:
        assert bundle.status == "error" and not bundle.tools
        assert "do-not-leak" not in str(bundle)


"""错误与次数测试函数：业务错误不产生依据，调用达到上限后不再请求服务器。"""


@pytest.mark.parametrize("status", [210, False])
def test_errors_limits_and_business_exception(monkeypatch, status):
    from langchain.mcp import MCPAdapter

    from app.services.chat.metrics import measure_run

    server = FastMCP("次数限制")
    calls = []

    """错误替身函数：返回业务错误码，虽然MCP信封成功也不能当成有效事实。"""

    @server.tool
    def map_weather(district_id: str) -> dict:
        calls.append(district_id)
        return {"status": status, "message": "test-key"}

    client = BaiduMCPClient(Settings(baidu_map_api_key="test-key"))
    monkeypatch.setattr(client, "_adapter", lambda: MCPAdapter(server))
    with measure_run("map-errors") as run, pytest.raises(RuntimeError, match="business-failed"):
        with client.open_tools() as bundle:
            for _ in range(25):
                reply = bundle.tools[0].invoke({"district_id": "330100"})
                assert "test-key" not in str(reply)
            assert len(calls) == 24 and not bundle.evidence
            raise RuntimeError("business-failed")
    tools = [step for step in run.process_snapshot()["steps"] if step.get("call_id")]
    assert len(tools) == 25
    assert all(step["status"] == "failed" for step in tools)


"""流式持久化测试函数：真实协议查询交给Agent，历史恢复和重试不重新连接。"""


def test_agent_evidence_stream_and_atomic_history(store_engine, monkeypatch):
    from contextlib import nullcontext
    from uuid import uuid4

    from langchain.mcp import MCPAdapter
    from langchain_core.messages import AIMessage

    from app.schemas.document.search import SearchResult
    from app.schemas.requirement.history import SavedRequirementMessage
    from app.services.baidu import BaiduMaps
    from app.services.chat.events import event_sink
    from app.services.chat.service import process_saved_message
    from app.services.requirement.history import RequirementHistoryService
    from app.services.trip_service import TripService
    from tests.helpers import TEST_USER_ID, ToolCallingModel, answer, understanding
    from tests.test_nearby import native

    server = FastMCP("天气查询")

    """天气替身函数：只回传明确的温度和天气。"""

    @server.tool
    def map_weather(district_id: str) -> dict:
        return {"status": 0, "result": {"district_id": district_id, "temp": 25, "text": "晴"}}

    adapters = Mock(side_effect=lambda _: MCPAdapter(server))
    monkeypatch.setattr(BaiduMCPClient, "_adapter", lambda self: adapters(self))
    trip = TripService(store_engine).create_session("MCP回归", user_id=TEST_USER_ID)
    history = RequirementHistoryService(store_engine)
    events = []
    token = event_sink.set(lambda event, data: events.append((event, data)))
    payload = SavedRequirementMessage(message="杭州天气", message_id=uuid4(), expected_revision=0)
    # 提供方即使忽略parallel_tool_calls=False，框架仍可逐个执行同轮只读查询。
    model = Mock(model=ToolCallingModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "map_weather", "args": {"district_id": district},
            "id": district} for district in ("330100", "330101")]),
        AIMessage(content="", tool_calls=[{"name": "answer_from_maps", "id": "answer",
            "args": {"reply": "杭州晴，25℃。", "source_ids": [1, 2]}}]),
    ])))
    model.generate_json.return_value = json.dumps(understanding(
        answer(intent="travel_info"), response_mode="map", query_cities=["杭州"],
    ), ensure_ascii=False)
    try:
        with BaiduMaps(Settings(baidu_map_api_key="test-key")) as maps:
            saved = process_saved_message(trip.id, payload, model, history, "mcp-test",
                                           nullcontext(Mock()), maps)
        assert saved.response.mcp.evidence[0].tool_name == "map_weather"
        assert saved.response.mcp.source_ids == [1, 2]
        assert model.model.bindings[0][1]["parallel_tool_calls"] is False
        assert history.read(trip.id).turns[-1] == saved
        assert any(data.get("stage") == "mcp_query" for _, data in events)
        model.generate_json.assert_called_once()
        with BaiduMaps(Settings(baidu_map_api_key="test-key")) as maps:
            assert process_saved_message(trip.id, payload, model, history, "retry",
                                          nullcontext(Mock()), maps) == saved
        assert adapters.call_count == 1
        # 假引用不能保存成地图事实；工具失败后改查资料并保存真实的不确定说明。
        bad = Mock(model=native(*[("answer_from_maps", {"reply": "伪造", "source_ids": [99]})] * 8))
        bad.generate_json.return_value = model.generate_json.return_value
        bad.generate_text.return_value = "明天的天气暂时没有核实，不能沿用今天的温度。"
        search = Mock()
        search.search.return_value = SearchResult(items=[])
        with BaiduMaps(Settings(baidu_map_api_key="test-key")) as maps:
            recovered = process_saved_message(trip.id, SavedRequirementMessage(message="明天呢",
                message_id=uuid4(), expected_revision=1), bad, history, "invalid",
                nullcontext(search), maps)
        assert recovered.response.mcp is None
        assert recovered.response.reply == bad.generate_text.return_value
        assert search.search.called
        assert history.read(trip.id).revision == 2
        assert history.read(trip.id).turns[0] == saved
    finally:
        event_sink.reset(token)


"""精简结果测试函数：大量地图绘制点和图片不进入上下文，路线与评分事实保留。"""


def test_compact_keeps_business_facts():
    from app.services.baidu_mcp import compact_result

    value = {"path": "x" * 100000, "steps": [{"instruction": "步行到车站", "duration": 60,
        "polyline": "long"}], "detail_info": {"price": 100, "overall_rating": 4.8,
        "photos": ["image"]}}
    result = compact_result(value)
    assert result == {"steps": [{"instruction": "步行到车站", "duration": 60}],
                      "detail_info": {"price": 100, "overall_rating": 4.8}}
