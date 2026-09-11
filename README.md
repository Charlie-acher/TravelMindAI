# TravelMindAI

TravelMindAI 提供 Vue 旅行需求对话页和 Python 后端，支持自然语言需求抽取、多轮修改、结构化预算估算和草稿存储。通过 FastAPI 暴露 HTTP 接口，将参数校验、需求合并、预算规则与数据访问分层组织，便于阅读、测试和集成。

## 主要功能

- **旅行需求对话**：Vue与Arco组件构建聊天页，调用DeepSeek提取需求、集中追问缺项，支持多轮修改、指定限制删除和变化高亮。当前对话保存在页面内存，刷新后重新开始。

- **旅行预算估算**：根据行程天数、同行人数、总预算和住宿档位计算全团费用，支持 2–5 天、1–8 人的行程。
- **费用明细与预算判断**：返回住宿、城际交通、市内交通、餐饮、门票及活动费用，并计算预留金、预计总额、余额和是否超支。
- **输入校验**：校验金额精度、人数、天数、住宿类型与日期一致性，拒绝未知字段和非法输入。
- **接口文档与请求追踪**：提供 Swagger UI、OpenAPI 描述、健康检查，以及响应体和响应头中的请求编号。
- **PostgreSQL 连接工具**：提供独立的只读连接检查命令，支持延迟连接、连接超时和连接信息脱敏。
- **业务表与迁移**：SQLAlchemy 定义会话、旅行需求和行程版本表，Alembic 显式管理表结构版本。
- **草稿存储**：Python 和 HTTP 接口均支持创建/读取会话、原子保存需求和行程快照、读取最新或历史版本；费用由后端计算。

预算使用 `demo-cny-v1` 固定演示单价，金额单位为人民币，不代表实时酒店、交通或门票报价。预算计算无需模型 API Key，也不依赖数据库连接。

## 技术实现

| 层次 | 实现 | 职责 |
| --- | --- | --- |
| HTTP 接口 | FastAPI、Uvicorn | 应用工厂、路由、健康检查与请求编号中间件 |
| 对话前端 | Vue 3、TypeScript、Vite、Arco Design Vue | 连续对话、需求卡片、错误提示与重新开始 |
| 需求理解 | LangChain ChatOpenAI、DeepSeek | 单模型抽取，Python合并与校验，最多一次输出修复 |
| 数据契约 | Pydantic | 请求校验、日期关系校验与结构化响应 |
| 预算规则 | Python、Decimal | 独立计算函数、分类计费、精确金额运算 |
| 环境配置 | Pydantic Settings | 环境变量与显式配置文件加载、敏感字段脱敏 |
| 数据库与迁移 | SQLAlchemy、psycopg、Alembic | PostgreSQL 连接、ORM 表定义及版本化建表 |
| 开发工具 | uv、pytest、Ruff、mypy | 依赖锁定、自动测试、风格与类型检查 |

请求经过参数校验后，由路由调用领域计算函数，再转换为响应对象。领域模块不依赖 HTTP 框架或外部服务，可以直接在 Python 中调用。

金额以 `Decimal` 计算，预留金按四舍五入保留两位小数；响应中的金额序列化为字符串。参数校验、预算规则和 HTTP 异常使用统一的 `error` 与 `request_id` 结构。请求编号也通过 `X-Request-ID` 响应头返回。

### 预算规则

| 项目 | 计算依据 |
| --- | --- |
| 住宿 | 经济型 200 元 / 间 / 晚，舒适型 400 元 / 间 / 晚 |
| 房间数与晚数 | 每间最多 2 人，房间数向上取整；晚数为天数减 1 |
| 城际交通 | 400 元 / 人 / 全程 |
| 市内交通 | 30 元 / 人 / 天 |
| 餐饮 | 80 元 / 人 / 天 |
| 门票与活动 | 60 元 / 人 / 天 |
| 预留金 | 分类费用小计的 10% |

总预算必须大于 0、不超过 9999999999.99 元，最多保留两位小数。开始日期和结束日期可同时省略；填写时，包含首尾日的天数必须与 `days` 一致。超支时仍返回计算结果，`remaining` 为负数，`over_budget` 为 `true`。

## 快速开始

需要 Python 3.11 和 Git。以下命令适用于 PowerShell：

```powershell
git clone https://github.com/Charlie-acher/TravelMindAI.git
cd TravelMindAI/backend
python -m pip install uv==0.12.10
python -m uv sync --locked --python 3.11 --link-mode copy
python -m uv run --locked uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

启动后访问：

- [Swagger UI](http://127.0.0.1:8000/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)

### 启动旅行需求对话页

后端的`.env`填写`TRAVELMIND_DEEPSEEK_API_KEY`，并可设置`TRAVELMIND_DEEPSEEK_MODEL=deepseek-v4-pro`；不要将密钥放到前端。也兼容显式配置文件中的`DS_API_KEY`。

在`backend`目录启动后端：

```powershell
./.venv/Scripts/python.exe -X utf8 -m uvicorn app.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8000
```

另开终端，在`frontend`目录运行（Node.js需要兼容20.19+或22.12+）：

```powershell
npm ci
npm run dev
```

打开 [旅行需求对话](http://127.0.0.1:5173/)。依次输入“想去杭州”“三天两个人预算五千”“改成三个人”，右侧展示更新后的需求。当前仅整理需求，不生成逐日行程；每次发送会调用模型。

页面通过`POST /api/v1/requirements/messages`传递原话和上一轮需求，`GET /api/v1/requirements/status`只检查模型配置，不发送模型请求。生产打包用`npm run build`；正式部署需为`/api`配置同源反向代理。

### 启用会话和草稿接口

上面的默认启动命令只读取环境变量。如果数据库地址在 `backend/.env` 中，先确保 PostgreSQL 容器运行并已执行迁移，再从 `backend` 目录启动：

```powershell
# 先停止占用8000端口的旧开发服务；不要同时启动两份使用同一端口的服务。
.\.venv\Scripts\python.exe -X utf8 -m uvicorn app.main:create_app --factory --env-file .env --reload --host 127.0.0.1 --port 8000
```

`--env-file .env` 由 Uvicorn 显式载入配置；应用仍不会在 import 时自动读取文件。连接池按应用生命周期创建/关闭，同步存储操作在线程池执行，参考 [FastAPI lifespan 官方说明](https://fastapi.tiangolo.com/advanced/events/)。

在 Swagger 的“会话与草稿”分组按以下顺序操作：

1. `POST /api/v1/sessions` → Try it out → 填写 `{"title":"杭州三日游"}` → Execute，复制响应中的 `session.id`。
2. `POST /api/v1/sessions/{session_id}/drafts` → 粘贴会话ID，保留示例预算条件，执行保存。响应状态为201，金额在 `draft.itinerary.itinerary_json.budget` 中。
3. `GET /api/v1/sessions/{session_id}/drafts` → 粘贴会话ID，version留空读取最新版；填1可读第一版。
4. `GET /api/v1/sessions/{session_id}` → 查看会话标题、状态和修改时间。

保存请求示例：

```json
{
  "title": "杭州三日预算草稿",
  "notes": "先确认预算，再安排景点",
  "requirements": {"days": 3, "travelers": 2, "total_budget": "5000.00", "lodging": "economy"}
}
```

该输入保存的总费用为 `2442.00`，余额为 `2558.00`；不接受客户端指定总费用、版本或确认状态。每次成功POST会保存新版本，暂不提供幂等键。草稿的每日活动仍为空，不代表AI生成了完整行程。

未配置数据库时存储接口返回503；不存在的资源返回404；非法输入返回422；数据库约束冲突返回409；连接故障返回503；其他数据库异常返回脱敏500。响应保持 `error/request_id` 格式，不暴露SQL或连接密码。存储接口按单用户使用方式设计，不提供归属鉴权。

接口阅读顺序：[schemas/trip.py](backend/app/schemas/trip.py) 定义请求/响应 → [api/trips.py](backend/app/api/trips.py) 调用计算与存储 → [main.py](backend/app/main.py) 装配生命周期与错误处理 → [test_trips_api.py](backend/tests/test_trips_api.py) 验证完整链路。

### 调用预算接口

`POST /api/v1/budget/estimate`

```json
{
  "days": 3,
  "travelers": 2,
  "total_budget": "5000.00",
  "lodging": "economy"
}
```

在另一个 PowerShell 窗口发送请求：

```powershell
$body = @{ days = 3; travelers = 2; total_budget = '5000.00'; lodging = 'economy' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/budget/estimate' -ContentType 'application/json' -Body $body
```

该示例的主要返回字段如下；完整响应还包含单价、分类费用、计算假设和请求编号。

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `budget.subtotal` | `"2220.00"` | 分类费用小计 |
| `budget.contingency` | `"222.00"` | 预留金 |
| `budget.total` | `"2442.00"` | 预计总费用 |
| `budget.remaining` | `"2558.00"` | 预算余额 |
| `budget.over_budget` | `false` | 是否超支 |

### 健康检查

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/health/live` | 检查应用能否处理请求 |
| `GET /api/v1/health/ready` | 未配置数据库时返回 `postgresql: disabled`；配置后实际检查三张业务表和字段，成功返回ready，失败返回503 |

### 配置与 PostgreSQL

应用配置使用 `TRAVELMIND_` 前缀，支持 `TRAVELMIND_APP_NAME`、`TRAVELMIND_ENVIRONMENT` 和可选的 `TRAVELMIND_DATABASE_URL`。配置优先级为环境变量、显式指定的配置文件、默认值；模块导入不会自动读取 `.env` 或发起网络连接。

使用数据库工具时，准备可访问的 PostgreSQL 实例。在 `backend` 目录将 `.env.example` 复制为 `.env`，填写连接地址，再运行：

```powershell
python -m uv run --locked python -X utf8 -m scripts.check_db --env-file .env
```

连接地址格式见 [数据库配置示例](backend/.env.example)。该命令只查询当前数据库名和用户名；连接池按需建立连接，命令结束后释放资源。真实 `.env` 由 Git 忽略，数据库配置不参与预算接口的计算。

### 创建或检查业务表

首次迁移为 `0001_business_tables`。Docker Desktop 需要运行，已有容器可用 `docker start travelmind-postgres` 启动。在 `backend` 目录执行：

```powershell
# upgrade head 表示升级到最新表结构；已经是最新版时不会重复建表。
.\.venv\Scripts\python.exe -X utf8 -m alembic -x env_file=.env upgrade head
# 查看数据库记录的迁移版本。
.\.venv\Scripts\python.exe -X utf8 -m alembic -x env_file=.env current
# 检查 ORM 声明与数据库实际结构是否一致。
.\.venv\Scripts\python.exe -X utf8 -m alembic -x env_file=.env check
```

配置放在 `pyproject.toml` 的 `[tool.alembic]` 中，保留 UTF-8 中文注释；不使用会受 Windows 本地编码影响的 INI 文件。数据库地址仍由 `-x env_file=.env` 显式读取。此用法参考 [Alembic 官方 TOML 配置说明](https://alembic.sqlalchemy.org/en/latest/tutorial.html#using-pyproject-toml-for-configuration)。

| 表 | 用途 |
| --- | --- |
| `sessions` | 旅行规划会话、标题、状态和独立 thread_id |
| `travel_requests` | 每次旅行条件的版本快照，同一会话的版本号不重复 |
| `itineraries` | 行程版本快照，关联同一会话下的需求版本 |
| `alembic_version` | Alembic 使用的表结构版本记录，不是业务数据 |

表定义的阅读顺序：先看 [models/trip.py](backend/app/models/trip.py) 的字段，再看 [首次迁移](backend/migrations/versions/0001_business_tables.py) 的 `upgrade`，最后看 [迁移入口](backend/migrations/env.py) 如何读取配置并执行。`models/trip.py` 类似 JPA Entity；迁移类似版本化 SQL 脚本。仅修改 Python 类不会自动改变数据库。

同一会话可以有多个需求和行程版本；复合外键保证草稿不能引用别的会话的需求。JSONB 列只要求 JSON 对象，完整旅行领域字段仍需由上层校验。当前没有用户认证、LangGraph 检查点或自动生成行程。

### 保存和读取演示草稿

在 `backend` 目录执行下面的命令，**会向配置的数据库新增一个会话、一份需求快照和一份演示草稿**。每次不带 `--session-id` 运行都会新增一组记录。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo_trip --env-file .env
```

返回 JSON 中有 `session_id`、`itinerary_id`、版本号和预算；演示预算应为 `2442.00` 元。记录 `session_id`，关闭终端后仍可在 `backend` 目录运行以下命令读取（把占位文字换成实际 ID）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo_trip --env-file .env --session-id "替换为刚才的session_id"
```

第二条命令只读取，不新建会话或草稿。演示不调用模型，逐日活动为空，金额来自已有预算模块；它验证数据保存与重读，不代表生成了可出行的完整行程。

存储阅读顺序：先看 [test_trip_service.py](backend/tests/test_trip_service.py) 中的正常保存和失败回滚用例，再看 [trip_service.py](backend/app/services/trip_service.py)，最后看 [demo_trip.py](backend/scripts/demo_trip.py) 如何把预算计算和存储串起来。

| TripService 方法 | 功能 |
| --- | --- |
| `create_session(title)` | 创建会话并提交，标题去首尾空白后须为1到200字符 |
| `get_session(session_id)` | 读取会话；不存在返回 `None` |
| `save_draft(session_id, request_json=..., itinerary_json=...)` | 在同一事务保存需求和草稿，分别分配版本，更新会话修改时间 |
| `get_draft(session_id, version=...)` | 返回草稿及其关联需求；省略版本则取最新，不存在返回 `None` |

`SavedDraft.requirement` 是需求快照，`SavedDraft.itinerary` 是行程快照。返回对象已脱离 ORM Session，修改其属性不会自动写回数据库。调用方应将 Decimal/date 等通过 Pydantic `model_dump(mode="json")` 转为 JSON 可存储值。

每次方法调用使用独立 SQLAlchemy Session；一次 `save_draft` 中两张快照表及会话修改时间一起提交，失败一起回滚。会话创建是独立事务，演示程序不是把“创建会话+保存草稿”包成一个大事务。同会话写入先锁会话行，再取下一版本；使用 PostgreSQL 默认 `READ COMMITTED` 隔离级别，不同会话可以并行。

**`downgrade base` 会删除这三张业务表及其数据，不要在已存业务数据的库上随意执行。** 正式库中的业务数据应在迁移前备份；不要使用回滚命令代替清理测试数据。


## 代码结构

```text
TravelMindAI/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI 工厂、生命周期、中间件与健康检查
│   │   ├── config.py                # 配置读取与校验
│   │   ├── database.py              # Engine 创建与只读连接检查函数
│   │   ├── models/                  # SQLAlchemy 表映射
│   │   │   ├── __init__.py          # 登记模型，供 Alembic 收集元数据
│   │   │   └── trip.py              # 旅行会话、需求版本、草稿版本
│   │   ├── schemas/                 # Pydantic 请求/响应 DTO
│   │   │   ├── __init__.py
│   │   │   ├── common.py            # 统一错误响应
│   │   │   ├── budget.py            # 预算输入、结果与校验
│   │   │   └── trip.py              # 会话/草稿输入和响应
│   │   ├── api/                     # HTTP 路由，相当于 Controller
│   │   │   ├── __init__.py
│   │   │   ├── budget.py            # 预算估算接口
│   │   │   └── trips.py             # 会话与草稿接口
│   │   └── services/                # 业务规则及事务
│   │       ├── __init__.py
│   │       ├── budget_service.py    # 纯预算计算，供不同入口复用
│   │       └── trip_service.py      # TripService：保存、读取与版本管理
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── check_db.py              # 单独运行的数据库连接检查命令
│   │   └── demo_trip.py             # 单独运行的预算草稿保存/读取演示
│   ├── migrations/                 # Alembic 入口、模板及不可随意修改的历史迁移
│   ├── tests/                      # 预算、接口、配置、存储、迁移与导入安全测试
│   ├── pyproject.toml              # 依赖声明、打包、测试和检查工具配置
│   ├── uv.lock                     # 精确依赖版本
│   └── .env.example                # 可复制的配置模板
├── docs/                           # 技术设计、实现说明与审阅图
├── assets/                         # 项目材料
├── old_learn/                      # 独立学习脚本，不参与正式应用启动
└── README.md                       # 功能、安装和使用说明
```

`old_learn/` 收录 LangChain 与 LangGraph 示例，包括提示词模板、流式输出、结构化输出、工具调用和对话历史。示例与后端应用独立，部分脚本需要额外依赖、模型密钥或外部服务。

## 开发检查

在 `backend` 目录运行：

```powershell
python -m uv run --locked python -X utf8 -m pytest
python -m uv run --locked ruff check .
python -m uv run --locked python -m compileall -q app scripts migrations
python -m uv run --locked mypy app scripts
```

测试覆盖预算计算与边界条件、HTTP 契约、配置优先级、数据库异常脱敏，以及模块导入不联网的行为。

默认未设置测试数据库地址时会跳过真实 PostgreSQL 测试；其中数据库不可达用例连接本机关闭端口，不需要真实数据库。需要验证真实数据库时，在 `backend` 目录执行下面命令：它只在当前进程中把 `.env` 的连接地址交给测试，不打印地址或密码；数据库用户需要创建 schema 的权限。

```powershell
@'
import os
from pathlib import Path
import pytest
from app.config import load_settings
settings = load_settings(Path('.env'))
os.environ['TRAVELMIND_TEST_DATABASE_URL'] = settings.database_url.get_secret_value()
raise SystemExit(pytest.main(['-q']))
'@ | .\.venv\Scripts\python.exe -X utf8 -
```
