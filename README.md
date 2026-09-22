# TravelMindAI

TravelMindAI 是旅行需求聊天与共享资料检索应用。首页采用青绿淡彩山景；点击“开始规划”或发送旅行问题时校验登录，登录后继续原问题。对话工作区以左侧历史、中间聊天和右侧状态/文件组织旅行信息；个人空间集中查看自己的附件与行程，管理员管理共享知识库。聊天支持周边商户查询、逐日行程草稿、指定日期修改和撤销。

## 主要功能与实现

- **账号与会话**：用户名/密码自助注册只创建普通用户；密码为6～12位（含边界）。左下角显示首字母头像和完整账号名。管理员由部署命令创建。Argon2保存口令哈希，服务端登录态只存随机令牌的哈希；Cookie有效期7天，HttpOnly、SameSite Strict，生产或HTTPS启用Secure。请求在后端按登录账号校验会话归属，跨用户编号返回404；用户历史按更新时间和编号分页。
- **旅行对话**：Vue 3 + TypeScript聊天页调用所选模型理解本轮条件，Python校验、合并和追问；成功轮次、引用依据和公开执行过程保存到PostgreSQL，刷新可恢复。首次发送才创建会话；首轮成功后自动总结短标题，手动改名优先。历史按服务端搜索和分页，支持重命名及确认删除；删除会话清理对应消息、需求和行程，独立调用账目保留。
- **会话工作区**：状态面板区分最近一次模型输入占用与累计调用消耗，按实际型号展示Token、缓存与最近调用；未知值明确保留。右侧面板可拖动、键盘调整、双击复位并记住宽度，手机改用抽屉。过程时间线显示真实知识库、网页、地图调用状态与结果摘要。
- **个人文件**：个人空间展示本人全部会话的私人附件和最新逐日行程，支持搜索、类型筛选、分页及返回来源会话；聊天右侧文件面板只展示当前会话。附件复用原件预览/下载，行程可切换保存版本并下载Markdown；管理员也不能读取其他账号的私人文件。
- **周边查询**：标准 Agent 工具结合聊天上下文查询餐馆或住宿，支持价格、口味和距离追问，也能以推荐商户继续查周边。百度地图 MCP 返回真实位置，由程序筛选4分以上评分、圆形范围及参考消费；消费不是实时酒店房价。
- **地图实时查询**：通过 LangChain 原生 MCPAdapter 接入百度官方只读工具，按需查询地点、天气、路线和路况；回答保留本轮工具依据。地图统一配置后端 `BAIDU_MAP_API_KEY`，不再使用高德或独立百度 HTTP 客户端。景点、商户及行程卡片统一打开百度网页地图。
- **共享知识库**：管理员上传PDF、DOCX、TXT或Markdown，按城市及住宿/景点/餐馆类别管理。文件管理保留标签编辑、原文/片段预览、后台任务、重试及删除；城市总数与分页来自后端。检索测试可限定城市、类别、文件及返回条数，展示真实原文和混合排序分。原文与片段在PostgreSQL，Embedding向量在Milvus；普通用户不直接管理文件。
- **逐日行程与修改**：必要条件齐全后进入LangGraph，单文本模型按需检索知识库、展开附近同节原文和查询地图，程序核对地点、时间、节奏及预算，生成逐日草稿。支持文字修改、实际差异摘要和撤销最近有效修改，需求、行程与聊天原子保存。
- **审查与接续**：草稿经过独立规则复查和模型审查，自动返工最多两次；缺项或需要取舍时保存等待状态，可补充条件、采用合规候选或保留现状。PostgreSQL 保存编排检查点，服务重启后通过原消息重试或继续回复恢复；断线重试不会重复保存行程版本。
- **模型选择与故障切换**：输入框可选DeepSeek、Kimi、Qwen，选择覆盖普通回答、结构提取和原生工具。优先使用所选模型，可用性故障时切备用并显示实际使用模型；流式正文开始后不跨模型拼接。支持停止后保留输入并重试。
- **按需研究协作**：主规划助手可委派独立研究助手核对来源与地点，返回后继续排程；程序规则与独立模型审核把关。相邻地点按百度路线估时核对预留时间，查询失败保留未知标记。
- **预算与证据边界**：费用使用Python Decimal及固定演示费率；地点与地图回答保留工具依据。路线估时用于核对交通预留，不保证实际耗时；开放时间与实时价格仍需另行确认。
- **接口与工程**：FastAPI、Pydantic、SQLAlchemy和Alembic提供HTTP契约、数据持久化与显式迁移；Swagger UI、健康检查和请求编号便于调试。

聊天支持私人附件：点击加号选择，或将文件拖入输入框自动上传；卡片显示类型、名称、大小和待发送状态，可补充要求后点击发送识别。配置MinerU后，PDF与PNG/JPEG/WebP执行页面OCR，DOCX读取原生结构，TXT和Markdown直接读文字；PDF图片区域与路线图由现有百炼Qwen补读。每条最多3份；PDF最大30MB（30,000,000字节），其他附件最大10MiB。完整解析正文按会话私有保存，提取失败可复用；地点及页码依据随消息保存，不进入共享知识库。附件按本轮用途交给现有智能体，不新增独立智能体。

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

知识库使用本机Docker部署时，从项目根目录执行：

```powershell
docker compose -f deploy/milvus/compose.yaml up -d
docker compose -f deploy/milvus/compose.yaml ps
```

配置会先等待etcd健康，再启动Milvus；两者沿用同一命名卷中的原有数据。不要执行`down -v`或清空数据卷。后端的Milvus地址仍为`http://127.0.0.1:19530`。

## 工作区上下文容量配置

状态面板将各实际型号最近一次聊天调用的输入Token除以该型号容量；它不是累计Token占窗口的比例。容量由`backend/.env`的`TRAVELMIND_MODEL_CONTEXT_WINDOWS`提供，格式为“型号→正整数Token”的JSON对象；空映射或未知型号保持`null`，页面显示暂不可用。新增配置后重启后端。

`backend/.env.example`按2026-09-22资料列出DeepSeek Flash（`deepseek-flash`及`deepseek-v4.1-flash`）1048576、Qwen3.8 Max及两种0902快照别名1000000、`kimi-k2.6`262144（256K）。具体依据见[DeepSeek容量说明](https://api-docs.deepseek.com/quick_start/pricing/)、[DeepSeek精确窗口配置](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)、[Qwen3.8 Max上下文限制](https://help.aliyun.com/zh/model-studio/qwen3-8-max)、[Kimi开放平台](https://platform.kimi.com/)。更换型号时按供应商当前说明核对；此配置仅用于展示容量，不修改模型调用、输入裁剪或费用规则。

## 聊天附件的 MinerU 部署（Windows）

MinerU 4.0.4使用独立虚拟环境，本机API只监听`127.0.0.1:8010`。FastAPI通过已有HTTP客户端连接，无须将模型依赖安装进backend。默认使用ONNX CPU小模型与llama.cpp视觉模型；实际速度取决于机器、页面和图片补读数量。本项目已核对的Windows依赖锁保存在`deploy/mineru/requirements.lock.txt`。

在项目根目录安装并下载模型：

```powershell
python -m uv venv deploy/mineru/.venv --python 3.11
python -m uv pip sync deploy/mineru/requirements.lock.txt --python deploy/mineru/.venv/Scripts/python.exe --link-mode copy
$env:MINERU_HOME = Join-Path (Get-Location) 'deploy/mineru/.runtime'
./deploy/mineru/.venv/Scripts/mineru-kit.exe models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source modelscope
./deploy/mineru/.venv/Scripts/mineru-kit.exe models verify --tier standard --small-backend onnx --vlm-engine llama-cpp
New-Item -ItemType Directory -Force temp/logs | Out-Null
./deploy/mineru/start.ps1 2>&1 | Tee-Object temp/logs/mineru.log
```

`backend/.env`设置以下配置，执行迁移后重启后端：

```dotenv
TRAVELMIND_MINERU_BASE_URL=http://127.0.0.1:8010
TRAVELMIND_MINERU_TIMEOUT_SECONDS=600
```

```powershell
cd backend
./.venv/Scripts/python.exe -X utf8 -m alembic -x env_file=.env upgrade head
# 后端启动命令见上方“本地启动”。
```

健康状态可访问[本机MinerU健康检查](http://127.0.0.1:8010/v1/health)。模型和运行状态在`deploy/mineru/.runtime/`，虚拟环境与这些文件均不提交Git；不要按阶段日志清理模型。MinerU接口临时输出放在.runtime/api，资源与任务索引不跨服务重启保存；已经写入PostgreSQL的完整正文可继续复用。服务不可用时附件明确失败，不悄悄回退为旧文字层解析成功。

PDF上传接受扫描件，最多500页；解析必须覆盖全部物理页。识别后的正文按批提取，地点容量仍为300项，超过限制要求拆分，不截掉后半份。图片页面的视觉补读仍需要原有百炼视觉配置，总耗时可能高于纯OCR。当前上传格式未扩展到XLSX/PPTX；DOCX内嵌图片尚未做视觉补读。旧消息快照保持原样，旧附件重新选择发送时升级为MinerU识别，后续复用成功结果。

上游资料：[MinerU官方部署与接口文档](https://opendatalab.github.io/MinerU/usage/http_api/)、[许可证](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)。模型解析在本机执行，后续文字理解与图像理解仍使用项目已配置的DeepSeek／百炼服务。

## 固定聊天场景评测

固定题库`backend/evals/chat.v1.json`覆盖模糊需求、资料不足、亲子行程与指定日修改、预算不足、附件冲突及工具模型故障。默认只读取题库；加`--live`使用`backend/.env`连接真实模型、数据库和检索服务，会产生API费用：

```powershell
cd backend
./.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_chat
./.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_chat --live --env-file .env --provider deepseek
# 只复测一组；报告明确记录本次子集。
./.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_chat --live --env-file .env --cases family-and-edit
```

脚本创建并清理独立测试账号和会话，报告写入`temp/reports/`。故障场景只在评测进程注入首选工具模型超时，备用模型仍真实调用。报告包含程序检查、阶段耗时、用量和待人工评阅的回答；来源准确率及顶层cost_cny保持空值，每轮metrics现有各原币已知费用估算与未知项，不等于供应商实际账单。阶段耗时是公开步骤间隔，TestClient首事件时间经过缓冲，不能当作真实网络首包延迟。验收结论归档后按项目约定清理临时报告，保留固定题库。

## 预算规则

固定`demo-cny-v1`演示费率按2–5天、1–8人计算：经济/舒适型住宿每间每晚200/400元，城际交通每人400元，市内交通每人每天30元，餐饮80元，门票活动60元，另计分类小计10%的预留金。金额响应以字符串返回，超支时仍返回计算结果。

## 调用费用与官方票价

执行迁移至0015并重启后端后，管理员侧栏「调用费用」可按最近1/7/30天及请求编号查看文本、工具模型、向量、视觉、网页、地图与本地OCR记录。开始/结束更新同一账目，失败重试分别记录，缓存不重复收费。采用2026-09-21核对的官方原价估算，人民币与美元分开合计；未知用量/费率保持未知，不含免费额度、套餐优惠和本机运行成本，离线旧脚本与历史调用不自动回填。

新行程发布时，通过已配置Tavily检索政府、灵隐寺、故宫官方资料，附上门票公布价、原文、适用期、来源和查询时间。失败仍可发布行程，未查到的票价待查询。公开价不保证指定日期可购买，单项票价不自动替换演示预算。

**交通自动查询**：在对话中说“比较高铁和机票”，系统结合已有城市、日期和人数查询12306公开车次、时刻、二等座成人价与余票，并检索航空公司官方公开页面。往返分别筛选时间，支持“只改返程”；不要求预算或重新规划。铁路最多展示每程三班直达高铁/动车，成人票参考小计不等于优惠票价或锁定多人余票。航司网页不是当日库存报价，未查到时明确说明；酒店仍提供华住会入口，没有代订、支付或供应商合作API。12306公开接口变化或访问受限时可能暂不可用，不绕过验证码。
