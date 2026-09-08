import httpx
from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import JsonOutputKeyToolsParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# class FieldInfo(BaseModel):
#     a: int = Field(description="第一个参数")
#     b: int = Field(description="第二个参数")
#
# # args_schema定义参数信息
# @tool(args_schema=FieldInfo,description="计算两个整数的和")
# def add_number(a: int,b: int) -> int:
#     return a+b
# # 键值对
# res=add_number.invoke({"a" : 1, "b" : 2})
# print(res)


"""
调用天气工具
"""
import os
from dotenv import load_dotenv

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
model = init_chat_model(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

# 将模型和工具绑定
model_with_tools = model.bind_tools([get_weather])

# 提取json数据
parser = JsonOutputKeyToolsParser(key_name=get_weather.name, first_tool_only=True)

# 真正执行天气工具 查询天气
get_weather_chain = model_with_tools | parser | get_weather

# 定义提示词模板 将json天气数据转为自然语言
output_prompt = ChatPromptTemplate.from_messages([
    ("system",
     """
     你将收到一段 JSON 格式的天气数据{weather_json}，请用简洁自然的方式将其转述给用户。
     以下是天气 JSON 数据:请将其转换为中文天气描述，例如:
     北京现在天气:多云，气温 28°C，体感有点闷热(约 32°C)，湿度 75%，微风(东南风2米/秒),
     能见度很好，大约10 公里。建议穿短袖短裤。适合做户外运动。
     """)
])

# 字符串输出解析
output_parser = StrOutputParser()

# 转成自然语言通过模型给出字符串输出
output_chain = output_prompt | model | output_parser

# 完整处理链 天气查询链 -> 将天气数据包装为字典格式 -> 输出链
full_chain = get_weather_chain | (lambda x:{"weather_json":x}) | output_chain

result = full_chain.invoke("请问宁夏银川今天天气如何")
print(result)


