"""测试层：检查景点定位、网页证据和门票卡片，外部服务使用离线替身。"""

import json
from unittest.mock import Mock

import httpx
import pytest

from app.config import Settings
from app.services.amap import AmapClient
from tests.helpers import evidence

"""定位测试函数：只保存有效坐标，入口位置与景点中心分开，消费不变成门票。"""


@pytest.mark.parametrize("coordinate, expected", [("120.12,30.25", True),
                                                 ("200,30", False), ("NaN,30", False)])
def test_map_coordinates(coordinate: str, expected: bool) -> None:
    """响应替身函数：返回同名同城景点及不同的坐标数据。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/config/district":
            return httpx.Response(200, json={"status": "1", "districts": [{
                "name": "杭州市", "level": "city", "adcode": "330100",
                "citycode": "0571",
            }]})
        assert request.url.params["show_fields"] == "business,navi"
        assert request.url.params["region"] == "330100"
        return httpx.Response(200, json={"status": "1", "pois": [{
            "id": "B001", "name": "湖滨路步行街", "cityname": "杭州市",
            "location": coordinate, "address": "湖滨路",
            "navi": {"entr_location": "120.13,30.26"}, "business": {"cost": "0"},
        }]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = AmapClient(Settings(amap_api_key="test"), http).lookup("杭州", "湖滨路步行街")
    assert bool(result.location) == expected
    assert result.entrance.longitude == 120.13
    assert result.entrance.coordinate_system == "GCJ-02"


"""卡片测试函数：即使已有历史门票，也要定位；最终价格必须能对应证据原句。"""


def test_attraction_card_uses_map_and_ticket_evidence() -> None:
    from app.schemas.document.answer import GeoPoint, MapLookup
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    hit.chunk.text = "城市：杭州；景点名称：湖滨路步行街。2025年门票：成人票8元。"
    draft = {"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
             "description": "湖边的步行街，适合散步。", "reason": "适合步行游览。",
             "ticket": {"status": "paid", "summary": "2025年成人票8元，仅供参考。",
                        "amount": "8", "ticket_type": "成人票", "applicable_date": "2025年",
                        "source_id": 1, "quote": "2025年门票：成人票8元。"}}
    first = {"status": "answered", "points": [{"text": "适合散步。", "source_ids": [1]}],
             "attractions": [draft]}
    model, maps = Mock(), Mock()
    model.generate_json.side_effect = [json.dumps(first), json.dumps(first)]
    maps.lookup.return_value = MapLookup(city="杭州", name=draft["name"], status="found",
                                        poi_id="B001",
                                        location=GeoPoint(longitude=120, latitude=30),
                                        )
    result = answer_from_sources("杭州散步景点", [hit], model, maps=maps)
    maps.lookup.assert_called_once_with("杭州", draft["name"])
    assert result.attractions[0].location.poi_id == "B001"
    assert result.attractions[0].ticket.basis == "reference"
    assert str(result.attractions[0].ticket.amount) == "8"


"""网页回退测试函数：知识库无命中时可用真实返回的网页证据，不能伪造来源编号。"""


def test_web_evidence_can_answer_without_local_hits() -> None:
    from app.schemas.document.answer import WebEvidence, WebSearchResult
    from app.services.document.answer import answer_from_sources

    web, model = Mock(), Mock()
    web.search.return_value = WebSearchResult(status="found", items=[WebEvidence(
        id=6, title="杭州景点公告", url="https://example.org/notice",
        content="杭州湖滨路步行街适合步行游览。",
    )])
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可步行游览。", "source_ids": [6]}],
    })
    result = answer_from_sources("杭州哪里散步", [], model, web=web)
    assert result.status == "answered" and not result.sources
    assert result.web_search.items[0].id == 6
    web.search.assert_called_once()
    assert "web_sources" in json.loads(model.generate_json.call_args.args[0][1]["content"])


"""票价拒绝测试函数：模型数字或摘录不在证据里时，不得生成看似确认的票价卡片。"""


def test_ticket_cannot_invent_price() -> None:
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    model = Mock()
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{"text": "可散步。", "source_ids": [1]}],
        "attractions": [{"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
                         "description": "可散步。", "reason": "步行游览。",
                         "ticket": {"status": "paid", "summary": "成人票99元",
                                    "amount": "99", "ticket_type": "成人票",
                                    "source_id": 1, "quote": "成人票99元。"}}],
    })
    result = answer_from_sources("介绍杭州景点", [hit], model)
    assert result.attractions[0].ticket.status == "unknown"
    assert "99元" not in str(result.attractions) + str(result.points)
    assert result.clarification


"""二次回答回归函数：保留已核对的卡片，页面票价说明不得另编数字。"""


@pytest.mark.parametrize("bad_summary", [None, "成人票99元", "成人票2025元"])
def test_second_pass_keeps_cards_and_verified_ticket_text(bad_summary: str | None) -> None:
    from app.services.document.answer import answer_from_sources

    hit, model = evidence(), Mock()
    hit.chunk.text = "杭州湖滨路步行街。2025年门票：成人票8元。"
    draft = {"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
             "description": "适合散步。", "reason": "步行游览。",
             "ticket": {"status": "paid", "summary": bad_summary or "成人票8元",
                        "amount": "8",
                        "source_id": 1, "quote": "2025年门票：成人票8元。"}}
    final = {"status": "answered", "points": [{"text": "可以散步。", "source_ids": [1]}]}
    model.generate_json.side_effect = [json.dumps({**final, "attractions": [draft]}),
                                       json.dumps(final)]
    if bad_summary:
        model.generate_json.side_effect = [json.dumps({**final, "attractions": [draft]})] * 2
        result = answer_from_sources("杭州哪里散步", [hit], model)
        assert result.attractions[0].ticket.status == "unknown"
        assert bad_summary not in result.model_dump_json()
        assert result.attractions[0].description == "适合散步。"
        return
    result = answer_from_sources("杭州哪里散步", [hit], model)
    assert len(result.attractions) == 1
    assert result.attractions[0].ticket.summary == "成人票8元"
    payload = json.loads(model.generate_json.call_args.args[0][1]["content"])
    assert payload["attraction_drafts"][0]["name"] == "湖滨路步行街"


"""搜索指代回归函数：目的地和日期不能挤掉上一轮第二个景点的名称。"""


def test_web_query_keeps_second_attraction_in_context() -> None:
    from app.schemas.document.answer import WebSearchResult
    from app.services.document.answer import answer_from_sources

    web = Mock()
    web.search.return_value = WebSearchResult(status="empty")
    context = ("杭州\n旅行日期：2026-10-01至2026-10-03\n"
               "最近第1轮景点顺序（仅供指代）：1.杭州/湖滨路步行街；2.杭州/太子湾公园")
    answer_from_sources("第二个门票多少", [], web=web, conversation_context=context)
    assert "太子湾公园" in web.search.call_args.args[0]
    assert len(web.search.call_args.args[0]) <= 500


"""日期格式回归函数：同一天的中文和横线写法等价，不同日期仍不能通过。"""


@pytest.mark.parametrize("applicable_date, valid", [("2026-02-22", True), ("2027-02-22", False)])
def test_ticket_date_accepts_equivalent_format(applicable_date: str, valid: bool) -> None:
    from app.schemas.document.answer import AttractionDraft
    from app.services.document.places import validate_attractions

    quote = "2026年2月22日苏堤步行游览门票免费，仅限步行游览。"
    draft = AttractionDraft.model_validate({
        "city": "杭州", "name": "苏堤", "description": "沿湖步行。", "reason": "适合散步。",
        "source_ids": [1], "ticket": {"status": "partial", "summary": "所示日期步行游览免费。",
        "ticket_type": "步行游览", "applicable_date": applicable_date,
        "source_id": 1, "quote": quote},
    })
    if valid:
        validate_attractions([draft], {1: "杭州苏堤。" + quote}, set())
        assert draft.ticket.quote == quote
    else:
        with pytest.raises(ValueError, match="适用时间"):
            validate_attractions([draft], {1: "杭州苏堤。" + quote}, set())


"""新问题回归函数：换城市或询问住宿时，不混入旧景点和统一的门票搜索词。"""


@pytest.mark.parametrize("question", [
    "贵州有哪些网红打卡点？", "杭州西湖附近有什么300元以下住宿？",
])
def test_standalone_web_question_uses_original_query(question: str) -> None:
    from app.schemas.document.answer import WebSearchResult
    from app.services.document.answer import answer_from_sources

    web = Mock()
    web.search.return_value = WebSearchResult(status="empty")
    answer_from_sources(question, [], web=web,
                        conversation_context="杭州\n最近第1轮景点顺序：1.杭州/苏堤")
    web.search.assert_called_once_with(question)


"""修正次数回归函数：格式错误只补一次生成，地图查询不会因修正重复执行。"""


@pytest.mark.parametrize("bad_stage", [0, 1])
def test_one_output_repair_does_not_repeat_map_lookup(bad_stage: int) -> None:
    from app.schemas.document.answer import MapLookup
    from app.services.document.answer import answer_from_sources

    hit, model, maps = evidence(), Mock(), Mock()
    hit.chunk.text = "城市：杭州；景点名称：湖滨路步行街。适合散步。"
    draft = {"city": "杭州", "name": "湖滨路步行街", "source_ids": [1],
             "description": "适合散步。", "reason": "步行游览。"}
    valid = {"status": "answered", "points": [{"text": "可以散步。", "source_ids": [1]}],
             "attractions": [draft]}
    invalid = {**valid, "map_queries": [{"city": "杭州", "name": "湖滨路步行街"}]}
    outputs = [valid, valid]
    outputs.insert(bad_stage, invalid)
    model.generate_json.side_effect = [json.dumps(item) for item in outputs]
    maps.lookup.return_value = MapLookup(city="杭州", name="湖滨路步行街", status="no_match")
    result = answer_from_sources("杭州景点", [hit], model, maps=maps)
    assert result.attractions[0].name == "湖滨路步行街"
    assert model.generate_json.call_count == 3
    maps.lookup.assert_called_once()
