"""向量存储层：通过Milvus REST接口保存和搜索向量，只保存编号，不复制攻略正文。

由资料检索业务服务调用；复用已有httpx，不再引入一套数据库SDK。
"""

import json
import re
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

import httpx

from app.config import Settings


class MilvusError(RuntimeError):
    """向量存储异常类：用可展示的说明表示配置、网络和存储响应错误。"""


class MilvusStore:
    """Milvus存取类：按向量空间选择集合，并按资料归属保存和检索片段编号。"""

    """初始化函数：校验本地服务地址，根据模型配置生成固定集合名。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        if settings.milvus_url is None:
            raise MilvusError("未启用Milvus，请配置TRAVELMIND_MILVUS_URL并重启后端")
        address = settings.milvus_url
        # 本步仅支持本地免认证容器，不能把无认证配置直接用于远程服务器。
        if address.host not in {"localhost", "127.0.0.1", "[::1]"} or (
            address.username or address.password or address.query or address.fragment
        ):
            raise MilvusError("本步Milvus仅支持本机地址，且地址不能包含凭据或查询参数")
        identity = [
            settings.embedding_provider,
            str(settings.embedding_base_url or ""),
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_version,
            "plain-text-800-schema1",
        ]
        if not all(identity):
            raise MilvusError("请先配齐Embedding模型、地址、维度和版本")
        self.collection = (
            "travelmind_chunks_"
            + sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
        )
        self._dimensions = settings.embedding_dimensions
        self._url = str(address).rstrip("/") + "/v2/vectordb/"
        self._timeout = settings.milvus_timeout_seconds
        self._http = http

    """请求函数：处理HTTP和Milvus业务错误，隐藏外部响应里的资料与配置。"""

    def _post(self, operation: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._http.post(
                self._url + operation,
                json=payload,
                timeout=self._timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise MilvusError("Milvus连接或响应失败，请检查容器后重试") from None
        if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0:
            raise MilvusError("Milvus未完成操作，请检查容器状态后重试")
        if "data" not in body:
            raise MilvusError("Milvus响应缺少数据")
        return body["data"]

    """集合检查函数：只检查当前模型的集合是否存在，不创建数据。"""

    def exists(self) -> bool:
        data = self._post("collections/has", {"collectionName": self.collection})
        if not isinstance(data, dict) or type(data.get("has")) is not bool:
            raise MilvusError("Milvus集合状态无法读取")
        return bool(data["has"])

    """集合准备函数：首次建立固定字段和余弦索引，已有集合直接复用。"""

    def ensure_collection(self) -> None:
        if self.exists():
            return
        fields = [
            {
                "fieldName": "id",
                "dataType": "VarChar",
                "isPrimary": True,
                "elementTypeParams": {"max_length": "36"},
            },
            {
                "fieldName": "document_id",
                "dataType": "VarChar",
                "elementTypeParams": {"max_length": "36"},
            },
            {
                "fieldName": "owner_id",
                "dataType": "VarChar",
                "elementTypeParams": {"max_length": "100"},
            },
            {
                "fieldName": "vector",
                "dataType": "FloatVector",
                "elementTypeParams": {"dim": str(self._dimensions)},
            },
        ]
        try:
            self._post(
                "collections/create",
                {
                    "collectionName": self.collection,
                    "schema": {"autoID": False, "enableDynamicField": False, "fields": fields},
                    "indexParams": [
                        {
                            "fieldName": "vector",
                            "indexName": "vector_index",
                            "metricType": "COSINE",
                        "params": {"index_type": "AUTOINDEX"},
                        }
                    ],
                    "params": {"consistencyLevel": "Strong"},
                },
            )
        except MilvusError:
            # 另一份资料可能同时创建同名集合；只在确认已经存在时接受该结果。
            if not self.exists():
                raise

    """归属过滤函数：固定字段名，使用JSON转义值，避免拼接用户输入成为查询条件。"""

    def _filter(
        self, owner_id: str, document_id: UUID | None = None,
        document_ids: list[UUID] | None = None,
    ) -> str:
        expression = "owner_id == " + json.dumps(owner_id)
        if document_id is not None:
            expression += " and document_id == " + json.dumps(str(document_id))
        if document_ids is not None:
            expression += " and document_id in " + json.dumps(
                [str(value) for value in document_ids]
            )
        return expression

    """已有编号查询函数：读取当前资料已持久化的向量编号，供进度和断点续建使用。"""

    def indexed_ids(self, owner_id: str, document_id: UUID) -> set[UUID]:
        data = self._post(
            "entities/query",
            {
                "collectionName": self.collection,
                "filter": self._filter(owner_id, document_id),
                "outputFields": ["id"],
                "limit": 16383,
                "consistencyLevel": "Strong",
            },
        )
        try:
            if not isinstance(data, list) or len(data) >= 16383:
                raise ValueError()
            return {UUID(item["id"]) for item in data}
        except (ValueError, TypeError, KeyError):
            raise MilvusError("Milvus片段编号无法完整读取") from None

    """资料向量删除函数：跨模型版本清理当前资料，核实无残留后才允许删除正文。"""

    def delete_document(self, owner_id: str, document_id: UUID) -> None:
        collections = self._post("collections/list", {})
        if not isinstance(collections, list) or any(
            not isinstance(name, str) for name in collections
        ):
            raise MilvusError("Milvus集合列表无法读取，资料清理未完成")
        # 只处理本应用固定命名的集合；切换过模型也不能漏删旧版向量。
        for name in collections:
            if not re.fullmatch(r"travelmind_chunks_[0-9a-f]{24}", name):
                continue
            payload = {"collectionName": name, "filter": self._filter(owner_id, document_id)}
            self._post("entities/delete", payload)
            remaining = self._post("entities/query", {
                **payload, "outputFields": ["id"], "limit": 1, "consistencyLevel": "Strong",
            })
            if remaining != []:
                raise MilvusError("Milvus尚未确认资料向量全部清理，请重试删除")

    """向量保存函数：固定片段编号作为主键，重试覆盖同一条，不产生重复编号。"""

    def upsert(
        self, owner_id: str, document_id: UUID, rows: list[tuple[UUID, list[float]]]
    ) -> None:
        data = self._post(
            "entities/upsert",
            {
                "collectionName": self.collection,
                "data": [
                    {
                        "id": str(identifier),
                        "document_id": str(document_id),
                        "owner_id": owner_id,
                        "vector": vector,
                    }
                    for identifier, vector in rows
                ],
            },
        )
        if not isinstance(data, dict) or data.get("upsertCount") != len(rows):
            raise MilvusError("Milvus未确认全部向量写入，请重新读取进度")

    """语义检索函数：按余弦相似度查编号，业务层再从PostgreSQL读取真实正文。"""

    def search(
        self, owner_id: str, vector: list[float], limit: int, document_id: UUID | None = None,
        *, document_ids: list[UUID] | None = None,
    ) -> list[tuple[UUID, float]]:
        if document_ids == []:
            return []  # 空候选不能退回搜索整个归属。
        data = self._post(
            "entities/search",
            {
                "collectionName": self.collection,
                "data": [vector],
                "annsField": "vector",
                "filter": self._filter(owner_id, document_id, document_ids),
                "limit": limit,
                "outputFields": ["id"],
                "consistencyLevel": "Strong",
            },
        )
        try:
            if not isinstance(data, list):
                raise ValueError()
            results = [(UUID(item["id"]), float(item["distance"])) for item in data]
            if any(not isfinite(score) for _, score in results):
                raise ValueError()
            return results
        except (ValueError, TypeError, KeyError):
            raise MilvusError("Milvus搜索结果无法读取") from None
