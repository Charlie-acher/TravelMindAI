# TravelMindAI 项目进度文档

> 文档版本：v0.5（M1-B 数据库连接基础）  
> 盘点时间：2026-09-08（Asia/Shanghai）  
> 证据范围：`E:\TravelMindAI` 当前文件、静态检查和本次命令输出  
> 关联：[PRD](./01-PRD.md) · [技术设计](./02-TECHNICAL-DESIGN.md) · [模块实施](./03-MODULE-IMPLEMENTATION.md) · [参考文档对照](./05-REFERENCE-ALIGNMENT.md)

## 1. 当前结论

**M0 和 M1-A 已完成；M1-B 第一步已打通 PostgreSQL 连接，数据表、迁移和业务存储尚未实现。完整 M1 尚未完成。**

本次增量（2026-09-08）：用户已创建 PostgreSQL 17 容器 `travelmind-postgres`，本机端口 `127.0.0.1:5432`；Python 通过 psycopg 实际执行只读 SQL，确认数据库和用户均为 `travelmind`。新增 `persistence/database.py`、包声明、数据库测试与 `backend/.env.example`，扩展 Settings；本机 `backend/.env` 已配置且被 Git 忽略，未改根目录 `.env`。

当前共66项测试通过（M1-A原55项、配置新增2项、连接模块新增9项），其中默认自动测试不连接真实数据库；真实数据库通过独立命令行检查验收。新增和改动文件的 Ruff/格式检查通过，mypy 通过11个源码文件，compileall 通过。全量 Ruff 报告 `domain/budget.py:104` 现有注释 E501，未修改该行；保留两项已有第三方弃用警告。锁定 SQLAlchemy 2.0.52、psycopg/psycopg-binary 3.3.5；Alembic 尚未引入。

本次只完成连接基础，不创建业务表，不把连接成功当成保存/恢复验收；预算 API 和 `/health/ready` 的行为保持 M1-A 语义。下面 M1-A 的55项计数为前阶段记录；下一小步实施数据表与迁移。命令和文件阅读顺序见 [README](../README.md)。

开发者已经了解 Python、LangChain、LangGraph，具备 Java/Vue 前后端分离开发经验。接下来直接按工程里程碑交付，不新增基础学习模块。

当前 9 个实验脚本已由用户整理到 `old_learn/`，覆盖模型调用、Prompt、结构化输出、工具调用、Redis 历史、LangGraph 和文本切分；本次不修改或运行它们。此前的 AST 语法检查仅证明语法，不证明远程服务接通。

M1-A 已增加预算领域函数、请求/响应 DTO、FastAPI 路由、应用工厂、错误处理和健康检查，当时55个测试通过，实际 HTTP 与 Swagger 提交验证成功。本次 M1-B 连接基础将测试扩展为66项。手写文件提供详细中文注释，每开发2–3个文件说明作用、顺序和验证结果。具体阅读顺序见 [README](../README.md)。

## 2. 当前工作区快照

### 2.1 文件证据

下表中的实验脚本均位于 `old_learn/`。

| 文件/目录 | 本次静态观察 | 尚未证明 |
|---|---|---|
| `LCEL.py` | Prompt、模型、输出解析器组成调用链 | 产品服务封装与异常处理 |
| `model_io.py` | 同步/异步/流式模型调用练习 | 多模型网关与正式 SSE 服务 |
| `prompt_templates.py` | PromptTemplate / ChatPromptTemplate 实验 | 旅行领域提示与抽取评测 |
| `StructuredOutput_TypedDict.py` | TypedDict/Pydantic 结构化输出示例 | TravelRequest schema 与业务规则 |
| `ToolCalling.py` | bind_tools、天气函数和工具调用链 | 当前真实天气可用、完整错误与日期边界 |
| `AgentSmartSelect.py` | create_agent 与天气工具调用示例 | 旅行编排、可恢复会话及端到端验收 |
| `RedisChatMemory.py` | RedisChatMessageHistory 与 RunnableWithMessageHistory | 当前 Redis 连接成功、持久化策略与图状态恢复 |
| `LangGraphHello.py` | 单节点 StateGraph，节点返回消息更新字典 | checkpointer、条件路由、interrupt、多 Agent 协作 |
| `RecursiveTextSplitter.py` | RecursiveCharacterTextSplitter 切分实验 | PDF/DOCX 解析、embedding、Milvus 索引和 RAG |
| `.env` | 文件存在，本次未读取内容 | 凭据有效或任何提供方可用 |
| `AGENTS.md` | 文件为空；执行遵循会话提供的指令 | 仓库已持久化同一规则 |
| `backend/src/travelmind/` | 配置、预算领域模块、DTO、FastAPI 应用和路由、PostgreSQL连接模块 | 模型规划、业务数据存储 |
| `backend/tests/` | 共66测试：8配置/导入+24领域+25 API+9连接模块 | 模型、业务存储事务、Vue集成 |
| `backend/pyproject.toml`、`uv.lock` | 最小依赖和工具配置，独立环境重装通过 | 全部后续依赖兼容 |
| `README.md` | 安装、预算服务启动、Swagger提交、计价规则与逐批文件说明 | 完整旅行产品可用 |
| `docs/` | 需求设计 v0.2；进度更新为 M0 验收 | M1–M7 已实现 |

### 2.2 工具环境快照

下表中 LangChain/LangGraph/FastAPI 等为本轮开始时的全局包快照，不是正式后端的运行依赖。M0 独立环境的精确依赖以 `backend/uv.lock` 为准。

| 项目 | 本次观察 |
|---|---|
| Python | `E:\Python 3.11.2\python.exe`，3.11.2 |
| LangChain | 1.3.14 |
| LangGraph | 1.2.11 |
| langchain-core | 1.5.3 |
| FastAPI | 0.136.3；安装了包但项目没有应用入口 |
| Pydantic | 2.13.4 |
| langchain-redis | 0.2.5 |
| uv | 已通过 pip 用户级安装 0.12.10，使用 `python -m uv` |
| Git | 已初始化本地 `m0-engineering` 分支，无提交、无远程地址 |

M0 项目环境：`backend/.venv/Scripts/python.exe`，Python 3.11.2；pydantic-settings 2.15.0、Pydantic 2.13.5、pytest 9.1.1、ruff 0.16.6、mypy 1.20.2。未把全局 LangChain、LangGraph 或 FastAPI 安装到 M0 环境。

M1-A 在项目环境增量安装 FastAPI 0.141.1、Uvicorn 0.52.4、httpx 0.28.1，及 Starlette 1.6.0 等依赖；`uv.lock` 记录30个包（含本地项目）。未升级原有全局学习环境中的包。

### 2.3 当前缺失的产品产物

- `frontend/` 工程及真实用户页面；
- 完整旅行需求 DTO（当前仅有预算 DTO）；
- 持久化会话、业务表、迁移、行程版本与导出；
- 带状态恢复、条件边、返工和人工确认的旅行工作流；
- POI 数据集、PDF/DOCX 解析、向量化、Milvus 和可追溯 RAG；
- 四提供方统一适配器、路由、熔断与降级；
- Vue 页面、自动化测试、评测报告和 Compose。

根目录的 `__pycache__`、`.pytest_cache` 等缓存不能作为当前产品测试通过的证据。

## 3. 里程碑状态

| 里程碑 | 状态 | 当前证据 | 下一验收 |
|---|---|---|---|
| 文档基线 | 已更新 v0.2 | 五份文档，含飞书对照及工程决策 | 后续随实现维护 |
| M0 工程基线 | **已完成** | 独立重装、6 测试、ruff/mypy/compileall、Git 忽略和新增文件凭据模式检查通过 | 进入 M1-A |
| M1 FastAPI + 预算 | **进行中：M1-A及数据库连接基础已完成** | 预算/健康API、OpenAPI、66测试、真实HTTP/Swagger和PostgreSQL只读连接 | M1-B 数据表、迁移与存储 |
| M2 单模型需求抽取 | 局部实验 | Prompt/结构化输出示例 | 旅行 schema、集中追问、数据集与真实 smoke |
| M3 RAG | 局部实验 | 文本切分脚本 | 文档生命周期、POI、Milvus、引用与检索评测 |
| M4 LangGraph / 记忆 | 局部实验 | 单节点图、create_agent、Redis 历史示例 | 旅行图、返工、interrupt、checkpoint 恢复 |
| M5 四模型网关 | 未开始 | 单模型调用实验不能计为网关 | 适配器契约、真实 smoke、故障降级 |
| M6 Vue 全栈 | 未开始 | 没有前端工程 | Arco 页面、真实 API、SSE、E2E |
| M7 质量与部署 | 未开始 | 无 Compose/评测报告 | 六个 PRD 场景、故障演练、新环境启动 |

完整里程碑为 **1/8 完成**，另有 M1-A 子阶段和 M1-B 连接基础完成；M1-B 数据表、迁移与存储尚未实现，不把整个 M1 记为完成。

## 4. 本次变更与验证

### 2026-09-08：M1-A 预算计算与 API

开发顺序：领域测试→领域计算→API依赖与测试→DTO→路由→应用装配→实际HTTP与Swagger→文档。每批2–3个文件说明用途；源码包含中文注释与Java概念对应。

新增：`domain/__init__.py`、`domain/budget.py`、`schemas.py`、`api/__init__.py`、`api/v1/__init__.py`、`api/v1/budget.py`、`main.py`、`tests/test_budget.py`、`tests/test_budget_api.py`。更新依赖与锁文件、导入安全测试、README、实施和进度文档。

预算输入2–5天、1–8人、正数人民币预算（最多两位小数）和economy/comfort住宿档位。默认每间2人、向上取整房间数、晚数=天数−1。日期可不填，填写则要求首尾日包含在days中。所有价格是 `demo-cny-v1` 固定演示假设；2人3天经济档分类小计2220、预留金222、合计2442元，预算5000时余额2558元。

已执行并通过：

- 测试先行：领域测试先因缺少领域包失败，API测试先因缺少main入口失败；实现后55测试通过；
- `python -m uv run --locked ruff check .`：无错误；
- `python -m uv run --locked ruff format --check .`：13个Python文件格式正确；
- `python -m uv run --locked mypy src`：9个源文件无错误；
- `python -m uv run --locked python -m compileall -q src`：通过；
- `python -m uv run --locked python -X utf8 -m pytest -q`：55 passed，2条依赖弃用提示；
- 实际启动 `uvicorn travelmind.main:create_app --factory --host 127.0.0.1 --port 8000`；
- HTTP检查：live/ready/docs/openapi均200；预算返回2442.00/2558.00，请求头与体编号一致；days=1返回422；
- 浏览器Swagger实际执行默认JSON，Server response显示200与正确预算；
- `old_learn/` 文件按本轮开始时的SHA-256快照检查，无内容变化。

两条提示来自 Starlette 对 httpx 的弃用，以及 AnyIO BlockingPortal 旧别名；当前测试可用，没有过滤警告或以此推断未来版本兼容。后续升级测试依赖时单独处理。

边界：只实现预算与健康接口，不调用模型/天气，不保存会话或预算结果，不引入数据库。校验/领域/HTTP错误已统一；未捕获的意外程序错误仍使用框架默认500行为。Swagger使用默认CDN资源，离线环境的页面可用性尚未验收。当前分支仍无提交、无远程地址。

### 2026-09-08：M0 编码与验收

开发顺序：配置与依赖（3 文件）→ 测试（2 文件）→ 正式包与配置实现（2 文件）→ README/实施/进度（3 文件）。`uv.lock` 为工具自动生成，不手工修改。

新增工程文件：`.gitignore`、`.env.example`、`backend/pyproject.toml`、`backend/uv.lock`、`backend/src/travelmind/__init__.py`、`backend/src/travelmind/settings.py`、`backend/tests/test_settings.py`、`backend/tests/test_import_safety.py`、`README.md`。

测试先行记录：实现前配置测试因缺少 travelmind 包收集失败，独立进程导入测试失败；创建包和配置实现后，6 个测试全部通过。

在 `E:\TravelMindAI\backend` 执行：

| 命令/检查 | 结果 |
|---|---|
| `python -m uv sync --locked --link-mode copy` | 按锁文件安装成功 |
| `python -m uv run --locked python -X utf8 -m pytest` | 6 passed |
| `python -m uv run --locked ruff check .` | 无错误 |
| `python -m uv run --locked ruff format --check .` | 4 个 Python 文件格式符合要求 |
| `python -m uv run --locked mypy src` | 2 个源文件无类型错误 |
| `python -m uv run --locked python -m compileall -q src` | 语法编译通过 |
| 独立临时目录安装 + pytest + load_settings | 重新创建环境，6 passed，默认配置正确 |
| Git 忽略检查 | `.env`、.venv、上传目录被忽略，`.env.example` 可跟踪 |
| 新增手写工程文件常见凭据模式扫描 | 未发现匹配；非完整安全审计 |
| 原有 9 个脚本 SHA-256 比较 | 内容均未变化 |

独立重装目录为 `C:\Users\admin\AppData\Local\Temp\travelmind-m0-evpm67ah\backend`。只复制源码、测试、pyproject 和锁文件，未复制 `.env` 与虚拟环境；使用同一机器的基础 Python 和依赖缓存，不能等同于另一台机器验收。

修正过的验证问题：起初从根目录运行检查扫到了旧实验脚本，已改为 backend 工作目录；mypy 未识别 BaseSettings 的 `_env_file`，根据已安装插件实现和官方文档启用 `pydantic.mypy` 后通过。没有以关闭类型检查或修改旧实验代码来规避错误。

无模型、天气、Redis、数据库请求。当前没有提交任何文件；首次提交前仍需单独审查旧实验脚本与用户材料，不能把新增文件的凭据模式检查当作整个目录的安全保证。

### 2026-09-08：对照飞书更新项目文档

以下为编码开始前的文档阶段记录；其中环境缺失描述仅对应当时快照。

变更文件：

- `docs/01-PRD.md`：补修订依据、景点筛选、本地推荐、路线调整及第六个验收场景；
- `docs/02-TECHNICAL-DESIGN.md`：补选型取舍、预算端点、Embedding 独立配置、工具有效性、生命周期、POI 与行程数据边界；
- `docs/03-MODULE-IMPLEMENTATION.md`：直接按产品工程推进，细化 M0、M1-A/M1-B，补中断索引处理与路线测试；
- `docs/04-PROGRESS.md`：以当前 E 盘、9 个脚本和真实命令结果替换过时快照；
- `docs/05-REFERENCE-ALIGNMENT.md`：新增章节覆盖、技术清单差异和样例代码缺口。

本次通过 `ast.parse` 逐个解析根目录 `.py` 源码，输出 9 个 `SYNTAX_OK`，退出码 0。仅解析源码，没有导入执行，没有网络调用；不等同于运行测试通过。可复核命令：

```powershell
Set-Location E:\TravelMindAI
python -c "import ast,pathlib; files=sorted(pathlib.Path('.').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p)) for p in files]; print('SYNTAX_OK',len(files))"
```

同时核对了 `git status --short`、文件清单、`Get-Command python,uv,git` 与包元数据：目录不是 Git 仓库；uv 不在 PATH；解释器和版本见 §2.2。文档编辑完成后检查五份文件的本地链接、代码围栏及 Python 文件 SHA-256，确认无断链、无未闭合代码围栏，9 个脚本内容未变化。

文档阶段未运行 pytest、ruff、mypy；后续 M0 编码已运行并验证，结果见本节前部。后端 Web 启动、前端构建、数据库/向量库和真实模型/天气 smoke 仍未运行。

## 5. 历史记录与当前边界

2026-08-17 的 v0.1 文档记录了 `D:\TravelMindAI` 当时只有 4 个脚本，并完成四份规划文档；该描述是历史快照，不能继续当作当前目录的状态。

原进度文档还保留过早期预算模块/13 个测试及一次模型调用的历史记录。当前目录没有对应预算模块和测试，因此本次不复用历史“通过”状态；M1 需要在当前正式工程重新实现或恢复并验证。

飞书的示例源码未在本地运行，静态查出的风险不扩展为整个参考项目的运行结论。三个嵌入图块的细节尚未核实，不影响基于其文字与代码对照本次需求。

## 6. 后续开发入口

M1-B 的连接基础已完成，下一次编码实现数据表与 Alembic 迁移，然后完成会话/旅行需求/行程草稿存储。继续按2–3个文件一批讲解，先测试、后实现，并更新证据。运行命令与计价规则见 [README](../README.md)。

当前正式后端入口是 `backend/src/travelmind/main.py:create_app`，在PowerShell运行：

```powershell
Set-Location E:\TravelMindAI\backend
python -m uv sync --locked
python -m uv run --locked uvicorn travelmind.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

打开 [Swagger](http://127.0.0.1:8000/docs)，选择预算POST接口并执行。此入口已验证可用；不要把飞书的 `app.main:app` 当成本地入口。若端口已有本阶段服务在运行，不必重复启动。

实施时需核实：

- M1：预算计价单位、测试夹具和依赖增量；保持 `.env` 忽略规则；
- M2：选择已有配置的单一提供方，核实模型标识及结构化输出能力；
- M3：验证 Windows 的 Milvus 部署环境、Embedding 供应方和语料来源；
- M5：逐个核验提供方，不把未配置适配器记为已接通；
- P1：天气/地图数据源及账号配额，接入前保持“待核实”；
- 对外开放前：认证与数据隔离；本地单租户不等于具备公网服务条件。

## 7. 后续记录模板

```text
日期：
模块/验收项：
变更文件：
执行命令：
结果：通过 / 失败 / 未运行
验证层级：静态 / 单元 / 集成 / 真实 API / E2E
外部依赖：fake / 本地服务 / 真实 provider
已证明：
未证明：
遗留问题：
下一步：
```
