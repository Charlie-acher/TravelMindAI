# 三模型选择与四智能体实施计划

> 已被2026-09-21用户确认的[体验优先计划](../plans/2026-09-21-experience-first.md)替代。以下保留为旧设计，不再执行四Agent必交付要求。

> **For agentic workers:** 使用 superpowers:executing-plans 分步实施；每步同步 07/08 并记录实际验证。以下未勾选项均未完成。

**Goal:** 用户在输入框选择 DeepSeek/Kimi/Qwen，故障可快速换模；主智能体真实调用三个独立子智能体完成研究、规划、核对。

**Architecture:** 复用现有模型适配器、外层 LangGraph、检查点、预算和行程保存。统一模型上下文贯穿文本及工具调用；独立 Coordinator/Research/Planner/Reviewer 运行在受约束的委派边界内。

**Tech Stack:** 现有 FastAPI、Pydantic、LangChain/LangGraph、PostgreSQL、Vue 3/TypeScript，不新增代理框架。

**Spec:** [三模型选择与主从四智能体设计修订](../specs/2026-09-21-model-choice-and-four-agents.md)。本计划替代旧 M5-C 后续范围，M5-A/B、M4-A/B/C 历史验收记录保留。

## 全局约束

- 仅 DeepSeek/Kimi/Qwen；用户选择优先，故障才降级。正常聊天不被结构化提取强制阻断。
- 四个 Agent = Coordinator + Research + Planner + Reviewer；必须有独立模型循环和真实委派，不以节点名或开发审查任务充数。
- 不重写已有业务服务，不新增常驻微服务；程序继续负责权限、预算、两次返工上限、校验和幂等发布。
- Python 检查从 backend 执行，正式配置只使用 backend/.env；不输出密钥。临时产物分别放 temp/logs、temp/reports、temp/runtime。
- 任何新增/修改持久字段要同步 08 的字段字典、约束、索引和迁移；不得把下面的拟议契约写成已建表。
- 每两三个文件说明职责、调用顺序和数据流；不自动提交/推送当前已有大量未提交变更。

## 重点验证的边界

1. 同消息编号换模型必须冲突；新尝试引用原任务但不重复发布。
2. 已输出正文、已执行工具后故障，不混接正文、不重复副作用。
3. 停止换模与旧请求迟到并发时，旧执行不能覆盖新选择和草稿。
4. 子任务不能读其他用户/会话附件，也不能把旧审核用于新候选。
5. Kimi 未配置、工具能力不支持或三家全部故障时，界面状态与后端一致。

## 任务 1：M5-C 三模型选择贯穿服务端

**文件：**修改 `backend/app/config.py`、`backend/app/llm/contracts.py`、`providers.py`、`gateway.py`、`gateway_client.py`；随后修改 `backend/app/schemas/requirement/chat.py`、`history.py`、`conversation.py`、`backend/app/api/requirement/routes.py`、`history.py`、`backend/app/services/requirement/history.py`、`backend/app/services/chat/workflow.py`。持久字段落点先核对现有 models/session 与快照结构，再写明确迁移，不新建空表占位。

**接口：**统一 `ProviderName = Literal['deepseek','kimi','qwen']`；请求增加可向后兼容的 `selected_provider`，旧请求默认 DeepSeek。服务端安全元数据返回三家名称、可用状态及原因；每次执行记录 `selected_provider/actual_provider`。`.model` 工具路径也必须进入同一选择与故障策略，不能继续固定构造 DeepSeek。

- [ ] 在 `backend/tests/test_model_gateway.py`、`test_gateway_client.py`、`test_provider_adapters.py` 补失败测试：首选健康时固定使用、未配置排除、原生工具模型选择、MiniMax 拒绝、跨能力传递；先运行确认失败原因。
- [ ] 将加权抽签改为首选优先、固定备用顺序；保留已验证的并发/熔断防竞态逻辑，移除本轮退出的 MiniMax 专用分支与对应专用测试。
- [ ] 在 `backend/tests/test_chat_workflow.py` 补失败测试：HTTP/SSE 选择一致、同 ID 不同选择冲突、恢复沿用冻结选择、历史区分所选与实际模型；实现会话持久化及迁移。
- [ ] 运行上述定向测试、改动范围 Ruff 和 mypy；迁移使用隔离 PostgreSQL 验证 upgrade/字段约束/恢复，正式迁移另记录备份和执行证据。
- [ ] 更新 `.env.example` 和 07/08，逐家记录真实能力验证；未提供 Kimi 配置时如实保留待验收。

## 任务 2：M5-D 输入框选择与快速故障切换

**文件：**修改 `frontend/src/App.vue`、`chat.css`、`useRequirementConversation.ts`、`api/requirements.ts`；修改 `backend/app/llm/gateway.py`、`providers.py` 及取消/重试经过的 API/workflow；新增 `frontend/tests/model-selection.mjs`，扩展 `frontend/tests/requirement-stream.mjs`、`workflow.mjs` 和后端网关/工作流测试。

**接口：**消费任务 1 元数据与选择字段；输入框展示三家下拉框，发送时冻结选择。提供安全的停止并换模重试操作，后端核实旧执行停止后才接受新尝试，事件返回安全原因及实际模型。

- [ ] 先补前后端失败测试：列表状态、发送/刷新恢复、切会话、运行中改选择、停止失败/旧响应迟到、首段后失败、全部不可用。
- [ ] 在 composer 底栏发送按钮左侧接选择器；窄屏隐藏键盘提示，保留键盘可操作的名称和状态。保留附件与输入内容。
- [ ] 实现故障即切备用、连接/首事件超时与取消释放；测试时钟验证 3 秒连接、10 秒首事件初值，以及已知故障后 1 秒内发起备用的调度目标。
- [ ] 验证首段后不跨模型续写；停止/重试从安全边界开始，旧运行不能落库覆盖新运行。相应测试覆盖重点边界 1、2、3、5。
- [ ] 前端执行 `npm run build`，按现有 mjs 测试入口运行受影响回归；后端运行定向测试。浏览器实测三个选项、故障提示、窄屏和刷新；分别记录模拟与真实模型结果。
- [ ] 同步 07/08 和用户操作说明，未完成三家真实工具验收前不标记 M5 整体完成。

## 任务 3：M4-D 四个独立 Agent 与主从委派

**文件：**新增 `backend/app/services/chat/coordinator.py`、`backend/app/services/document/research_agent.py`、`backend/app/schemas/agent.py`；修改 `backend/app/services/chat/service.py`、`workflow.py`、`dining.py`、`backend/app/services/itinerary/graph.py`、`review.py`。沿用既有模块保存四个角色，不为统一目录迁移全部代码。运行记录持久字段先冻结结构，再扩展既有 workflow 模型/迁移和 08 字典。

**接口：**主智能体只通过 `delegate_research/plan/review` 委派；统一 `AgentTask` 携带父任务、归属、约束、模型上下文和产物引用。分别产出 `EvidenceBundle/PlanCandidate/ReviewResult`，结构化校验后才回到主智能体；子状态不直接写共享会话。

- [ ] 新增 `backend/tests/test_agent_delegation.py`，用脚本模型验证主智能体实际发出委派、子智能体独立调用和结果回传；普通聊天无委派、资料问答只研究、规划进入审核、研究缺口返回主智能体再决策。先运行确认失败。
- [ ] 抽出 Research 的证据工具循环，复用 RAG/附件/地图服务，周边 Agent 能力归入研究角色；复用 Planner 原生循环并缩小其职责到规划。
- [ ] 将 Reviewer 单次 JSON 请求升级为独立核对工具循环，使用独立消息和来源视图；原有确定性 `check_plan` 仍是强制门槛。
- [ ] 接入 Coordinator 决策/委派，限制可调用角色、工具次数、任务时限和两次返工；保留程序发布门槛及普通聊天，不允许主智能体直接绕过审核保存方案。
- [ ] 在工作流测试补子任务隔离、候选版本匹配、checkpoint 恢复、重复委派幂等及故障换模场景；验证重点边界 2、3、4。进度事件只显示真实子任务状态。
- [ ] 运行定向测试、Ruff/mypy；真实完成一次研究→规划→独立核对→返工/通过的委派记录，再验证服务重启恢复与数据库版本幂等。
- [ ] 同步 07 逐文件职责与 08 实际调用链、字段字典；只有真实主从委派证据齐备才标记 M4-D 完成。

## 任务 4：联合验收与阶段收尾

- [ ] 后端完整回归、前端受影响回归及构建；外部服务使用隔离测试数据，记录跳过项。
- [ ] 三家分别验证普通回复、知识核对、完整规划、审核返工；仅报告实际配置和调用的具体型号。
- [ ] 注入模型故障，在浏览器观察快速切换、手动停止换模、已输出后重试和恢复；数据库核实同任务仅发布一个版本、历史所选/实际模型正确。
- [ ] 对四个角色出示同一父任务下的真实子任务调用记录；自动测试、真实模型、数据库、浏览器四层分开给结论。
- [ ] 同步 03/04/07/08 的状态；验收记录落盘后只清理本阶段已停止占用的临时文件，保留上传、备份、知识资料和在用服务文件。

本轮仅完成计划修订，以上实现任务均未执行，不复用此前 954 项通过作为新功能证据。
