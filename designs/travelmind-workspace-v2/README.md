# TravelMind 对话工作区 v2

2026-09-22 高保真交互设计稿。基于用户确认的 `docs/superpowers/specs/2026-09-22-chat-workspace-design.md`，补充导航与工具调用小图标。

## 打开设计

入口：[http://127.0.0.1:4311/travelmind-workspace-v2/](http://127.0.0.1:4311/travelmind-workspace-v2/)。从项目根目录启动静态预览：

```powershell
python -m http.server 4311 --bind 127.0.0.1 --directory designs
```

同一端口已有该静态服务时直接复用。运行中的预览日志位于 `temp/logs/workspace-v2-preview*.log`，本次保留以供审阅。

## 页面与交互

- **对话**：左侧新建、个人空间和管理员知识库，历史统一为“最近”。可以切换示例历史、搜索、改名、新建和发送示例消息。
- **执行过程**：原子形工具组图标，下接书本检索、文档读取和地图查询图标；展开每行查看公开结果摘要。
- **状态**：右上角打开最近上下文、累计用量、模型统计和最近调用。切换新建或其他历史时不沿用苏州会话的示例数值。
- **文件**：查看当前会话附件、打开草稿、切换版本、放大预览和下载示例 Markdown。附件下载提供明确标注的 TXT 说明，不伪装成原始 PDF 或图片。
- **个人空间**：汇总全部示例会话的附件与草稿，支持类型筛选、文件名搜索、预览和返回所属对话。
- **知识库**：文件表格支持城市、类别和文件名筛选；检索测试支持问题、城市、类别、单文件与数量筛选，展示示例原文及混合检索排序分。
- **角色预览**：左下角账号菜单切换管理员/普通用户，普通用户隐藏知识库和管理员费用入口。此处仅是视觉状态切换，不是正式鉴权。
- **窄屏**：左侧抽屉导航，状态和文件覆盖显示；文件表格在自身容器中横向滚动。

保持一套细线图标：18–20px 导航、16px 工具条，圆头描边；纯图标按钮包含名称和提示。Logo 直接复用已确认的品牌资产，没有重绘。

## 文件分工

1. `index.html`：工作区框架、侧栏、顶栏和文件预览弹窗。
2. `workspace.css`：绿色主题、文字层级、图标规格、三栏排布及手机样式。
3. `workspace.js`：示例状态与切换交互，统一生成 SVG 图标和页面内容。
4. `logo-mark.svg`：从 `designs/travelmind-chat-v1/logo-mark.svg` 复制的品牌标志。
5. `preview-chat.png`、`preview-files.png`、`preview-space.png`、`preview-knowledge.png`、`preview-search.png`、`preview-mobile.png`、`preview-mobile-knowledge.png`：浏览器截图。
6. `_d_meta.json`：设计稿入口与待审阅状态。

## 验收记录与边界

执行 `node --check designs/travelmind-workspace-v2/workspace.js` 通过。

在 Codex 浏览器检查 1440×1000 与 390×844 两个尺寸：

- 状态面板、草稿打开、侧栏版本切换、放大预览版本切换均实际点击检查。
- 个人空间草稿筛选显示 3 份文件，长沙文件预览标题正确。
- 知识库苏州筛选显示 3 份，未匹配文件显示空状态。
- 默认检索返回 3 条示例，限制 1 条返回 1 条，城市改为长沙显示无匹配示例。
- 普通用户隐藏知识库入口；新消息显示示例主题标题，新对话状态显示“暂无调用统计”。
- 手机打开导航并进入知识库正常；聊天与知识库根文档均无横向溢出。
- 检查期间浏览器未报告 warning/error。

仅使用静态示例，没有调用真实模型、工具、数据库或业务 API。统计数值、行程、文件和检索结果均为设计数据。选择本地文件只读取名称与大小用于列表展示，不上传、不解析、不建立索引。刷新重置示例数据。真实自动命名、用量关联、历史过程持久化和权限校验仍按已确认设计等待接入；本轮未改变正式 Vue 页面及后端。

## 设计依据

品牌、字体和图标基础来自本地 `frontend/src/style.css`、`frontend/src/chat.css`、`frontend/src/components/ChatIcon.vue`，以及已确认的聊天设计 v1。

外部参考来自用户指定的 [Yuxi](https://github.com/xerrors/Yuxi)，已在前一轮查看其实际组件：

- [上下文占用组件](https://github.com/xerrors/Yuxi/blob/main/web/src/components/ContextUsageRing.vue)
- [工具组时间线](https://github.com/xerrors/Yuxi/blob/main/web/src/components/ToolCallsGroupComponent.vue)
- [工具结果组件分配](https://github.com/xerrors/Yuxi/blob/main/web/src/components/ToolCallingResult/ToolCallRenderer.vue)
- [个人文件导航](https://github.com/xerrors/Yuxi/blob/main/web/src/components/workspace/WorkspaceSidebar.vue)
- [知识库文件与检索页签](https://github.com/xerrors/Yuxi/blob/main/web/src/views/DataBaseInfoView.vue)

仅参考布局、图标语义和交互分层，未移植 Yuxi 源码或依赖。
