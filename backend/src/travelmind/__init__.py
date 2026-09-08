"""TravelMindAI 正式后端包。

1. __init__.py 把当前目录定义为一个普通 Python 包。
2. 安装本项目后，可以在其他文件中使用 import travelmind。
3. 包名是 travelmind；安装项目的名字 travelmind-backend 不能用于 import。

这里故意只有说明，没有创建配置对象、模型客户端或数据库连接。
Python 首次 import 一个模块时会执行模块顶层代码；因此把联网调用写在这里，
会使“仅仅导入包”就产生费用或报错。真正的初始化留给后续应用启动过程。
"""
