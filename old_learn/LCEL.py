import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 加载.env文件环境变量
load_dotenv(encoding="utf-8")

model=init_chat_model(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

# 提示词模板
chat_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个{role}，请简短回答我的问题"),
    ("human","{question}")
])

# 创建字符串输出解析器
parser = StrOutputParser()

# 构建处理链 将提示词模板、语言模型和输出解析器结合
chain = chat_prompt | model | parser

response = chain.invoke({"role": "计算机专家", "question": "langchain和langgraph有什么区别？"})
print(response)