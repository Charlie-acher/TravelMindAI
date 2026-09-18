"""模型文本验收层：验证原生流式正文、完整性和已核对卡片的保留。"""

from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import Settings
from app.llm.budget import ModelInputLimitError
from app.llm.client import DeepSeekClient, ModelClientError
from app.schemas.document.answer import AnswerResult
from app.services.chat.conversation import natural_chat_response
from app.services.chat.events import event_sink
from tests.test_chat_rag import response

"""文本请求测试函数：自然回答不要求JSON，模型服务截断不能伪装为成功。"""


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_native_request_does_not_require_json(monkeypatch, finish):
    calls = []

    def invoke(self, messages, **kwargs):
        calls.append(kwargs)
        return AIMessage(content="## 苏州\n可以逛园林。",
                         response_metadata={"finish_reason": finish})

    monkeypatch.setattr(ChatOpenAI, "invoke", invoke)
    with httpx.Client() as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        if finish == "stop":
            result = model.generate_text([{"role": "user", "content": "想去苏州"}])
            assert result.startswith("## 苏州")
        else:
            with pytest.raises(ModelClientError):
                model.generate_text([{"role": "user", "content": "想去苏州"}])
    assert calls == [{}]


"""流式文本测试函数：只展示公开回答草稿，分片不能夹带提供方思考字段。"""


def test_native_stream_only_emits_answer_text(monkeypatch):
    events = []

    def stream(self, messages, **kwargs):
        assert "response_format" not in kwargs
        yield AIMessageChunk(content="苏州园林适合慢慢逛。" * 3,
                             additional_kwargs={"reasoning_content": "未公开思考"})
        assert any(kind == "draft" for kind, _ in events)
        yield AIMessageChunk(content="也可以看古街。", response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ChatOpenAI, "stream", stream)
    token = event_sink.set(lambda kind, data: events.append((kind, data)))
    try:
        with httpx.Client() as http:
            model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
            text = model.generate_text([])
        assert events[0][0] == "reset"
        assert events[-1] == ("draft", {"text": text})
        assert "未公开思考" not in str(events)
    finally:
        event_sink.reset(token)


"""正文合成测试函数：自然模型可补常识，已核对卡片保持原样而不是从文字重建。"""


def test_native_synthesis_preserves_verified_cards_and_current_message_once():
    model = Mock()
    model.generate_text.return_value = "苏州还可以看看平江路和园林。"
    knowledge = AnswerResult.model_validate({"status": "answered", "sources": [], "points": [
        {"text": "拙政园是苏州景点。", "source_ids": [1]}]})
    original = response("推荐热门景点")
    result = natural_chat_response(original, model, "本轮查询苏州", knowledge=knowledge,
        history_messages=[{"role": "user", "content": "想去苏州"},
                          {"role": "assistant", "content": "喜欢园林吗？"}])
    assert result.knowledge is knowledge
    assert result.reply == model.generate_text.return_value
    messages = model.generate_text.call_args.args[0]
    assert messages[-1] == {"role": "user", "content": "推荐热门景点"}
    assert sum(item["content"] == "推荐热门景点" for item in messages) == 1


"""总量阻断测试函数：每次真实客户端调用都检查总量，必要状态超限不向提供方发送。"""


@pytest.mark.parametrize("method", ["generate_text", "generate_json"])
def test_client_checks_input_before_provider_call(monkeypatch, method):
    invoke = Mock()
    monkeypatch.setattr(ChatOpenAI, "invoke", invoke)
    with httpx.Client() as http:
        model = DeepSeekClient(Settings(deepseek_api_key=SecretStr("fake")), http)
        with pytest.raises(ModelInputLimitError):
            getattr(model, method)([{"role": "user", "content": "长" * 64001}])
    invoke.assert_not_called()
