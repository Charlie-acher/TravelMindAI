# TravelMindAI

TravelMindAI 是旅行需求聊天与共享资料检索应用。打开网站即可输入旅行问题，首次发送时弹出登录/注册，成功后自动回答原问题；普通用户可保存多轮对话、切换历史会话；管理员管理旅行知识库文件。后端保留结构化预算与版本草稿接口，聊天目前负责整理需求与证据回答，尚不自动生成逐日行程。

## 主要功能与实现

- **账号与会话**：用户名/密码自助注册只创建普通用户；密码为6～12位（含边界）。右上角显示首字母头像和账号名。管理员由部署命令创建。Argon2保存口令哈希，服务端登录态只存随机令牌的哈希；Cookie有效期7天，HttpOnly、SameSite Strict，生产或HTTPS启用Secure。请求在后端按登录账号校验会话归属，跨用户编号返回404；用户历史按更新时间和编号分页。
- **旅行对话**：Vue 3 + TypeScript聊天页调用DeepSeek理解本轮条件，Python校验、合并和追问；成功轮次及引用依据保存到PostgreSQL。刷新或重新登录可选择本人旧会话，新建对话保留历史。
- **共享知识库**：管理员上传PDF、DOCX、TXT或Markdown，按城市及住宿/景点/餐馆类别管理。原文与片段在PostgreSQL，Embedding向量在Milvus；聊天在服务端固定共享范围检索并回查原文。管理员可查看切片和索引任务，普通用户不直接管理文件。
- **预算与草稿接口**：使用Python Decimal的固定演示费率计算费用，支持原子保存需求与行程版本。费率不代表实时价格，当前草稿尚无自动生成的逐日活动。
- **接口与工程**：FastAPI、Pydantic、SQLAlchemy和Alembic提供HTTP契约、数据持久化与显式迁移；Swagger UI、健康检查和请求编号便于调试。

用户私人对话附件、图片理解、LangGraph行程智能体和文件替换仍未接入。

## 本地启动

需要Python 3.11、Node.js 20.19+或22.12+、PostgreSQL；知识库索引/检索还需配置Embedding与Milvus。在`backend`目录复制`.env.example`为`.env`，填写数据库和模型配置；真实密钥只放在本地配置文件。先安装依赖并显式迁移，再启动后端：

```powershell
cd backend
python -m pip install uv==0.12.10
python -m uv sync --locked --python 3.11 --link-mode copy
./.venv/Scripts/python.exe -X utf8 -m alembic -x env_file=.env upgrade head
./.venv/Scripts/python.exe -X utf8 -m uvicorn app.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8000
```

新环境可在首次提问后注册普通用户。部署时先初始化默认管理员 `admin / 123456`；重复执行保留已有密码，不在每次应用启动时重置：

```powershell
./.venv/Scripts/python.exe -X utf8 -m scripts.manage_accounts --env-file .env init-admin
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

访问[旅行对话](http://127.0.0.1:5173/)或[后端接口文档](http://127.0.0.1:8000/docs)。生产部署应让`/api`走同源反向代理；浏览器写请求带`X-Requested-With: TravelMindAI`并校验Origin。旧`local-demo`资料存在时资料接口返回503，须先在维护窗口完成共享范围迁移，不能仅改数据库字段。具体迁移方法和当前数据字典见[架构说明](docs/08-架构设计说明.md)。

## 预算规则

固定`demo-cny-v1`演示费率按2–5天、1–8人计算：经济/舒适型住宿每间每晚200/400元，城际交通每人400元，市内交通每人每天30元，餐饮80元，门票活动60元，另计分类小计10%的预留金。金额响应以字符串返回，超支时仍返回计算结果。
