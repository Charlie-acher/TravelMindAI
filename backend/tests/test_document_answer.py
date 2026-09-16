"""测试层：检查资料问答的引用、资料不足和接口错误，不调用收费模型。"""

import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.llm.client import DeepSeekClient
from tests.helpers import evidence

"""结果兼容测试函数：旧快照可恢复，但内部地图请求不再出现在最终数据或接口文档中。"""


def test_final_answer_hides_internal_queries_and_reads_old_snapshot() -> None:
    from app.schemas.document.answer import AnswerResult, ModelAnswer

    old = {"status": "insufficient", "points": [], "sources": [], "map_queries": []}
    restored = AnswerResult.model_validate(old)
    assert restored.status == "insufficient"
    assert "map_queries" not in restored.model_dump(mode="json")
    assert "map_queries" not in AnswerResult.model_json_schema()["properties"]
    assert "map_queries" in ModelAnswer.model_json_schema()["properties"]


"""状态回归测试函数：拆开模型和最终结果后，两边仍拒绝无要点的已回答状态。"""


def test_answer_status_validation_survives_schema_split() -> None:
    from pydantic import ValidationError

    from app.schemas.document.answer import AnswerResult, ModelAnswer

    for schema, extra in [(ModelAnswer, {}), (AnswerResult, {"sources": []})]:
        with pytest.raises(ValidationError):
            schema.model_validate({"status": "answered", "points": [], **extra})

"""问答测试函数：正常回答的引用必须映射到传入原文，提示词分清问题和资料。"""


def test_answer_uses_authoritative_sources() -> None:
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    model = Mock(spec=DeepSeekClient)
    model.generate_json.return_value = json.dumps({
        "status": "answered",
        "points": [{"text": "可以去湖滨路步行街散步。", "source_ids": [1]}],
    })
    result = answer_from_sources("去哪散步？", [hit], model)
    assert result.status == "answered"
    assert result.points[0].source_ids == [1]
    assert result.sources[0].hit == hit
    messages = model.generate_json.call_args.args[0]
    assert messages[0]["role"] == "system"
    assert "不可信" in messages[0]["content"]
    content = json.loads(messages[1]["content"])
    assert content["question"] == "去哪散步？"
    assert content["sources"][0]["text"] == hit.chunk.text
    model.generate_json.assert_called_once()


"""空资料测试函数：没有命中时直接说明不足，不调用DeepSeek；不足回答不带断言。"""


def test_insufficient_sources_do_not_invent_answer() -> None:
    from app.services.document.answer import answer_from_sources

    model = Mock(spec=DeepSeekClient)
    empty = answer_from_sources("问题", [], model)
    assert empty.status == "insufficient"
    assert empty.points == empty.sources == []
    model.generate_json.assert_not_called()
    model.generate_json.return_value = '{"status":"insufficient","points":[]}'
    result = answer_from_sources("今天票价？", [evidence()], model)
    assert result.status == "insufficient"
    assert not result.points and not result.sources


"""坏输出测试函数：乱填编号、漏引用、空回答和非法JSON都必须失败，不能冒充有依据。"""


@pytest.mark.parametrize("raw", [
    'not json',
    '{"status":"answered","points":[]}',
    '{"status":"answered","points":[{"text":"散步","source_ids":[2]}]}',
    '{"status":"answered","points":[{"text":"散步","source_ids":[]}]}',
    '{"status":"answered","points":[{"text":"散步","source_ids":[true]}]}',
    '{"status":"answered","points":[{"text":"散步","source_ids":["1"]}]}',
    '{"status":"answered","points":[{"text":"  ","source_ids":[1]}]}',
    '{"status":"insufficient","points":[{"text":"乱编","source_ids":[1]}]}',
    '{"status":"answered","points":[],"sources":[{"file_name":"伪造"}]}',
])
def test_invalid_model_output_is_rejected(raw: str) -> None:
    from app.services.document.answer import answer_from_sources

    model = Mock(spec=DeepSeekClient)
    model.generate_json.return_value = raw
    with pytest.raises(HTTPException) as error:
        answer_from_sources("问题", [evidence()], model)
    assert error.value.status_code == 502
    assert model.generate_json.call_count == 2


"""工具衔接测试函数：只有原文内的景点能查地图，结果参与二次回答并保留快照。"""


def test_answer_maps_are_bounded_and_grounded() -> None:
    from app.schemas.document.answer import MapLookup
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    model, maps = Mock(), Mock()
    query = {"city": "杭州", "name": "湖滨路步行街", "source_id": 1}
    first = {"status": "answered", "points": [{"text": "可步行。", "source_ids": [1]}],
             "map_queries": [query, query]}
    model.generate_json.side_effect = [json.dumps(first), json.dumps({
        "status": "answered", "points": [{"text": "可步行。门票暂无法确认。", "source_ids": [1]}],
    })]
    maps.lookup.return_value = MapLookup(city="杭州", name="湖滨路步行街", status="no_match")
    result = answer_from_sources("介绍一下", [hit], model, maps=maps)
    maps.lookup.assert_called_once_with("杭州", "湖滨路步行街")
    assert result.map_lookups[0].status == "no_match"
    final_payload = json.loads(model.generate_json.call_args.args[0][1]["content"])
    assert final_payload["map_lookups"][0]["status"] == "no_match"
    assert final_payload["sources"][0]["text"] == hit.chunk.text
    # 名字或城市不在指定证据里，不能凭模型记忆扩展工具查询范围。
    for wrong in [dict(query, name="故宫"), dict(query, city="北京")]:
        model.generate_json.side_effect = [json.dumps(dict(first, map_queries=[wrong]))] * 2
        maps.reset_mock()
        with pytest.raises(HTTPException):
            answer_from_sources("介绍一下", [hit], model, maps=maps)
        maps.lookup.assert_not_called()


"""漏查回归函数：模型漏掉工具请求时，已介绍的结构化景点仍会补查，不查询别处。"""


def test_missing_tool_request_is_filled_from_explicit_poi_fields() -> None:
    from app.schemas.document.answer import MapLookup
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    hit = hit.model_copy(update={"chunk": hit.chunk.model_copy(update={
        "text": "城市：杭州；景点名称：湖滨路步行街，也称湖滨步行街。适合散步。门票尚待核实。",
    })})
    model, maps = Mock(), Mock()
    model.generate_json.side_effect = [json.dumps({
        "status": "answered", "points": [{
            "text": "湖滨路步行街可以散步。门票暂无法确认。", "source_ids": [1],
        }],
    }), json.dumps({
        "status": "answered", "points": [{
            "text": "湖滨路步行街可以散步。门票暂无法确认，地图查询尚未启用。", "source_ids": [1],
        }],
    })]
    maps.lookup.return_value = MapLookup(city="杭州", name="湖滨路步行街", status="unconfigured")
    result = answer_from_sources("杭州哪里适合散步？", [hit], model, maps=maps)
    maps.lookup.assert_called_once_with("杭州", "湖滨路步行街")
    assert result.map_lookups[0].status == "unconfigured"


"""已知票价回归函数：普通介绍已有历史参考时，也要核对地图位置。"""


def test_known_historical_ticket_still_resolves_location() -> None:
    from app.schemas.document.answer import MapLookup
    from app.services.document.answer import answer_from_sources

    hit = evidence()
    hit = hit.model_copy(update={"chunk": hit.chunk.model_copy(update={
        "text": "城市：杭州；景点名称：湖滨路步行街。门票：2026年公开记录为免费步游。",
    })})
    model, maps = Mock(), Mock()
    maps.lookup.return_value = MapLookup(city="杭州", name="湖滨路步行街", status="unconfigured")
    model.generate_json.return_value = json.dumps({
        "status": "answered", "points": [{
            "text": "湖滨路步行街适合步行。门票：2026年公开记录为免费步游。", "source_ids": [1],
        }],
    })
    result = answer_from_sources("介绍一下", [hit], model, maps=maps)
    maps.lookup.assert_called_once_with("杭州", "湖滨路步行街")
    assert model.generate_json.call_count == 2
    assert result.map_lookups[0].status == "unconfigured"
