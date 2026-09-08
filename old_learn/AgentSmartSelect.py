import os

import httpx
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

"""
多工具并行调用，发送两次调用最后结果聚合
"""
load_dotenv(encoding="utf-8")

@tool(description="根据城市名称获取当前天气。调用时必须将城市名转换为英文，例如北京使用 Beijing。")
def get_weather(loc: str):
    # 调用地址
    url = "https://api.openweathermap.org/data/2.5/weather"

    params = {
        "q": loc,
        "appid": os.getenv("OPENWEATHER_API_KEY"),
        "units": "metric",
        "lang": "zh_cn"
    }
    # 发起请求
    response = httpx.get(url, params=params, timeout=30)
    data = response.json()
    return data

# 初始化模型
llm = ChatOpenAI(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

# 创建聊天提示词模板
# prompt = ChatPromptTemplate.from_messages(
#     [
#         ("system", "你是天气助手，请根据用户的问题，给出相应的天气信息"),
#         ("human", "{input}"),
#         # 占位符 消息插槽/历史对话
#         ("placeholder", "{agent_scratched}")
#     ]
# )

# 定义可用工具列表
tools = [get_weather]

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt="你是天气助手，当用户询问多个城市天气时，你需要分别调用工具获取数据，并进行分析"
)

result = agent.invoke(
    {"input":"请告诉我北京和上海今天的天气，哪个城市更热？"}
)

print(result)