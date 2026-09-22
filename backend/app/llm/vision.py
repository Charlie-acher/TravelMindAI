"""模型接入层：通过百炼兼容接口读取私人图片，返回待校验的JSON。"""

import base64
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAIError

from app.config import Settings
from app.llm.client import ModelClientError, ModelOutputError
from app.services.usage import observe_model, usage_call


class QwenVisionClient:
    """百炼视觉客户端类：读取用户图片或PDF渲染页，不发送公开图片链接。"""

    """初始化方法：检查独立视觉配置，复用请求管理的HTTP连接。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        if not settings.vision_api_key or not settings.vision_base_url:
            raise ModelClientError("尚未配置百炼视觉模型，请配置后重试图片识别")
        self.endpoint = str(settings.vision_base_url)
        self.timeout = settings.vision_timeout_seconds
        self.model = ChatOpenAI(
            model=settings.vision_model, api_key=settings.vision_api_key,
            base_url=str(settings.vision_base_url), http_client=http,
            timeout=settings.vision_timeout_seconds, max_retries=0,
            use_responses_api=False, http_socket_options=(),
            extra_body={"enable_thinking": False, "max_tokens": 4096},
        )

    """图片识别方法：发送原件数据，不生成公开URL，不保存模型思考内容。"""

    def generate_image_json(self, content: bytes, mime_type: str, prompt: str) -> str:
        return self.generate_images_json([(content, mime_type)], prompt)

    """多页识别方法：按输入次序提交全部页面，限制每批图像数量和上传总量。"""

    def generate_images_json(
        self, images: list[tuple[bytes, str]], prompt: str, first_page: int | None = None,
        page_numbers: list[int] | None = None,
    ) -> str:
        if not 1 <= len(images) <= 4:
            raise ModelClientError("一次最多读取4张页面图片，请拆分后重试")
        if sum(len(content) for content, _ in images) > 10 * 1024 * 1024:
            raise ModelClientError("图片超过10MiB，请压缩后重试")
        parts: list[str | dict[str, Any]] = [{"type": "text", "text": "读取全部图片，只返回JSON。"}]
        for index, (content, mime_type) in enumerate(images):
            if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ModelClientError("不支持此图片格式")
            encoded = base64.b64encode(content).decode("ascii")
            if first_page is not None or page_numbers is not None:
                page = page_numbers[index] if page_numbers else (first_page or 1) + index
                parts.append({"type": "text", "text": f"下面是PDF实际第{page}页；"
                              "引用使用此页码，忽略图内印刷页码。"})
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{mime_type};base64,{encoded}",
            }})
        try:
            with usage_call("qwen", self.model.model_name, self.endpoint, "vision"):
                response = self.model.invoke([
                    SystemMessage(content=prompt),
                    HumanMessage(content=parts),
                ],
                    response_format={"type": "json_object"},
                    extra_body={"enable_thinking": False,
                                "max_tokens": 8192 if len(images) > 1 else 4096},
                    # 多页同读最多等待120秒；总页数上限由PDF读取层控制。
                    timeout=self.timeout if len(images) == 1 else min(
                        120, self.timeout * len(images)),
                )
                observe_model(response)
        except (OpenAIError, ValueError, TypeError, KeyError, IndexError) as error:
            raise ModelClientError("百炼图片识别未完成，请稍后重试") from error
        if (response.response_metadata.get("finish_reason") != "stop"
                or not isinstance(response.content, str)):
            raise ModelOutputError("百炼识别结果不完整，请重试")
        return response.content
