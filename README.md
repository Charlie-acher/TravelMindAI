# TravelMindAI

TravelMindAI 是旅行需求聊天与共享资料检索应用。首页采用青绿淡彩山景；点击“开始规划”或发送旅行问题时才校验登录，有效登录态直接进入智能体，否则弹出登录/注册。发送入口在登录成功后自动回答原问题；普通用户可保存、切换、重命名和删除自己的会话；管理员管理旅行知识库文件。聊天支持周边商户查询、逐日行程草稿、指定日期修改和撤销。

## 主要功能与实现

- **账号与会话**：用户名/密码自助注册只创建普通用户；密码为6～12位（含边界）。左下角显示首字母头像和完整账号名。管理员由部署命令创建。Argon2保存口令哈希，服务端登录态只存随机令牌的哈希；Cookie有效期7天，HttpOnly、SameSite Strict，生产或HTTPS启用Secure。请求在后端按登录账号校验会话归属，跨用户编号返回404；用户历史按更新时间和编号分页。
- **旅行对话**：Vue 3 + TypeScript聊天页调用DeepSeek理解本轮条件，Python校验、合并和追问；成功轮次及引用依据保存到PostgreSQL。刷新或重新登录可选择本人旧会话，新建对话居中展示品牌和输入框，首次发送才创建记录；历史栏支持搜索已加载的对话、重命名和三点菜单删除；删除确认后，同一事务删除本人会话及对应消息、需求和行程版本。
- **周边查询**：标准 Agent 工具结合聊天上下文查询餐馆或住宿，支持价格、口味和距离追问，也能以推荐商户继续查周边。百度地图 MCP 返回真实位置，由程序筛选4分以上评分、圆形范围及参考消费；消费不是实时酒店房价。
- **地图实时查询**：通过 LangChain 原生 MCPAdapter 接入百度官方只读工具，按需查询地点、天气、路线和路况；回答保留本轮工具依据。地图统一配置后端 `BAIDU_MAP_API_KEY`，不再使用高德或独立百度 HTTP 客户端。景点、商户及行程卡片统一打开百度网页地图。
- **共享知识库**：管理员上传PDF、DOCX、TXT或Markdown，按城市及住宿/景点/餐馆类别管理。原文与片段在PostgreSQL，Embedding向量在Milvus；聊天在服务端固定共享范围检索并回查原文。管理员可查看切片和索引任务，普通用户不直接管理文件。
- **逐日行程与修改**：必要条件齐全后进入LangGraph，单文本模型按需选择知识库、网页和地图工具，程序核对地点、时间、节奏及预算，生成逐日草稿。支持文字修改、实际差异摘要和撤销最近有效修改，需求、行程与聊天原子保存。
- **预算与证据边界**：费用使用Python Decimal及固定演示费率；地点与地图回答保留工具依据。单独路线问答可以查询百度路线，但逐日草稿的交通预留仍是建议，尚未逐段核实路线、开放时间或实时价格。
- **接口与工程**：FastAPI、Pydantic、SQLAlchemy和Alembic提供HTTP契约、数据持久化与显式迁移；Swagger UI、健康检查和请求编号便于调试。

聊天支持私人附件：点击加号选择，或将文件拖入输入框自动上传；卡片显示类型、名称、大小和待发送状态，可补充要求后点击发送识别。PNG/JPEG/WebP路线图交给百炼Qwen读取，文字PDF、DOCX、TXT和Markdown复用现有解析。每条最多3份；PDF最大30MB（30,000,000字节），其他附件最大10MiB。结果与原文依据随消息保存，附件不进入共享知识库。当前仅识别和解释附件，附件更新行程、独立审查、持久暂停恢复和文件替换仍待后续实现。

图片识别需在`backend/.env`显式配置`TRAVELMIND_VISION_BASE_URL`、`TRAVELMIND_VISION_API_KEY`及`TRAVELMIND_VISION_MODEL`（默认`qwen3-vl-plus`）；不自动借用Embedding密钥。原件保存在`temp/uploads/conversations/`，只能通过所属会话的认证接口下载。删除会话时同时清理原件；磁盘失败保留清理记录，修复磁盘权限后可重试删除，或从`backend`执行`./.venv/Scripts/python.exe -m scripts.cleanup_attachment_files --env-file .env`补清。该命令只处理有删除记录且会话已不存在的文件。

## 目录与本地文件

| 目录 | 职责与保留规则 |
|---|---|
| `backend/app`、`frontend/src` | 正式后端和前端源码 |
| `backend/migrations`、`backend/tests` | 数据库迁移和自动化测试，纳入Git |
| `backend/evals/*.json` | 固定评测输入，纳入Git；不能按报告清理 |
| `backend/scripts` | 检查、评测和账号管理命令 |
| `deploy`、`docs` | 部署配置、当前架构与阶段开发说明 |
| `data/chinatravel`、`data/lvbangpt` | 本地数据集，不提交Git |
| `temp/uploads`、`temp/knowledge` | 必须保留的上传原文和补充资料，不提交Git |
| `temp/logs`、`temp/reports`、`temp/runtime` | 日志、评测输出和验收临时文件，不提交Git |

正式应用只使用`backend/.env`，配置模板为`backend/.env.example`。`.venv`、`node_modules`、`dist`及检查缓存继续使用工具默认位置，并由Git忽略。

上传原文、补充资料和备份必须保留；清理日志时请确认文件已不再使用，不要递归清空`temp`。

## 本地启动

需要Python 3.11、Node.js 20.19+或22.12+、PostgreSQL；知识库索引/检索还需配置Embedding与Milvus。在`backend`目录复制`.env.example`为`.env`，填写数据库和模型配置；真实密钥只放在本地配置文件。先安装依赖并显式迁移，再启动后端：

```powershell
cd backend
python -m pip install uv==0.12.10
python -m uv sync --locked --python 3.11 --link-mode copy
./.venv/Scripts/python.exe -X utf8 -m alembic -x env_file=.env upgrade head
New-Item -ItemType Directory -Force ../temp/logs | Out-Null
./.venv/Scripts/python.exe -X utf8 -m uvicorn app.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8000 2>&1 | Tee-Object ../temp/logs/backend.log
```

新环境可在首次提问后注册普通用户。部署时先初始化默认管理员 `admin / 123456`；重复执行保留已有密码，不在每次应用启动时重置：

```powershell
./.venv/Scripts/python.exe -X utf8 -m scripts.manage_accounts --env-file .env init-admin
```

另开终端启动前端：

```powershell
cd frontend
npm ci
New-Item -ItemType Directory -Force ../temp/logs | Out-Null
npm run dev 2>&1 | Tee-Object ../temp/logs/frontend.log
```

访问[旅行对话](http://127.0.0.1:5173/)或[后端接口文档](http://127.0.0.1:8000/docs)。生产部署应让`/api`走同源反向代理；浏览器写请求带`X-Requested-With: TravelMindAI`并校验Origin。旧`local-demo`资料存在时资料接口返回503，须先在维护窗口完成共享范围迁移，不能仅改数据库字段。具体迁移方法和当前数据字典见[架构说明](docs/08-架构设计说明.md)。

## 预算规则

固定`demo-cny-v1`演示费率按2–5天、1–8人计算：经济/舒适型住宿每间每晚200/400元，城际交通每人400元，市内交通每人每天30元，餐饮80元，门票活动60元，另计分类小计10%的预留金。金额响应以字符串返回，超支时仍返回计算结果。
