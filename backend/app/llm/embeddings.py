"""模型连接层：把少量文字交给向量服务，检查返回结果是否能与原文一一对应。

使用兼容 /embeddings 的文本接口，由试跑命令调用；不保存向量或修改资料状态。
"""

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.services.usage import token_usage, usage_call


class EmbeddingError(RuntimeError):
    """向量调用异常类：向调用者说明配置、网络或返回数据的问题。"""


class _EmbeddingItem(BaseModel):
    """单条响应类：保存提供方给出的输入序号及数字列表。"""

    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    index: int = Field(ge=0)  # 从0开始，对应请求中的文字顺序。
    embedding: list[float] = Field(min_length=1, max_length=65536)


class _EmbeddingResponse(BaseModel):
    """响应检查类：读取本次模型名和向量，用量已由调用账本单独保存。"""

    model_config = ConfigDict(strict=True)
    model: str
    data: list[_EmbeddingItem] = Field(min_length=1, max_length=8)


class EmbeddingBatch(BaseModel):
    """向量结果类：同时保存数字和生成它们的模型信息，避免后续混用不同向量空间。"""

    provider: str
    model: str
    dimensions: int
    version: str  # 本项目自定标签，不代表已锁定提供方内部模型权重。
    vectors: list[list[float]]  # 顺序与输入文字一致。


class EmbeddingClient:
    """向量客户端类：用独立配置发送一小批文字，并拒绝不能使用的响应。"""

    """初始化函数：检查配置是否完整，借用调用方管理的HTTP连接。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        if (
            not settings.embedding_provider
            or not settings.embedding_model
            or settings.embedding_base_url is None
            or settings.embedding_dimensions is None
            or not settings.embedding_version
            or settings.embedding_api_key is None
            or not settings.embedding_api_key.get_secret_value().strip()
        ):
            raise EmbeddingError("Embedding未配置完整，请填写独立的TRAVELMIND_EMBEDDING_*配置")
        address = settings.embedding_base_url
        if address.username or address.password or address.query or address.fragment:
            raise EmbeddingError("Embedding地址不能包含账号、密码、查询参数或片段")
        if address.scheme != "https" and address.host not in {"127.0.0.1", "localhost", "[::1]"}:
            raise EmbeddingError("远程Embedding地址必须使用HTTPS")
        self._url = str(address).rstrip("/") + "/embeddings"
        self._key = settings.embedding_api_key
        self._provider = settings.embedding_provider
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._version = settings.embedding_version
        self._timeout = settings.embedding_timeout_seconds
        self._http = http

    """向量生成函数：发送1至8段文字，按输入顺序返回通过校验的向量。"""

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        # 沿用当前片段的800字符上限；这是应用限额，不冒充提供方的token计算。
        # ponytail: 本步只做小批量试跑，正式索引时再按选定模型补token预算和调度。
        if not 1 <= len(texts) <= 8 or any(not text.strip() or len(text) > 800 for text in texts):
            raise EmbeddingError("每次需提供1至8段非空文字，每段最多800个字符")
        try:
            with usage_call("qwen" if self._provider == "aliyun" else self._provider,
                            self._model, self._url, "embedding") as call:
                response = self._http.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                    json={"model": self._model, "input": texts, "encoding_format": "float"},
                    timeout=self._timeout,
                    follow_redirects=False,  # 配置地址有误时直接提示，不跟随跳转发送资料。
                )
                response.raise_for_status()
                try:
                    body = response.json()
                    call.update(token_usage(body.get("usage") if isinstance(body, dict) else None))
                except ValueError:
                    pass  # 原有响应校验负责报错，费用未知不覆盖原异常。
        except httpx.TimeoutException:
            raise EmbeddingError("Embedding请求超时，请稍后重试") from None
        except httpx.HTTPStatusError as error:
            raise EmbeddingError(
                f"Embedding调用失败（HTTP {error.response.status_code}）"
            ) from None
        except httpx.RequestError:
            raise EmbeddingError("无法连接Embedding服务，请检查网络与配置") from None
        try:
            payload = _EmbeddingResponse.model_validate_json(response.content)
        except ValidationError:
            # 不透传提供方正文或校验原值，防止终端带出资料和密钥。
            raise EmbeddingError("Embedding返回了无法使用的数据") from None
        items = sorted(payload.data, key=lambda item: item.index)
        if payload.model != self._model or [item.index for item in items] != list(
            range(len(texts))
        ):
            raise EmbeddingError("Embedding返回的模型、数量或序号与请求不符")
        if any(
            len(item.embedding) != self._dimensions or not any(item.embedding) for item in items
        ):
            raise EmbeddingError("Embedding返回的维度不符或向量全为零")
        return EmbeddingBatch(
            provider=self._provider,
            model=self._model,
            dimensions=self._dimensions,
            version=self._version,
            vectors=[item.embedding for item in items],
        )
