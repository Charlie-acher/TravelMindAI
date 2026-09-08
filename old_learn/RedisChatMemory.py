import os

import redis
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableWithMessageHistory, RunnableConfig
from langchain_redis import RedisChatMessageHistory
import logging

# 加载.env文件环境变量
load_dotenv(encoding="utf-8")

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"

# 创建redis客户端 decode_response=True表示返回的数据自动解码为字符串
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# 初始化模型
model = init_chat_model(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

# 提示词模板
prompt = ChatPromptTemplate.from_messages([
    MessagesPlaceholder("history"),
    ("human", "{question}")
])

""" 获取或创建redis会话历史 """
def get_session_history(session_id:str) -> RedisChatMessageHistory:
    history = RedisChatMessageHistory(
        session_id=session_id,
        redis_url=REDIS_URL
    )
    return history

# 创建带历史的链
chain = RunnableWithMessageHistory(
    prompt | model,
    get_session_history,
    input_messages_key="question",
    history_messages_key="history"
)

config = RunnableConfig(configurable={"session_id": "user-002"})

# 主循环
print("开始对话")
while True:
    question = input("\n输入问题：")
    if question.lower() in ["exit", "quit"]:
        break

    response = chain.invoke({"question": question}, config)
    logger.info(f"AI回答：{response.content}")

    redis_client.save()
