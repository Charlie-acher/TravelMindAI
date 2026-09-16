"""测试层：在HTTP边界验证Milvus请求、模型隔离和错误脱敏。"""

import json
from uuid import uuid4

import httpx
import pytest

from app.config import Settings

"""配置准备函数：使用虚构地址和二维模型，不读取个人.env。"""


def vector_settings(**changes: object) -> Settings:
    values = dict(
        milvus_url="http://127.0.0.1:19530",
        embedding_provider="test",
        embedding_model="test-vector",
        embedding_dimensions=2,
        embedding_version="v1",
        embedding_base_url="https://example.com/v1",
    )
    values.update(changes)
    return Settings(**values)


"""隔离测试函数：模型、地址、维度或版本改变时必须使用不同集合。"""


def test_collection_identity_changes_with_vector_space() -> None:
    from app.services.document.vector_store import MilvusStore

    with httpx.Client() as http:
        original = MilvusStore(vector_settings(), http).collection
        for changes in [
            dict(embedding_model="other"),
            dict(embedding_dimensions=3),
            dict(embedding_base_url="https://other.example/v1"),
            dict(embedding_version="v2"),
            dict(embedding_provider="other"),
        ]:
            assert MilvusStore(vector_settings(**changes), http).collection != original


"""协议测试函数：固定片段主键upsert，检索必须带归属过滤和强一致性。"""


def test_milvus_requests_and_results() -> None:
    from app.services.document.vector_store import MilvusStore

    chunk_id, document_id = uuid4(), uuid4()
    requests: list[dict] = []

    """模拟响应函数：按实际REST操作给出对应结构。"""

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path.endswith("/has"):
            data = {"has": False}
        elif request.url.path.endswith("/query"):
            data = [{"id": str(chunk_id)}]
        elif request.url.path.endswith("/search"):
            data = [{"id": str(chunk_id), "distance": 0.9}]
        elif request.url.path.endswith("/upsert"):
            data = {"upsertCount": 1, "upsertIds": [str(chunk_id)]}
        else:
            data = {}
        return httpx.Response(200, json={"code": 0, "data": data})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        store = MilvusStore(vector_settings(), http)
        store.ensure_collection()
        store.upsert("local-demo", document_id, [(chunk_id, [1.0, 0.0])])
        assert store.indexed_ids("local-demo", document_id) == {chunk_id}
        assert store.search("local-demo", [1.0, 0.0], 5, document_id) == [(chunk_id, 0.9)]
    schema = requests[1]["schema"]
    assert schema["autoID"] is False
    assert schema["fields"][0]["isPrimary"] is True
    assert requests[-1]["consistencyLevel"] == "Strong"
    assert 'owner_id == "local-demo"' in requests[-1]["filter"]
    assert str(document_id) in requests[-1]["filter"]
    assert requests[2]["data"][0]["id"] == str(chunk_id)


"""错误测试函数：HTTP成功但Milvus业务失败也必须报错，不回显原响应。"""


@pytest.mark.parametrize("body", [{"code": 1100, "message": "private-body"}, {}, []])
def test_milvus_errors_are_not_empty_success(body: object) -> None:
    from app.services.document.vector_store import MilvusError, MilvusStore

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(MilvusError) as caught:
            MilvusStore(vector_settings(), http).exists()
    assert "private-body" not in str(caught.value)


"""删除协议测试函数：清理所有模型版本的目标资料，保留其他集合并核实没有残留。"""

@pytest.mark.parametrize("remaining", [False, True])
def test_delete_document_across_vector_versions(remaining: bool) -> None:
    from app.services.document.vector_store import MilvusError, MilvusStore

    identifier = uuid4()
    calls = []
    collections = ["travelmind_chunks_" + "a" * 24, "travelmind_chunks_" + "b" * 24]

    """响应函数：模拟旧版和新版集合，删除后仍有编号时不能当作成功。"""

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/list"):
            data = collections + ["unrelated"]
        elif request.url.path.endswith("/delete"):
            data = {"deleteCount": 1}
        else:
            data = [{"id": str(uuid4())}] if remaining else []
        return httpx.Response(200, json={"code": 0, "data": data})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        store = MilvusStore(vector_settings(), http)
        if remaining:
            with pytest.raises(MilvusError):
                store.delete_document('owner-"a', identifier)
        else:
            store.delete_document('owner-"a', identifier)
            deleted = [body["collectionName"] for path, body in calls if path.endswith("/delete")]
            assert deleted == collections
    for path, body in calls[1:]:
        expected = 'owner_id == "owner-\\\"a" and document_id == ' + json.dumps(str(identifier))
        assert body["filter"] == expected
        if path.endswith("/query"):
            assert body["consistencyLevel"] == "Strong"
