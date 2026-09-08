# TravelMindAI

当前已完成 **M0 工程基线 + M1-A 预算 API**，可以通过网页文档或 HTTP 请求计算预算。
M1-B 第一步已打通 Python → PostgreSQL 连接；尚未创建业务表或实现行程保存。
模型调用和自动规划功能按后续阶段实现。

### M1-B 第一步：检查 PostgreSQL 连接

本机容器为 `travelmind-postgres`，数据库和用户名均为 `travelmind`，地址为 `127.0.0.1:5432`。
先启动 Docker Desktop；已有容器用 `docker start travelmind-postgres`，不要重复执行创建容器的 `docker run`。

```powershell
Set-Location E:\TravelMindAI\backend
# 你已在 PyCharm 中使用 backend/.venv，直接指定它的 Python 最明确。
# --env-file 显式读取 backend/.env，不读取根目录中旧实验使用的 .env。
.\.venv\Scripts\python.exe -X utf8 -m travelmind.persistence.database --env-file .env
```

预期输出：`连接成功：database=travelmind, user=travelmind`。检查命令执行完就退出，数据库容器继续运行。
该命令只执行 `SELECT current_database(), current_user`，不创建、修改或删除业务数据。

本机已配置 `backend/.env`，无需覆盖。换一台电脑时，把 [后端配置样本](./backend/.env.example) 复制为同目录的 `.env`，填写创建容器时的密码。真实 `.env` 被 Git 忽略；URL 含特殊字符的密码需要 URL 编码。只填写文件不会自动给预算 API 加上数据库功能。

阅读顺序：

1. [settings.py](./backend/src/travelmind/settings.py)：`database_url` 保存连接地址，`SecretStr` 避免打印配置时直接显示密码。
2. [database.py](./backend/src/travelmind/persistence/database.py)：`create_database_engine` 创建连接池管理器，`check_connection` 借用连接执行只读 SQL，`main` 提供命令行入口。
3. [test_database.py](./backend/tests/test_database.py)：验证缺配置、地址错误、密码脱敏、延迟连接和命令行失败处理。

对应 Java：配置对象类似 `ConfigurationProperties`，Engine 类似 `DataSource`，连接上的 `execute` 类似执行 JDBC 查询。

本小步验收：66 项自动测试通过，另有真实 PostgreSQL 只读连接成功；新增和改动的 Python 文件通过 Ruff 检查与格式检查，11 个源码文件通过 mypy。全量 Ruff 发现 `domain/budget.py` 现有注释的 E501 超长问题，本次未改该文件；测试仍有两项已有的第三方弃用警告。

下一小步为 SQLAlchemy 数据表定义与 Alembic 迁移，随后实现会话、旅行需求和行程草稿存储。完整 M1-B 尚未完成，当前 `/health/ready` 仍是预算 API 的就绪检查，不代表数据库就绪。

## 0. 当前阶段：启动预算 API

```powershell
Set-Location E:\TravelMindAI\backend
python -m uv sync --locked --link-mode copy
# --factory 表示调用 create_app() 创建应用；--reload 在改代码后自动重启。
# 127.0.0.1 只允许本机访问，8000 是本地端口。
python -m uv run --locked uvicorn travelmind.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

终端保持运行，打开 [Swagger 接口文档](http://127.0.0.1:8000/docs)：展开绿色的 `POST /api/v1/budget/estimate` → `Try it out` → 保留示例 → `Execute`。
看 **Server response** 中实际返回的结果；下方 **Example Value** 只是文档结构示例，不是本次计算。停止自己启动的服务时，在该终端按 Ctrl+C。

请求示例（金额单位为人民币元）：

```json
{"days": 3, "travelers": 2, "total_budget": "5000.00", "lodging": "economy"}
```

响应的 `budget.total` 应为 `"2442.00"`，`budget.remaining` 为 `"2558.00"`，`over_budget` 为 false。
`request_id` 是本次请求的编号，也会出现在响应头 `X-Request-ID`；每次请求不同，但相同条件的 budget 内容相同。

也可以另外打开一个 PowerShell 发送请求：

```powershell
# ConvertTo-Json 把 PowerShell 对象转换为 JSON；Invoke-RestMethod 发出 HTTP 请求。
$budgetBody = @{ days = 3; travelers = 2; total_budget = '5000.00'; lodging = 'economy' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/budget/estimate' -ContentType 'application/json' -Body $budgetBody
```

M1-A 文件阅读顺序：

| 顺序 | 文件 | 对应职责 |
|---|---|---|
| 1 | [test_budget.py](./backend/tests/test_budget.py) | 先看手算例子与边界，理解正确结果是什么 |
| 2 | [domain/budget.py](./backend/src/travelmind/domain/budget.py) | 核心计算，类似独立的 Java Service |
| 3 | [schemas.py](./backend/src/travelmind/schemas.py) | 请求/响应 DTO，校验 JSON 与日期关系 |
| 4 | [api/v1/budget.py](./backend/src/travelmind/api/v1/budget.py) | Controller：接收输入、调用计算、返回结果 |
| 5 | [main.py](./backend/src/travelmind/main.py) | 应用装配：挂路由、请求编号、错误处理、健康检查 |
| 6 | [test_budget_api.py](./backend/tests/test_budget_api.py) | 类似 MockMvc，验证完整 HTTP 契约 |

调用链为：浏览器/前端 → FastAPI 中间件 → Pydantic DTO → 路由函数 → 领域计算 → 响应 DTO → JSON。
`domain`、`api`、`api/v1` 中的 `__init__.py` 用于声明普通包；已有导入测试覆盖新增模块，确保导入不会启动服务或调用模型。

### 固定价格 demo-cny-v1

以下数字只是用于验证工程的**演示单价**，不是市场报价，不按城市或日期变化：

| 分类 | 单价及单位 | 两人三天经济档 |
|---|---|---:|
| 住宿 | 经济200 / 舒适400元，每间每晚 | 200 × 1间 × 2晚 = 400 |
| 城际交通 | 400元，每人全程 | 800 |
| 市内交通 | 30元，每人每天 | 180 |
| 餐饮 | 80元，每人每天 | 480 |
| 门票/活动 | 60元，每人每天 | 360 |
| 预留金 | 上述分类小计 × 10% | 222 |
| 合计 | 2220 + 222 | 2442 |

每间最多2人，奇数人数向上取整；晚数=天数−1。固定价格在领域函数中定义，测试预期值由手算得出。
仅支持2–5天、1–8人；预算大于0、最多两位小数、不超过9999999999.99元。
开始/结束日期可同时省略；填写时要求成对、顺序正确，包含首尾日的天数与 days 一致。
超支仍返回200并给出负余额；参数错误为422，领域拒绝为400，未知路径为404。
校验/领域/HTTP错误统一返回 `error` 和 `request_id`，不回显原始请求或内部堆栈。
当前没有单独的未捕获异常 JSON 处理器；意外程序错误仍按框架的500行为处理。

## 1. M0 基础文件回顾

本次按下面的顺序开发，手写的配置和 Python 文件均带中文说明：

| 批次 | 文件 | 用途 |
|---|---|---|
| 1 | [.gitignore](./.gitignore)、[.env.example](./.env.example)、[pyproject.toml](./backend/pyproject.toml) | 保护本地配置，声明变量、依赖和检查规则 |
| 2 | [test_settings.py](./backend/tests/test_settings.py)、[test_import_safety.py](./backend/tests/test_import_safety.py) | 先描述正确行为；类似先写 JUnit 测试 |
| 3 | [__init__.py](./backend/src/travelmind/__init__.py)、[settings.py](./backend/src/travelmind/settings.py) | 创建正式包，实现配置读取，让测试通过 |
| 4 | 本说明、[模块实施](./docs/03-MODULE-IMPLEMENTATION.md)、[进度记录](./docs/04-PROGRESS.md) | 给出可复现命令和实际验收结果 |

如果现在只想读懂一段 Python，先读 `settings.py`，再对照 `test_settings.py`。
不用先逐行看锁文件。`backend/uv.lock` 由 uv 自动生成，记录精确依赖版本和哈希；提交到 Git，但不手工编辑或逐行加注释。

## 2. 目录与开发工具

```text
TravelMindAI/
├── backend/
│   ├── pyproject.toml             # 项目声明与工具规则，作用接近 pom.xml
│   ├── uv.lock                    # 自动生成的依赖锁定结果
│   ├── .venv/                    # 自动安装的本项目 Python 和依赖，不提交
│   ├── src/travelmind/
│   │   ├── __init__.py           # 包的说明，导入不会启动服务
│   │   └── settings.py           # 配置对象和显式加载函数
│   └── tests/                   # pytest 发现并执行的测试
├── docs/                        # 产品要求、技术设计和实施进度
├── .env.example                 # 可共享的配置模板
├── .env                         # 你已有的个人配置，Git 忽略
└── README.md
```

9 个实验脚本现在位于 `old_learn/`（用户调整的目录）；正式工程不导入它们。不要运行实验脚本来代替后端验收，其中部分代码会调用外部服务。

`uv` 管理依赖和虚拟环境，`pytest` 检查实际行为，`ruff` 检查风格和常见代码错误，`mypy` 检查类型提示。工具配置集中在 `pyproject.toml`。

## 3. 安装并复现环境

当前验证平台：Windows PowerShell、Python 3.11.2、uv 0.12.10。
使用未激活其他虚拟环境的 PowerShell；下面的 `python` 应当是装有 uv 的基础解释器。

```powershell
# 第一次在新机器上使用时，先准备 Python 3.11，再安装 uv 工具。
python --version
python -m pip install --user uv==0.12.10
python -m uv --version

# 必须进入 backend，后续的 src、tests、点号 . 都是相对于此目录。
Set-Location E:\TravelMindAI\backend

# 按已提交的锁文件安装到 backend/.venv，不安装到全局环境。
# --locked：发现依赖声明与锁文件不一致时直接报错，不偷偷改版本。
# --link-mode copy：C 盘缓存复制到 E 盘，避免跨盘硬链接警告。
python -m uv sync --locked --python 3.11 --link-mode copy
```

本机 uv 已安装；重复开发通常只需进入 `backend` 后执行 sync。无需执行 Activate.ps1，uv run 会自动选择项目虚拟环境。
若终端已激活其他虚拟环境导致找不到 uv，先退出该环境或使用基础解释器绝对路径执行 `-m uv`。

不要把全局 Python 中安装过的 LangChain 等包当成本项目依赖。M1-A 只增加了 Pydantic、FastAPI、Uvicorn 和测试用 httpx；精确版本见锁文件，后续按需添加其他库。

## 4. 现在能运行什么

除了第0节的预算服务，还可以单独运行配置读取，确认包能使用：

```powershell
Set-Location E:\TravelMindAI\backend
python -m uv run --locked python -X utf8 -c "from travelmind.settings import load_settings; print(load_settings().model_dump())"
```

没有设置项目环境变量时，预期输出：

```text
{'app_name': 'TravelMindAI', 'environment': 'development'}
```

命令含义：`uv run` 选择项目环境；`python -c` 执行引号内代码；`import` 导入函数；`load_settings()` 创建配置；`model_dump()` 把对象转换为字典；`print()` 打印结果。`-X utf8` 让中文输出使用 UTF-8。

需要读取文件时必须指定路径。下面只读取公开的示例，不读取你的真实密钥：

```powershell
python -m uv run --locked python -X utf8 -c "from pathlib import Path; from travelmind.settings import load_settings; print(load_settings(Path('../.env.example')).model_dump())"
```

已有 `.env` 不要用模板覆盖。M0 无需 API Key，也无需修改该文件。
配置优先级是环境变量 > 显式指定文件 > 默认值。`import travelmind.settings` 只定义类与函数；调用 `load_settings()` 才创建对象。

## 5. 检查是否写对

```powershell
Set-Location E:\TravelMindAI\backend
python -m uv run --locked python -X utf8 -m pytest
python -m uv run --locked ruff check .
python -m uv run --locked ruff format --check .
python -m uv run --locked mypy src
python -m uv run --locked python -m compileall -q src
```

每条命令分别检查结果和退出码，上一条失败时不要因为最后一条成功就当作全部通过。
当前结果：55 个测试通过、ruff 无错误、13 个 Python 文件格式正确、mypy 检查 9 个源文件无错误、compileall 无错误。测试依赖有两条弃用提示（Starlette/httpx 与 AnyIO），不影响本次结果；未用过滤警告隐藏它们。

测试使用临时文件，不依赖个人 `.env`，也不发送模型请求。配置测试验证默认值、中文文件、环境变量覆盖和非法输入；导入测试在独立进程中阻止 socket 联网和交互输入。

在 IDE 中，将后端解释器选为 `E:\TravelMindAI\backend\.venv\Scripts\python.exe`，测试工作目录设为 `E:\TravelMindAI\backend`。从根目录执行 `ruff check .` 会检查旧实验文件；本阶段的检查范围是 backend。

## 6. Git 与变更范围

本地已初始化 Git，当前分支为 `m0-engineering`，没有提交或远程地址。`.env`、虚拟环境和运行数据被忽略，`.env.example` 与 `uv.lock` 应进入版本管理。
首次提交前分别检查新工程和旧实验文件是否含有硬编码凭据；不要直接把全部根目录文件加入提交。

## 7. 下一步

M1-A 已完成，下一小阶段 M1-B 增加数据库、迁移和会话/行程草稿存储。继续按 2–3 个文件一批说明用途、开发顺序与验证结果，保持详细中文注释。

完整需求见 [PRD](./docs/01-PRD.md)，后续任务见 [模块实施](./docs/03-MODULE-IMPLEMENTATION.md)，当前证据见 [项目进度](./docs/04-PROGRESS.md)。

实现时核对的官方资料：[uv 安装](https://docs.astral.sh/uv/getting-started/installation/)、[uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)、[Pydantic mypy 插件](https://docs.pydantic.dev/latest/integrations/mypy/)。
