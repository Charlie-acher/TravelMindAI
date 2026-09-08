import os

import asyncio
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage
from pyexpat.errors import messages

# 加载.env文件环境变量
load_dotenv(encoding="utf-8")

# 配置日志
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

"""
实例化模型v1
"""
# model=init_chat_model(
#     model="qwen3.8-max",
#     model_provider="openai",
#     api_key=os.getenv("QWEN_API_KEY"),
#     base_url="https://ws-19x4uyg6j1ouw0hr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
# )
#
# print(model.invoke("你是谁").content)

"""
实例化模型v2
"""
model=init_chat_model(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com"
)

# 构建消息模型
messages = [
    SystemMessage("你是一个律师助手，只回答法律问题，非法律问题无可奉告"),
    HumanMessage("广告法具体是什么？")
]

"""
同步调用大模型(invoke)
"""
# print(model.invoke(messages).content)

"""
异步调用大模型(async invoke)
"""
# async def main():
#     # ainvoke 需await修饰
#     response = await model.ainvoke(messages)
#     print(response.content)

"""
流式调用大模型(stream)
"""
# response = model.stream(messages)
# # 流式打印结果
# for chunk in response:
#     # 刷新缓冲区
#     print(chunk.content,end="",flush=True)
# print("\n")


"""
异步流式调用大模型(async stream)
"""
async def main():
    # astream 无需await修饰，直接赋值
    response = model.astream(messages)
    # 异步遍历生成器 必须使用 async for
    async for chunk in response:
        print(chunk.content, end="", flush=True)
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())