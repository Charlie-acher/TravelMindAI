import os
import sys
from typing import TypedDict, Annotated

from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

# 加载.env文件环境变量
load_dotenv(encoding="utf-8")

model=init_chat_model(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

"""
Annotated 增强型类型工具，元数据是补充说明/规则配置
Annotated[类型，元数据1，元数据2，..]
"""
class Animal(TypedDict):
    animal: Annotated[str,"动物"]
    emoji: Annotated[str,"表情"]

class AnimalList(TypedDict):
    animals: Annotated[list[Animal],"动物与表情列表"]

messages = [{"role": "user", "content": "任意生成三种动物，以及他们的emoji表情"}]
model_structured_output = model.with_structured_output(AnimalList)
response = model_structured_output.invoke(messages)
sys.stdout.reconfigure(encoding="utf-8")
print(response)

"""
Pydantic 自定义输出
"""
class Movie(BaseModel):
    """一部带有详细信息的电影。"""
    title: str = Field(description="电影标题")
    year: int = Field(description="电影上映年份")
    director: str = Field(description="电影导演")
    rating: float = Field(description="电影评分，满分 10 分")

model_with_structure = model.with_structured_output(Movie)
response = model_with_structure.invoke("提供关于电影《盗梦空间》的详细信息")
print(response)