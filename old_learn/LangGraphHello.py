import os
from typing import TypedDict, Annotated, List

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.constants import START,END
from langgraph.graph import StateGraph, add_messages

load_dotenv(encoding="utf-8")

# ========= 1. 定义状态 =============
# 存储对话信息
class MemoryState(TypedDict):
    # Annotated+add_messages表示自动追加信息
    messages: Annotated[List, add_messages]

# ========= 2. 定义大模型 ===========
llm = ChatOpenAI(
    model="deepseek-v4-pro",
    api_key=os.getenv("DS_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)

# ========= 3. 定义节点函数 ===========
# 调用大模型，把回复加入到state["messages"]中,   :是字典！！！
def model_node(state: MemoryState):
    reply = llm.invoke(state["messages"])
    return {"messages": [reply]}

# ========= 4. 构建图结构 ===========
graph = StateGraph(MemoryState)

graph.add_node("model",model_node) # 添加节点叫model

graph.add_edge(START,"model")
graph.add_edge("model",END)

# ========= 5. 编译 ===========
app = graph.compile()

# ========= 6. 运行 ===========
result = app.invoke({"messages": "请用一句话解释LangGraph是什么"})
print("模型回答：", result["messages"][-1].content)

print(app.get_graph().print_ascii())
print(app.get_graph().draw_mermaid())