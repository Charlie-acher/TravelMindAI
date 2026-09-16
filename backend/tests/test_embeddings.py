"""测试层：用本机HTTP响应检查向量调用，不能据此声称真实模型已接通。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings

"""配置准备函数：使用虚构的向量服务，避免测试读取个人密钥。"""


def embedding_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "embedding_provider": "test-provider",
        "embedding_base_url": "https://embedding.example/v1",
        "embedding_model": "test-embedding",
        "embedding_api_key": SecretStr("embedding-private-key"),
        "embedding_dimensions": 2,
        "embedding_version": "test-v1",
        "embedding_timeout_seconds": 7,
        "deepseek_api_key": SecretStr("chat-private-key"),
    }
    values.update(changes)
    return Settings(**values)


"""配置隔离测试函数：只有聊天密钥时，向量密钥必须仍然为空。"""


def test_embedding_configuration_is_independent() -> None:
    settings = Settings(deepseek_api_key=SecretStr("chat-private-key"))
    assert settings.embedding_api_key is None
    assert settings.embedding_model is None


"""响应准备函数：返回公开协议中的模型、序号、向量及用量字段。"""


def response_body(data: list[dict[str, object]]) -> dict[str, object]:
    return {
        "object": "list",
        "model": "test-embedding",
        "data": data,
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    }


"""请求与排序测试函数：验证独立凭据、正文原样发送和按序号对应输入。"""


def test_request_and_response_order() -> None:
    from app.llm.embeddings import EmbeddingClient

    requests: list[httpx.Request] = []

    """模拟响应函数：故意颠倒返回顺序，检查客户端能正确还原。"""

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=response_body(
                [
                    {"object": "embedding", "index": 1, "embedding": [0, 1]},
                    {"object": "embedding", "index": 0, "embedding": [1, 0]},
                ]
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = EmbeddingClient(embedding_settings(), http).embed([" 西湖 ", "杭州住宿"])
    assert result.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert (result.provider, result.model, result.dimensions, result.version) == (
        "test-provider",
        "test-embedding",
        2,
        "test-v1",
    )
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://embedding.example/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer embedding-private-key"
    assert json.loads(request.content) == {
        "model": "test-embedding",
        "input": [" 西湖 ", "杭州住宿"],
        "encoding_format": "float",
    }
    assert all(value == 7 for value in request.extensions["timeout"].values())


"""前置拒绝测试函数：未配置和超量输入都不能发出网络请求。"""


@pytest.mark.parametrize("texts", [[], [" "], ["字" * 801], ["西湖"] * 9])
def test_invalid_input_never_sends(texts: list[str]) -> None:
    from app.llm.embeddings import EmbeddingClient, EmbeddingError

    requests: list[httpx.Request] = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500)
        )
    ) as http:
        with pytest.raises(EmbeddingError):
            EmbeddingClient(embedding_settings(), http).embed(texts)
        with pytest.raises(EmbeddingError, match="未配置"):
            EmbeddingClient(embedding_settings(embedding_api_key=None), http)
    assert requests == []


"""错误向量测试函数：缺失、重复序号、维度错误和非法数字不能被当成有效向量。"""


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": [1, 2, 3]}],
        [{"index": 1, "embedding": [1, 2]}],
        [{"index": 0, "embedding": [1, 2]}, {"index": 0, "embedding": [1, 2]}],
        [{"index": 0, "embedding": [True, 1]}],
        [{"index": 0, "embedding": ["1", 2]}],
        [{"index": 0, "embedding": [0, 0]}],
    ],
)
def test_bad_vectors_are_rejected(data: list[dict[str, object]]) -> None:
    from app.llm.embeddings import EmbeddingClient, EmbeddingError

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body(data)))
    ) as http:
        with pytest.raises(EmbeddingError):
            EmbeddingClient(embedding_settings(), http).embed(["西湖"])


"""错误外层测试函数：错误模型、非JSON及无穷数字不能通过响应校验。"""


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        "{}",
        '{"model":"wrong","data":[{"index":0,"embedding":[1,2]}]}',
        '{"model":"test-embedding","data":[{"index":0,"embedding":[NaN,1]}]}',
        '{"model":"test-embedding","data":[{"index":0,"embedding":[Infinity,1]}]}',
    ],
)
def test_bad_response_is_rejected(body: str) -> None:
    from app.llm.embeddings import EmbeddingClient, EmbeddingError

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    ) as http:
        with pytest.raises(EmbeddingError):
            EmbeddingClient(embedding_settings(), http).embed(["西湖"])


"""网络错误测试函数：超时或HTTP错误只调用一次，并隐藏提供方正文和密钥。"""


@pytest.mark.parametrize("failure", [401, 429, 503, "timeout", "connection", 302])
def test_network_errors_do_not_retry_or_leak(failure: int | str) -> None:
    from app.llm.embeddings import EmbeddingClient, EmbeddingError

    requests: list[httpx.Request] = []

    """故障响应函数：在真正的HTTP边界模拟外部故障。"""

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-body", request=request)
        if failure == "connection":
            raise httpx.ConnectError("private-body", request=request)
        return httpx.Response(int(failure), text="private-body embedding-private-key")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(EmbeddingError) as caught:
            EmbeddingClient(embedding_settings(), http).embed(["西湖"])
    assert len(requests) == 1
    assert "private" not in str(caught.value)
