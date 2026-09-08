from datetime import datetime
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate

"""
构造方法生成格式化提示词模板（新建对象）
"""
# template = PromptTemplate(
#     template="你是一个专业的{role}工程师，请根据我的问题作出回答，我的问题是：{question}",
#     # 提示输入的变量名称列表
#     input_variables=["role", "question"],
# )
#
# prompt = template.format(role="python", question="如何使用python实现一个简单的爬虫？")
# print(prompt)


"""
from_template方法生成格式化提示词模板
"""
# template = PromptTemplate.from_template("你是一个专业的{role}工程师，请根据我的问题作出回答，我的问题是：{question}")
# # 格式化提示词为字符串
# prompt = template.format(role="后端开发", question="快速排序怎么写？")
# print(prompt)


"""
部分提示词模板（预先固定+动态填充）
"""
# template = PromptTemplate.from_template(
#     template="现在时间是：{time}，我的问题是{question}",
#     partial_variables={"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
# )
#
# prompt = template.format(question="今天是几号？")
# print(prompt)


"""
使用 ChatPromptTemplate构造方法
    tuple构成列表：[(role,content)]
    dict构成列表：[{"role":...,"content":...}]
"""
chatPromptTemplate = ChatPromptTemplate(
    [
        ("system","你是一个AI工程师，你的名字是{name}"),
        ("human","你能帮我做什么？"),
        # 相当于话术规范，不指定就由大模型发挥
        ("ai","我能够回答你的{question}，请告诉我你的{question}。"),
        ("human","{user_input}")
    ]
)

# format_messages方法格式化提示词
prompt = chatPromptTemplate.format_messages(
    name="小智",question="问题",user_input="7+5等于多少")
print(prompt)


"""
from_messages：将模板变量替换后，直接生成消息列表 (List[BaseMessage])
"""
template = ChatPromptTemplate.from_messages(
    [
        {"role": "system", "content": "你是一个AI工程师，你的名字是{name}"},
        {"role": "human", "content": "{question}？"},
    ]
)
# 或者 prompt = template.from_messages(name="小松",question="什么是解包")
prompt = template.format_messages(**{"name": "小松", "question": "什么是解包"})
print(prompt)