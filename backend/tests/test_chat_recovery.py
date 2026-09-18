"""测试层：普通提问不会被资料格式阻断，城市与模型原生回答能接续。"""

import json
from unittest.mock import Mock

import pytest

from app.llm.client import ModelClientError, ModelOutputError
from app.llm.embeddings import EmbeddingError
from app.schemas.document.search import SearchResult
from app.services.chat.rag import ground_chat_response
from tests.helpers import evidence
from tests.test_chat_rag import response

"""兜底测试函数：资料为空、服务不可用和回答格式错误都继续正常聊天。"""


@pytest.mark.parametrize("failure", ["empty", "unavailable", "format", "insufficient"])
def test_native_answer_recovers_knowledge_failures(failure):
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[] if failure == "empty" else [evidence()])
    if failure == "unavailable":
        search.search.side_effect = EmbeddingError("unavailable")
    model.generate_json.return_value = (
        "not json" if failure == "format" else json.dumps({"status": "insufficient", "points": []})
    )
    model.generate_text.return_value = "苏州可以先逛拙政园、平江路和山塘街。"
    result = ground_chat_response(response("推荐一些热门景点吧"), search, model, "苏州")
    assert result.reply == "苏州可以先逛拙政园、平江路和山塘街。"
    assert result.knowledge is None or not result.knowledge.attractions
    messages = model.generate_text.call_args.args[0]
    payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert "苏州" in payload["conversation_context"]
    assert messages[-1] == {"role": "user", "content": "推荐一些热门景点吧"}


"""城市检索测试函数：省略城市的推荐问题仍限定目的地，不触发网页查询。"""


@pytest.mark.parametrize("city", ["苏州", "苏州市"])
def test_generic_recommendation_keeps_destination(city):
    from app.schemas.document.answer import WebSearchResult

    search, model, web = Mock(), Mock(), Mock()
    search.search.return_value = SearchResult(items=[])
    web.search.return_value = WebSearchResult(status="empty")
    model.generate_text.return_value = "苏州可以先逛平江路。"
    ground_chat_response(response("我没去过，推荐一些热门景点吧"), search, model, city, web=web)
    assert "苏州" in search.search.call_args.args[0]
    assert search.search.call_args.kwargs["metadata"].city == "苏州"
    web.search.assert_not_called()


"""错城测试函数：即使模型返回别城卡片，也不能将其当作本城推荐保存。"""


def test_wrong_city_cards_fall_back_to_native_answer():
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    model.generate_json.return_value = json.dumps({"status": "answered", "points": [
        {"source_ids": [1], "text": "西湖适合散步。"}], "attractions": [{
            "city": "杭州", "name": "西湖", "description": "适合散步。",
            "reason": "热门景点", "source_ids": [1]}]})
    model.generate_text.return_value = "苏州推荐平江路、拙政园。"
    result = ground_chat_response(response("热门景点"), search, model, "苏州")
    assert "西湖" not in result.reply
    assert result.knowledge is None or not result.knowledge.attractions


"""目的地测试函数：咨询另一城市不直接修改已经确认的旅行目的地。"""


def test_information_question_preserves_confirmed_destination():
    from app.schemas.requirement.base import TravelRequestExtraction
    from app.schemas.requirement.update import RequirementUpdate
    from app.services.requirement.merge import merge_requirements
    from tests.helpers import answer

    previous = TravelRequestExtraction.model_validate(answer(destination="杭州", days=2))
    update = RequirementUpdate.model_validate(answer(intent="travel_info", destination="苏州"))
    result = merge_requirements(previous, update)
    assert result.destination == "杭州"
    assert result.days == 2


"""输出边界测试函数：结构化截断可转文字，真正的模型网络失败仍向外报告。"""


@pytest.mark.parametrize("error", [ModelOutputError("答案截断"), ModelClientError("网络失败")])
def test_model_output_fallback_does_not_hide_network_failure(error):
    search, model = Mock(), Mock()
    search.search.return_value = SearchResult(items=[evidence()])
    model.generate_json.side_effect = error
    model.generate_text.return_value = "苏州可以逛园林和古街。"
    if isinstance(error, ModelOutputError):
        result = ground_chat_response(response("苏州热门景点"), search, model, "苏州")
        assert result.reply == model.generate_text.return_value
        assert result.knowledge is not None
        assert result.knowledge.status == "insufficient" and not result.knowledge.attractions
        assert (result.knowledge.sources[0].hit.chunk.id
                == search.search.return_value.items[0].chunk.id)
    else:
        with pytest.raises(ModelClientError, match="网络失败"):
            ground_chat_response(response("苏州热门景点"), search, model, "苏州")
        model.generate_text.assert_not_called()
