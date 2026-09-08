# TravelMindAI

TravelMindAI 是面向旅行场景的 Python 后端项目，提供结构化预算估算、费用明细和超支判断。通过 FastAPI 暴露 HTTP 接口，将参数校验、预算规则与数据访问分层组织，便于阅读、测试和集成。

## 主要功能

- **旅行预算估算**：根据行程天数、同行人数、总预算和住宿档位计算全团费用，支持 2–5 天、1–8 人的行程。
- **费用明细与预算判断**：返回住宿、城际交通、市内交通、餐饮、门票及活动费用，并计算预留金、预计总额、余额和是否超支。
- **输入校验**：校验金额精度、人数、天数、住宿类型与日期一致性，拒绝未知字段和非法输入。
- **接口文档与请求追踪**：提供 Swagger UI、OpenAPI 描述、健康检查，以及响应体和响应头中的请求编号。
- **PostgreSQL 连接工具**：提供独立的只读连接检查命令，支持延迟连接、连接超时和连接信息脱敏。

预算使用 `demo-cny-v1` 固定演示单价，金额单位为人民币，不代表实时酒店、交通或门票报价。预算计算无需模型 API Key，也不依赖数据库连接。

## 技术实现

| 层次 | 实现 | 职责 |
| --- | --- | --- |
| HTTP 接口 | FastAPI、Uvicorn | 应用工厂、路由、健康检查与请求编号中间件 |
| 数据契约 | Pydantic | 请求校验、日期关系校验与结构化响应 |
| 预算规则 | Python、Decimal | 独立计算函数、分类计费、精确金额运算 |
| 环境配置 | Pydantic Settings | 环境变量与显式配置文件加载、敏感字段脱敏 |
| 数据库连接 | SQLAlchemy、psycopg | PostgreSQL 连接池管理与只读连通性检查 |
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
python -m uv run --locked uvicorn travelmind.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

启动后访问：

- [Swagger UI](http://127.0.0.1:8000/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)

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
| `GET /api/v1/health/ready` | 检查预算服务就绪状态，不探测数据库或其他外部服务 |

### 配置与 PostgreSQL

应用配置使用 `TRAVELMIND_` 前缀，支持 `TRAVELMIND_APP_NAME`、`TRAVELMIND_ENVIRONMENT` 和可选的 `TRAVELMIND_DATABASE_URL`。配置优先级为环境变量、显式指定的配置文件、默认值；模块导入不会自动读取 `.env` 或发起网络连接。

使用数据库工具时，准备可访问的 PostgreSQL 实例。在 `backend` 目录将 `.env.example` 复制为 `.env`，填写连接地址，再运行：

```powershell
python -m uv run --locked python -X utf8 -m travelmind.persistence.database --env-file .env
```

连接地址格式见 [数据库配置示例](backend/.env.example)。该命令只查询当前数据库名和用户名；连接池按需建立连接，命令结束后释放资源。真实 `.env` 由 Git 忽略，数据库配置不参与预算接口的计算。

## 代码结构

```text
backend/
├── src/travelmind/
│   ├── main.py                 # 应用工厂、中间件、异常处理与健康检查
│   ├── settings.py             # 配置加载
│   ├── schemas.py              # 请求与响应结构
│   ├── api/v1/budget.py        # 预算接口
│   ├── domain/budget.py        # 预算计算规则
│   └── persistence/database.py # PostgreSQL 连接工具
├── tests/                      # 预算、接口、配置、连接与导入行为测试
├── pyproject.toml              # 依赖声明与工具配置
└── uv.lock                     # 精确依赖版本
```

`old_learn/` 收录 LangChain 与 LangGraph 示例，包括提示词模板、流式输出、结构化输出、工具调用和对话历史。示例与后端应用独立，部分脚本需要额外依赖、模型密钥或外部服务。

## 开发检查

在 `backend` 目录运行：

```powershell
python -m uv run --locked python -X utf8 -m pytest
python -m uv run --locked ruff check .
python -m uv run --locked ruff format --check .
python -m uv run --locked mypy src
```

测试覆盖预算计算与边界条件、HTTP 契约、配置优先级、数据库异常脱敏，以及模块导入不联网的行为。
