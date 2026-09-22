/** 工作台回归：真实状态层验证工具事件合并、历史恢复和分隔线宽度约束。 */
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createRenderer, createSSRApp, h, nextTick, ssrContextKey } from 'vue'
import { renderToString } from 'vue/server-renderer'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const storage = new Map()
const local = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
globalThis.localStorage = { getItem: key => local.get(key) ?? null, setItem: (key, value) => local.set(key, value) }
globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, innerWidth: 1440 })
const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {} })
let app
try {
  const { default: ThinkingProcess } = await server.ssrLoadModule('/src/components/ThinkingProcess.vue')
  const processHtml = await renderToString(createSSRApp({ render: () => h(ThinkingProcess, { steps: [
    { stage: 'received', message: '已收到' },
    { stage: 'knowledge', message: '检索知识库', call_id: 'tool', status: 'completed', summary: '找到3条资料', elapsed_seconds: 0.42 },
  ], seconds: 1.7 }) }))
  assert.ok(processHtml.includes('1 次工具调用') && !processHtml.includes('2 次工具调用'))
  assert.ok(processHtml.includes('tool-detail') && processHtml.includes('找到3条资料') && processHtml.includes('0.4 秒'))
  const groupedHtml = await renderToString(createSSRApp({ render: () => h(ThinkingProcess, { steps: [
    { stage: 'history', message: '不应逐条展示的准备阶段' },
    ...Array.from({ length: 5 }, (_, i) => ({ stage: 'knowledge', message: '检索知识库', call_id: `k${i}`, status: 'completed' })),
    { stage: 'knowledge_read', message: '读取原文', call_id: 'read', status: 'completed' },
    { stage: 'search', message: '检索网页', call_id: 'web', status: 'failed' },
    { stage: 'map', message: '查询地点', call_id: 'map', status: 'running' },
  ] }) }))
  assert.equal((groupedHtml.match(/class="process-group /g) || []).length, 4, '重复调用合并为四类，原文读取独立于检索')
  assert.ok(!groupedHtml.includes('不应逐条展示的准备阶段'))
  assert.ok(groupedHtml.includes('5 次') && groupedHtml.includes('失败') && groupedHtml.includes('未完成'))
  for (const steps of [
    [{ stage: 'research_start', message: '研究开始' }, { stage: 'research_done', message: '研究返回' }],
    [{ stage: 'subagent', message: '研究助手', call_id: 'agent', status: 'completed' }],
  ]) {
    const html = await renderToString(createSSRApp({ render: () => h(ThinkingProcess, { steps }) }))
    assert.ok(html.includes('1 次子智能体调用') && html.includes('研究助手'))
    assert.ok(!html.includes('1 次工具调用'), '子智能体独立计数，不冒充资料工具')
  }
  const { default: ItineraryCard } = await server.ssrLoadModule('/src/components/ItineraryCard.vue')
  const snapshot = { itinerary_id: 'saved-version', version: 2, operation: 'create', can_undo: false, changes: [],
    plan: { title: '苏州三日慢游计划', destination: '苏州', days: [{ day: 1, date: null, activities: [
      { place: { id: 'garden', map: { name: '拙政园', status: 'unavailable' }, sources: [] }, start_time: '09:00', duration_minutes: 90 },
    ] }], budget: { travelers: 2, nights: 0, costs: {}, assumptions: [], total: '100', total_budget: '3000', remaining: '2900' }, warnings: [] } }
  const cardHtml = await renderToString(createSSRApp({ render: () => h(ItineraryCard, { snapshot, compact: true, undoAvailable: false, busy: false }) }))
  assert.ok(cardHtml.includes('苏州三日慢游计划') && cardHtml.includes('拙政园'))
  assert.ok(cardHtml.includes('打开行程草稿') && !cardHtml.includes('预算明细与假设'), '聊天显示摘要，完整预算留在文件预览')
  const { default: PersonalFilesPanel } = await server.ssrLoadModule('/src/components/PersonalFilesPanel.vue')
  let files, exposedFiles
  const pendingFiles = new Map()
  globalThis.fetch = async url => String(url).includes('/personal-files?')
    ? Response.json({ items: [], total: 0 })
    : new Promise(resolve => pendingFiles.set(String(url).split('/').at(-1), resolve))
  app = renderer.createApp({ setup() {
    files = PersonalFilesPanel.setup({ sessionId: 'saved-session', refreshKey: 0 }, { expose(value) { exposedFiles = value }, emit() {} })
    return () => null
  } })
  app.provide(ssrContextKey, {}); app.mount({})
  const firstFile = exposedFiles.openItinerary('version-1')
  await nextTick()
  const secondFile = exposedFiles.openItinerary('version-2')
  await nextTick()
  pendingFiles.get('version-2')(Response.json({ item: { id: 'version-2', file_name: '新版.md' }, itinerary: { ...snapshot, version: 2 }, versions: [] }))
  await secondFile
  pendingFiles.get('version-1')(Response.json({ item: { id: 'version-1', file_name: '旧版.md' }, itinerary: { ...snapshot, version: 1 }, versions: [] }))
  await firstFile
  assert.equal(files.detail.value.item.id, 'version-2', '迟到的旧卡片文件请求不能覆盖新选择')
  assert.equal(files.selectedItem.value.file_name, '新版.md')
  app.unmount(); app = null
  const { default: App } = await server.ssrLoadModule('/src/App.vue')
  let ui
  app = renderer.createApp({ setup() { ui = App.setup({}, { expose() {} }); return () => null } })
  app.provide(ssrContextKey, {})
  app.mount({})
  ui.workspaceWidth.value = 1100
  ui.setInspectorWidth(900)
  assert.equal(ui.inspectorWidth.value, 653, '扣除分隔线后给聊天保留至少440像素')
  ui.setInspectorWidth(10)
  assert.equal(ui.inspectorWidth.value, 300)
  ui.resizeWithKeyboard({ key: 'ArrowLeft', shiftKey: true, preventDefault() {} })
  assert.equal(ui.inspectorWidth.value, 350)
  assert.equal(local.get('travelmind.workspace.inspector-width.v1'), '350')
  ui.workspaceWidth.value = 600
  assert.equal(ui.narrowInspector.value, true)
  ui.inspector.value = 'files'
  ui.statusOpen.value = true
  ui.onShortcut({ key: 'Escape' })
  assert.equal(ui.inspector.value, 'files', 'Escape先交给原生状态浮层，不连带关闭文件')
  ui.statusOpen.value = false
  ui.onShortcut({ key: 'Escape' })
  assert.equal(ui.inspector.value, null, '没有状态浮层时Escape继续关闭文件')
  app.unmount(); app = null

  const { useRequirementConversation } = await server.ssrLoadModule('/src/useRequirementConversation.ts')
  const state = useRequirementConversation()
  await state.initialize('workspace-test', false)
  state.openEmpty('workspace-session')
  state.input.value = '查询苏州资料'
  let controller, request, saved
  globalThis.fetch = async (_url, init) => {
    request = JSON.parse(init.body)
    return new Response(new ReadableStream({ start(value) { controller = value } }), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  const encode = (event, data) => new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
  const tick = () => new Promise(resolve => setImmediate(resolve))
  const sending = state.send()
  await tick()
  controller.enqueue(encode('progress', { stage: 'knowledge', message: '正在检索', call_id: 'one', status: 'running' }))
  controller.enqueue(encode('progress', { stage: 'knowledge', message: '读取了3条资料', call_id: 'one', status: 'completed', elapsed_seconds: 0.42 }))
  controller.enqueue(encode('progress', { stage: 'knowledge', message: '检索另一个城市', call_id: 'two', status: 'running' }))
  await tick()
  assert.equal(state.progress.value.length, 2, '同一编号开始结束合并，重复工具不同编号分别保留')
  assert.equal(state.progress.value[0].status, 'completed')
  assert.equal(state.progress.value[0].elapsed_seconds, 0.42)
  const process = { steps: [{ stage: 'knowledge', message: '已读取资料', call_id: 'one', status: 'completed', elapsed_seconds: 0.42 }], seconds: 1.7 }
  saved = { message_id: request.message_id, revision: 1, response: { result: { original_message: request.message }, reply: '苏州资料', status: 'knowledge', process } }
  controller.enqueue(encode('done', saved)); controller.close()
  await sending
  assert.deepEqual(state.messages.value.at(-1).process, process, '服务端确认过程优先于页面计时')
  globalThis.fetch = async () => Response.json({ revision: 1, turns: [saved] })
  await state.reloadConversation()
  assert.deepEqual(state.messages.value.at(-1).process, process, '刷新恢复公开过程，不重新调用工具')
  state.input.value = '再查询'
  globalThis.fetch = async () => new Response(new ReadableStream({ start(value) {
    value.enqueue(encode('progress', { stage: 'map', message: '正在查地图', call_id: 'failed-map', status: 'running' }))
    value.enqueue(encode('error', { message: '查询中断', status: 503 }))
    value.close()
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
  await state.send()
  assert.equal(state.failedProcess.value.steps[0].call_id, 'failed-map', '失败后仍可查看本页未完成过程')
  state.clearConversation()
  assert.equal(state.failedProcess.value, null, '退出或新建不残留私人过程')
  await nextTick()
  console.log('PASS: workspace divider bounds/persistence/keyboard; tool id merge; saved process restore; interrupted process isolation')
} finally { app?.unmount(); await server.close() }
