"""
模型连接层：调用 DeepSeek，处理模型服务异常。
"""

import httpx
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError

from app.config import Settings
from app.services.chat.events import draft_text, emit, event_sink


class ModelClientError(RuntimeError):
    """模型调用异常类：表示模型配置、网络或响应出错。"""


class DeepSeekClient:
    """DeepSeek 客户端类：负责向模型发送消息并接收回答。"""

    def __init__(self, settings: Settings, http: httpx.Client) -> None:
        if (
            settings.deepseek_api_key is None
            or not settings.deepseek_api_key.get_secret_value().strip()
        ):
            raise ModelClientError("DeepSeek模型未配置，请设置TRAVELMIND_DEEPSEEK_API_KEY")
        # 1. 创建一个模型对象；base_url决定请求发给谁。
        self.model = ChatOpenAI(
            model=settings.deepseek_model,  # 实际模型名，例如deepseek-v4-pro。
            api_key=settings.deepseek_api_key,  # 读取已校验的密钥，不在这里读.env。
            base_url="https://api.deepseek.com",  # 请求发给DeepSeek，而不是OpenAI官网。
            timeout=settings.deepseek_timeout_seconds,  # 防止网络请求一直等待。
            max_retries=0,  # 关闭SDK自动重试，输出修复只由需求服务控制一次。
            http_client=http,  # 复用入口管理的连接；无需自己调用http.post。
            http_socket_options=(),  # 保留系统代理行为，避免框架额外替换底层网络设置。
            use_responses_api=False,  # 使用DeepSeek兼容的Chat Completions接口。
            # 提供方专用字段放extra_body，SDK会原样传递给DeepSeek。
            # max_tokens放这里，避免ChatOpenAI自动改为另一种token参数名。
            extra_body={"thinking": {"type": "disabled"}, "max_tokens": 2048},
        )

    """模型调用方法：发送消息，返回 JSON 文本并处理调用错误。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        try:
            # 2. invoke表示“现在把消息发给模型”，JSON模式要求返回JSON文本。
            if event_sink.get() is not None:
                # 使用提供方真实分片；只展示公开 points 草稿，最终结果仍须完整校验。
                content, finish, last_draft = "", None, ""
                for chunk in self.model.stream(messages, response_format={"type": "json_object"}):
                    if not isinstance(chunk.content, str):
                        raise ModelClientError("DeepSeek没有返回文本答案")
                    content += chunk.content
                    finish = chunk.response_metadata.get("finish_reason") or finish
                    draft = draft_text(content)
                    # 合并很小的分片，减少页面刷新；没有人为等待或打字动画。
                    if draft != last_draft and (
                        len(draft) - len(last_draft) >= 24 or len(draft) < len(last_draft) or finish
                    ):
                        emit("draft", text=draft)
                        last_draft = draft
                if finish != "stop":
                    raise ModelClientError("DeepSeek返回了无法使用的响应或答案被截断")
                return content
            response = self.model.invoke(
                messages,
                response_format={"type": "json_object"},  # 要求JSON文本，之后再校验字段。
            )
        except APITimeoutError:
            raise ModelClientError("DeepSeek请求超时，请稍后重试") from None
        except APIStatusError as error:
            raise ModelClientError(
                f"DeepSeek调用失败（HTTP {error.status_code}），请检查配置或稍后重试"
            ) from None
        except APIConnectionError:
            raise ModelClientError("无法连接DeepSeek，请检查网络") from None
        except (OpenAIError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            # SDK/LangChain解析异常响应时也可能抛出这些错误，统一转换为可展示的消息。
            raise ModelClientError("DeepSeek返回了无法使用的响应") from None
        if response.response_metadata.get("finish_reason") != "stop":
            raise ModelClientError("DeepSeek返回了无法使用的响应或答案被截断") from None
        if not isinstance(response.content, str):
            raise ModelClientError("DeepSeek没有返回文本答案")
        # 空字符串也交给服务的JSON校验，允许按同一规则进行一次输出修复。
        return response.content
