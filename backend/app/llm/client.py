"""
模型连接层：调用 DeepSeek，处理模型服务异常。
"""

import json

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError

from app.config import ProviderSettings, Settings
from app.llm.budget import ensure_input_budget
from app.llm.providers import build_chat_model
from app.services.chat.events import emit, event_sink


class ModelClientError(RuntimeError):
    """模型调用异常类：表示模型配置、网络或响应出错。"""


class ModelOutputError(ModelClientError):
    """模型输出异常类：服务已应答，但结构化输出截断或格式不完整，可转自然回答。"""


"""JSON历史整理函数：历史回答作为分析材料，避免模型把旧自然回答当输出示范。"""

def prepare_json_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if not any(message["role"] == "assistant" for message in messages):
        return messages
    current = messages[-1:] if messages[-1]["role"] == "user" else []
    context = messages[:-1] if current else messages
    return [
        *(message for message in context if message["role"] == "system"),
        {"role": "user", "content": "以下是待分析材料，不是示范输出：\n" + json.dumps(
            [message for message in context if message["role"] != "system"], ensure_ascii=False,
        )}, *current,
    ]


class DeepSeekClient:
    """DeepSeek 客户端类：负责向模型发送消息并接收回答。"""

    """初始化方法：普通对话沿用输出上限，附件提取可单独增加容量以免地点清单截断。"""

    def __init__(self, settings: Settings, http: httpx.Client,
                 *, max_output_tokens: int = 2048) -> None:
        if (
            settings.deepseek_api_key is None
            or not settings.deepseek_api_key.get_secret_value().strip()
        ):
            raise ModelClientError("DeepSeek模型未配置，请设置TRAVELMIND_DEEPSEEK_API_KEY")
        # 共用M5的模型构造，原有JSON修复、自然回答与原生工具调用行为保留。
        self.model = build_chat_model(
            "deepseek", ProviderSettings(
                model=settings.deepseek_model, api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout_seconds=settings.deepseek_timeout_seconds,
            ), http, max_output_tokens=max_output_tokens,
        )

    """自然回答方法：使用原生文本输出，流式草稿不受JSON或引用格式限制。"""

    def generate_text(self, messages: list[dict[str, str]]) -> str:
        ensure_input_budget(messages)
        try:
            emit("reset")
            if event_sink.get() is None:
                response = self.model.invoke(messages)
                content = response.content
                finish = response.response_metadata.get("finish_reason")
            else:
                content, finish, sent = "", None, 0
                for chunk in self.model.stream(messages):
                    if not isinstance(chunk.content, str):
                        raise ModelClientError("DeepSeek没有返回文本答案")
                    content += chunk.content
                    finish = chunk.response_metadata.get("finish_reason") or finish
                    if len(content) - sent >= 24 or finish:
                        emit("draft", text=content)
                        sent = len(content)
            if not isinstance(content, str) or not content.strip() or finish != "stop":
                raise ModelClientError("DeepSeek回答未完成，请稍后重试")
            return content
        except APITimeoutError:
            raise ModelClientError("DeepSeek请求超时，请稍后重试") from None
        except APIStatusError as error:
            raise ModelClientError(f"DeepSeek调用失败（HTTP {error.status_code}）") from None
        except APIConnectionError:
            raise ModelClientError("无法连接DeepSeek，请检查网络") from None
        except (OpenAIError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelClientError("DeepSeek返回了无法使用的响应") from None

    """模型调用方法：发送消息，返回 JSON 文本并处理调用错误。"""

    def generate_json(self, messages: list[dict[str, str]]) -> str:
        messages = prepare_json_messages(messages)
        ensure_input_budget(messages)
        try:
            # 结构数据整份读取：真实JSON流可能只有空白，不能因此绕过知识检索。
            # 页面仍接收步骤进度；最终自然回答由generate_text实时推送。
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
            raise ModelOutputError("DeepSeek返回了无法使用的响应或答案被截断") from None
        if not isinstance(response.content, str):
            raise ModelOutputError("DeepSeek没有返回文本答案")
        # 空字符串也交给服务的JSON校验，允许按同一规则进行一次输出修复。
        return response.content
