"""模型接入层：通过百炼兼容接口读取私人图片，返回待校验的JSON。"""

import base64

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAIError

from app.config import Settings
from app.llm.client import ModelClientError, ModelOutputError


class QwenVisionClient:
    """百炼视觉客户端类：只在用户发送图片时构造和调用一个视觉模型。"""

    """初始化方法：检查独立视觉配置，复用请求管理的HTTP连接。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        if not settings.vision_api_key or not settings.vision_base_url:
            raise ModelClientError("尚未配置百炼视觉模型，请配置后重试图片识别")
        self.model = ChatOpenAI(
            model=settings.vision_model, api_key=settings.vision_api_key,
            base_url=str(settings.vision_base_url), http_client=http,
            timeout=settings.vision_timeout_seconds, max_retries=0,
            use_responses_api=False, http_socket_options=(),
            extra_body={"enable_thinking": False, "max_tokens": 4096},
        )

    """图片识别方法：发送原件数据，不生成公开URL，不保存模型思考内容。"""

    def generate_image_json(self, content: bytes, mime_type: str, prompt: str) -> str:
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ModelClientError("不支持此图片格式")
        if len(content) > 10 * 1024 * 1024:
            raise ModelClientError("图片超过10MiB，请压缩后重试")
        encoded = base64.b64encode(content).decode("ascii")
        try:
            response = self.model.invoke([
                SystemMessage(content=prompt),
                HumanMessage(content=[
                    {"type": "text", "text": "识别这份路线资料，只返回JSON。"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                    }},
                ]),
            ],
                response_format={"type": "json_object"},
            )
        except (OpenAIError, ValueError, TypeError, KeyError, IndexError) as error:
            raise ModelClientError("百炼图片识别未完成，请稍后重试") from error
        if (response.response_metadata.get("finish_reason") != "stop"
                or not isinstance(response.content, str)):
            raise ModelOutputError("百炼识别结果不完整，请重试")
        return response.content
