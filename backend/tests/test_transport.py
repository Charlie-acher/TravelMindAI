"""交通测试层：使用官方响应格式验证往返查询、时间筛选及失败边界。"""

from datetime import date

import httpx
import pytest

from app.schemas.document.answer import WebSearchResult
from app.schemas.transport import TransportQuery
from app.services.transport import (
    RailClient,
    merge_transport,
    query_transport,
    render_flights,
    render_rail,
)

"""车次样本函数：保留12306公开页面使用的列位置。"""

def train(code, departure, arrival, duration="05:30", seats="6"):
    cells = [""] * 56
    for index, value in {2: "internal", 3: code, 6: "YIJ", 7: "ZAF", 8: departure,
                         9: arrival, 10: duration, 11: "Y", 16: "01", 17: "09",
                         30: seats, 35: "O"}.items():
        cells[index] = value
    return "|".join(cells)


"""传输样本函数：初始化、站名、车次和票价都由模拟官方HTTP返回。"""

def transport(request):
    if request.url.path.endswith("/init"):
        return httpx.Response(200, text="var CLeftTicketUrl = 'leftTicket/queryG';"
            '<script src="/otn/resources/js/framework/station_name.js?v=1"></script>')
    if request.url.path.endswith("station_name.js"):
        return httpx.Response(200, text="@ych|银川|YIJ|yinchuan@zzh|郑州|ZZF|zhengzhou")
    if request.url.path.endswith("/queryG"):
        return httpx.Response(200, json={"status": True, "data": {"result": [
            train("G1", "06:00", "11:30"), train("G2", "09:00", "14:30"),
            train("G3", "20:00", "01:30"), train("K1", "10:00", "15:30")],
            "map": {"YIJ": "银川", "ZAF": "郑州东"}}})
    assert request.url.path.endswith("/queryTicketPrice")
    return httpx.Response(200, json={"status": True, "data": {"O": "¥494.0"}})


"""往返测试函数：筛掉凌晨和次日到达，票价只用官方字段，回程正确交换城市。"""

def test_roundtrip_queries_filters_and_prices():
    seen = []

    def handle(request):
        seen.append(request)
        return transport(request)

    class Web:
        def search(self, query, *, domains):
            assert "csair.com" in domains
            return WebSearchResult(status="empty")

    query = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-22",
        return_date="2026-09-24", earliest_departure="07:00", latest_arrival="23:00",
        travelers=3)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = query_transport(query, Web(), RailClient(http), today=date(2026, 9, 21))
    assert len(result.legs) == 2
    assert [r.code for r in result.legs[0].rail.options] == ["G2"]
    option = result.legs[0].rail.options[0]
    assert str(option.price) == "494.0" and option.to_station == "郑州东"
    requests = [r for r in seen if r.url.path.endswith("queryG")]
    assert requests[1].url.params["leftTicketDTO.from_station"] == "ZZF"
    assert requests[1].url.params["leftTicketDTO.train_date"] == "2026-09-24"
    assert "1482.0" in render_rail(result)  # 仅成人票价乘人数的参考小计。


"""售期测试函数：过去或超过15天的日期不访问余票接口，不假装售罄。"""

def test_outside_sale_window_does_not_query():
    with httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(
            AssertionError("不应访问网络")))) as http:
        client = RailClient(http)
        assert client.query("银川", "郑州", date(2026, 10, 6), TransportQuery(),
                            date(2026, 9, 21)).status == "not_on_sale"
        assert client.query("银川", "郑州", date(2026, 9, 20), TransportQuery(),
                            date(2026, 9, 21)).status == "past_date"


"""失败测试函数：验证页面不能当零车次，票价失败也保留已查到的班次。"""

def test_blocked_and_price_failure_keep_honest_status():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            403, text="verification required"))) as http:
        result = RailClient(http).query("银川", "郑州", date(2026, 9, 22),
                                       TransportQuery(), date(2026, 9, 21))
    assert result.status == "unavailable" and result.options == []

    def failed_price(request):
        return (httpx.Response(503) if request.url.path.endswith("queryTicketPrice")
                else transport(request))

    with httpx.Client(transport=httpx.MockTransport(failed_price)) as http:
        result = RailClient(http).query("银川", "郑州", date(2026, 9, 22),
                                       TransportQuery(), date(2026, 9, 21))
    assert result.status == "found" and result.options[0].price is None


"""聊天接入测试函数：交通问题不进入规划或地图，不补问预算，官方结果随原保存路径提交。"""

def test_chat_routes_transport_without_planning_or_budget_gate():
    import json
    from contextlib import nullcontext
    from unittest.mock import Mock, patch
    from uuid import uuid4

    from app.schemas.requirement.history import RequirementHistory, SavedRequirementMessage
    from app.schemas.transport import TransportResult
    from app.services.chat.service import process_saved_message
    from tests.helpers import answer, understanding

    query = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-22")
    data = understanding(answer(intent="trip_question"))
    data["transport_query"] = query.model_dump(mode="json")
    model, history, maps, search, web = (Mock() for _ in range(5))
    model.generate_json.return_value = json.dumps(data)
    model.generate_text.return_value = "航空公司公开网页未提供该日完整报价。"
    history.read.return_value = RequirementHistory(session_id=uuid4(), revision=0, turns=[])
    history.read_plan.return_value = (0, None)
    history.append.side_effect = lambda *args: args[3]
    result = TransportResult(query=query)
    with patch("app.services.chat.service.query_transport", return_value=result) as lookup:
        saved = process_saved_message(uuid4(), SavedRequirementMessage(message="比较高铁和机票",
            message_id=uuid4(), expected_revision=0), model, history, "transport-test",
            nullcontext(search), maps, web)
    assert saved.transport == result
    assert lookup.call_args.args[1] is web
    search.search.assert_not_called()
    model.generate_text.assert_not_called()  # 官方查询状态不交给第二次模型改写。
    assert saved.status == "knowledge"


"""接续测试函数：只修改返程时间，不筛掉去程；未取得航班报价有可追溯状态。"""

def test_return_time_only_and_airline_query_status():
    from unittest.mock import Mock

    query = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-22",
        return_date="2026-09-24", return_earliest_departure="10:00")
    web = Mock()
    web.search.return_value = WebSearchResult(status="error")
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        result = query_transport(query, web, RailClient(http), today=date(2026, 9, 21))
    assert result.legs[0].rail.options[0].departure.hour == 6
    assert result.legs[1].rail.options[0].departure.hour == 20
    assert "查询失败" in render_flights(result)
    assert web.search.call_count == 2


"""时段独立测试函数：去程单独限时或回程解除限制时，不把去程限制复制给返程。"""

def test_outbound_limit_does_not_constrain_unrestricted_return():
    from unittest.mock import Mock

    from app.schemas.transport import RailResult

    rail = Mock()
    rail.query.return_value = RailResult(status="empty")
    query = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-22",
        return_date="2026-09-24", earliest_departure="10:00", modes=["rail"])
    query_transport(query, None, rail, today=date(2026, 9, 21))
    assert rail.query.call_args_list[0].args[3].earliest_departure.hour == 10
    assert rail.query.call_args_list[1].args[3].earliest_departure is None


"""多轮合并测试函数：仅改返程出发时间保留最晚到达，明确解除才清空。"""

def test_transport_patch_retains_unmentioned_time_and_explicit_clear():
    old = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-22",
        return_date="2026-09-24", earliest_departure="07:00", latest_arrival="23:00",
        return_earliest_departure="07:00", return_latest_arrival="23:00", modes=["rail"],
        preferences="少走路")
    update = TransportQuery(return_earliest_departure="14:00", return_latest_arrival=None,
                            preferences="")
    merged = merge_transport(update, old, [])
    assert merged.return_latest_arrival.hour == 23
    assert merged.earliest_departure.hour == 7 and merged.modes == ["rail"]
    assert merged.preferences == "少走路"
    assert merge_transport(update, old, ["return_latest_arrival"]).return_latest_arrival is None
    assert old.return_earliest_departure.hour == 7


"""提前出发测试函数：前夜无车仍查原日期，夜间限制不传给原日期和返程。"""

@pytest.mark.parametrize("evening_state", ["empty", "found", "unavailable"])
def test_previous_evening_keeps_original_day_and_return(evening_state):
    seen = []

    def handle(request):
        if request.url.path.endswith("/queryG"):
            day = request.url.params["leftTicketDTO.train_date"]
            seen.append(day)
            if day == "2026-09-23" and evening_state == "unavailable":
                return httpx.Response(503)
            rows = [train("G2", "09:00", "14:30")]
            if day == "2026-09-23" and evening_state == "found":
                rows.append(train("G3", "20:00", "01:30"))
            return httpx.Response(200, json={"status": True, "data": {
                "result": rows,
                "map": {"YIJ": "银川", "ZAF": "郑州东"}}})
        return transport(request)

    query = TransportQuery(origin="银川", destination="郑州", departure_date="2026-09-24",
        return_date="2026-09-27", modes=["rail"], previous_day_earliest_departure="19:00",
        latest_arrival="18:00")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = query_transport(query, None, RailClient(http), today=date(2026, 9, 22))
    assert seen == ["2026-09-23", "2026-09-24", "2026-09-27"]
    assert result.legs[0].rail.status == evening_state
    if evening_state == "found":
        assert result.legs[0].rail.options[0].arrival.date() == date(2026, 9, 24)
    assert result.legs[1].rail.options[0].code == "G2"
    assert result.legs[2].rail.options[0].code == "G2"
    text = render_rail(result)
    assert text.count("19:00及以后出发") == 1
    assert "原出发日" in text
    merged = merge_transport(TransportQuery(return_earliest_departure="14:00"), query, [])
    assert merged.previous_day_earliest_departure.hour == 19
    assert merge_transport(TransportQuery(), merged,
        ["previous_day_earliest_departure"]).previous_day_earliest_departure is None


"""停止测试函数：取消后不再请求任何官方接口，也不把停止当成查询失败。"""

def test_cancelled_transport_never_queries():
    from threading import Event

    import pytest

    from app.services.chat.events import ChatCancelled, request_cancelled

    event = Event()
    event.set()
    token = request_cancelled.set(event)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(
                AssertionError("停止后不访问网络")))) as http:
            with pytest.raises(ChatCancelled):
                RailClient(http).query("银川", "郑州", date(2026, 9, 22),
                                       TransportQuery(), date(2026, 9, 21))
    finally:
        request_cancelled.reset(token)
