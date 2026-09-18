/** 流式对话回归：使用已有Vite读取TS，不引入测试依赖。运行 node tests/requirement-stream.mjs。 */
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const originalFetch = globalThis.fetch
const encoder = new TextEncoder()
const restaurant = { poi_id: 'amap-test', name: '测试餐馆', address: '测试地址', rating: 4.6, distance_m: 125, reference_cost: '60', location: { longitude: 120, latitude: 30, coordinate_system: 'GCJ-02' } }
const saved = { message_id: 'saved-message', revision: 1, response: { reply: '杭州餐馆推荐。你更想吃火锅还是烧烤？', status: 'knowledge', result: { original_message: '杭州吃什么' }, dining: { items: [restaurant] } } }
const frame = (event, data) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`
const nextEvent = () => new Promise(resolve => setImmediate(resolve))
const streamResponse = body => new Response(body, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
const chunks = (text, size = 1) => new ReadableStream({
  start(controller) {
    const bytes = encoder.encode(text)
    for (let index = 0; index < bytes.length; index += size) controller.enqueue(bytes.slice(index, index + size))
    controller.close()
  },
})

try {
  const { renderMarkdown } = await server.ssrLoadModule('/src/markdown.ts')
  assert.ok(renderMarkdown('### 苏州\n- **拙政园**').includes('<strong>拙政园</strong>'))
  const untrusted = renderMarkdown('<script>alert(1)</script>\n[x](javascript:alert(1))\n![x](https://example.com/track.png)')
  assert.ok(!untrusted.includes('<script>') && !untrusted.includes('href="javascript:') && !untrusted.includes('<img'))
  const { default: ThinkingProcess } = await server.ssrLoadModule('/src/components/ThinkingProcess.vue')
  const processHtml = await renderToString(createSSRApp({ render: () => h(ThinkingProcess, {
    steps: [{ stage: 'search', message: '正在查找苏州资料' }], running: true,
  }) }))
  assert.ok(!processHtml.includes('尚未完成的回答') && !processHtml.includes('回答草稿'))
  assert.ok(!processHtml.includes(' open'))
  assert.ok(processHtml.includes('thinking-dot'))
  assert.match(processHtml, /<summary\b[^>]*>[\s\S]*正在查找苏州资料[\s\S]*查看过程[\s\S]*<\/summary>/)
  const finishedHtml = await renderToString(createSSRApp({ render: () => h(ThinkingProcess, {
    steps: [{ stage: 'search', message: '已检索苏州资料' }], seconds: 3,
  }) }))
  assert.ok(finishedHtml.includes('用时 3 秒'))
  assert.ok(!finishedHtml.includes(' open'))
  assert.ok(!finishedHtml.includes('回答草稿内容'))
  const { default: AnswerSources } = await server.ssrLoadModule('/src/components/AnswerSources.vue')
  const knowledge = { status: 'answered', points: [], sources: [{ id: 1, hit: {
    file_name: '深圳攻略.pdf', chunk: { page_number: 8, section_path: ['看海'], text: '深圳西涌适合看海。<script>恶意内容</script>' },
  } }], web_search: { status: 'found', items: [
    { id: 6, title: '旅游公告', url: 'https://example.org/notice', content: '开放情况以公告为准。' },
    { id: 7, title: '危险链接', url: 'javascript:alert(1)', content: '不能执行。' },
  ] } }
  const sourcesHtml = await renderToString(createSSRApp({ render: () => h(AnswerSources, { knowledge }) }))
  assert.ok(sourcesHtml.includes('深圳攻略.pdf') && sourcesHtml.includes('第 8 页'))
  assert.ok(sourcesHtml.includes('深圳西涌适合看海。') && sourcesHtml.includes('本轮参考资料'))
  assert.ok(!sourcesHtml.includes('<script>') && !sourcesHtml.includes('href="javascript:'))
  assert.ok(sourcesHtml.includes('href="https://example.org/notice"'))
  const groupedKnowledge = { ...knowledge, sources: [...knowledge.sources,
    { id: 2, hit: { ...knowledge.sources[0].hit, chunk: { ...knowledge.sources[0].hit.chunk, page_number: 9, text: '第二段原文' } } }],
    web_search: { status: 'not_requested', items: [] } }
  const groupedHtml = await renderToString(createSSRApp({ render: () => h(AnswerSources, { knowledge: groupedKnowledge }) }))
  assert.equal(groupedHtml.split('深圳攻略.pdf').length - 1, 1, '同一文件只显示一个来源分组')
  assert.ok(groupedHtml.includes('2 段原文') && groupedHtml.includes('第二段原文'))
  saved.response.knowledge = knowledge
  // 百度标点按纬度、经度传值；新旧坐标系与中文名称均不得在链接中混淆。
  const { baiduMapLink } = await server.ssrLoadModule('/src/mapLink.ts')
  for (const [coordinate_system, expected] of [['BD-09', 'bd09ll'], ['GCJ-02', 'gcj02']]) {
    const link = new URL(baiduMapLink({ longitude: 120, latitude: 30, coordinate_system }, '西湖 & 入口', '湖滨路 1 号'))
    assert.equal(link.origin + link.pathname, 'https://api.map.baidu.com/marker')
    assert.equal(link.searchParams.get('location'), '30,120')
    assert.equal(link.searchParams.get('coord_type'), expected)
    assert.equal(link.searchParams.get('title'), '西湖 & 入口')
    assert.equal(link.searchParams.get('content'), '湖滨路 1 号')
    assert.equal(link.searchParams.get('output'), 'html')
    assert.equal(link.searchParams.get('src'), 'webapp.TravelMindAI.TravelMindAI')
  }
  const { default: RestaurantCards } = await server.ssrLoadModule('/src/components/RestaurantCards.vue')
  const baiduRestaurant = { ...restaurant, location: { ...restaurant.location, coordinate_system: 'BD-09' } }
  for (const [props, label, coord] of [
    [{ items: [baiduRestaurant] }, '百度公开评分', 'bd09ll'],
    [{ items: [restaurant], provider: 'amap' }, '高德公开评分', 'gcj02'],
  ]) {
    const html = await renderToString(createSSRApp({ render: () => h(RestaurantCards, props) }))
    for (const expected of [label, 'api.map.baidu.com/marker', `coord_type=${coord}`]) assert.ok(html.includes(expected), expected)
  }
  const { default: AttractionCards } = await server.ssrLoadModule('/src/components/AttractionCards.vue')
  const attraction = { name: '西湖', city: '杭州', description: '湖景', reason: '散步', source_ids: [1], ticket: { status: 'unknown' },
    location: { status: 'found', location: restaurant.location, entrance: { ...baiduRestaurant.location, latitude: 31 } } }
  const attractionHtml = await renderToString(createSSRApp({ render: () => h(AttractionCards, { items: [attraction] }) }))
  assert.ok(attractionHtml.includes('location=31%2C120'), '景点优先使用入口坐标')
  assert.ok(attractionHtml.includes('coord_type=bd09ll'))
  assert.ok(!(await renderToString(createSSRApp({ render: () => h(AttractionCards, { items: [{ ...attraction, location: { status: 'no_match' } }] }) }))).includes('api.map.baidu.com/marker'))
  const withoutLocation = await renderToString(createSSRApp({ render: () => h(AttractionCards, {
    items: [{ ...attraction, location: { status: 'not_requested' } }],
  }) }))
  assert.ok(!withoutLocation.includes('详细地点') && !withoutLocation.includes('未成功'))
  assert.ok(withoutLocation.includes('参考资料 [1]') && !withoutLocation.includes('api.map.baidu.com/marker'))
  assert.ok(withoutLocation.includes('attraction-brief'), '普通推荐使用无大边框的紧凑列表')
  assert.ok(!attractionHtml.includes('attraction-brief'), '真实定位保留地图卡片')
  const { readRequirementStream, sendRequirement } = await server.ssrLoadModule('/src/api/requirements.ts')
  const { ApiError } = await server.ssrLoadModule('/src/api/http.ts')
  const events = ': heartbeat\r\n\r\nevent: progress\r\ndata: {"stage":"retrieval",\r\ndata: "message":"正在检索杭州资料"}\r\n\r\n'
    + frame('draft', { text: '杭州 🌿 的餐馆' }) + frame('reset', {})
    + frame('draft', { text: '已重新整理' }) + frame('done', saved)
  for (let size = 1; size <= 31; size++) {
    const updates = []
    assert.deepEqual(await readRequirementStream(chunks(events, size), update => updates.push(update)), saved)
    assert.deepEqual(updates.map(update => update.event), ['progress', 'draft', 'reset', 'draft'])
    assert.equal(updates[0].data.message, '正在检索杭州资料')
    assert.equal(updates[1].data.text, '杭州 🌿 的餐馆')
  }
  assert.deepEqual(await readRequirementStream(chunks(frame('done', saved).replaceAll('\r\n', '\n')), () => {}), saved)
  await assert.rejects(readRequirementStream(chunks(frame('draft', { text: '半段回答' })), () => {}), /尚未确认保存/)
  await assert.rejects(readRequirementStream(chunks(frame('done', saved).trimEnd()), () => {}), /尚未确认保存/)
  await assert.rejects(readRequirementStream(chunks(frame('error', { status: 409, message: '对话已更新', request_id: 'test' })), () => {}), error => error instanceof ApiError && error.status === 409)
  await assert.rejects(readRequirementStream(chunks('event: draft\ndata: {坏JSON\n\n'), () => {}), /无法读取的回复/)
  await assert.rejects(readRequirementStream(new ReadableStream({ start(controller) { controller.error(new Error('断网')) } }), () => {}), /断网/)

  // 模拟浏览器存储与网络，检查草稿、重试编号、冲突恢复和退出后的旧流隔离。
  const storage = new Map()
  globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout })
  const { useRequirementConversation } = await server.ssrLoadModule('/src/useRequirementConversation.ts')
  // 新会话创建也可能等待网络：点击时清空，失败恢复；后续新草稿不能被旧请求覆盖。
  const creating = useRequirementConversation()
  let rejectCreate
  globalThis.fetch = async () => new Promise((_resolve, reject) => { rejectCreate = reject })
  creating.input.value = '想去苏州'
  const createSend = creating.send()
  assert.equal(creating.input.value, '')
  rejectCreate(new Error('创建失败'))
  await createSend
  assert.equal(creating.input.value, '想去苏州')
  for (const succeeds of [true, false]) {
    const composing = useRequirementConversation()
    composing.openEmpty('compose-session')
    composing.input.value = '杭州吃什么'
    let finish
    globalThis.fetch = async () => new Promise(resolve => { finish = resolve })
    const sending = composing.send()
    composing.input.value = '下一条草稿'
    finish(succeeds ? streamResponse(chunks(frame('done', saved)))
      : streamResponse(chunks(frame('error', { status: 502, message: '连接失败' }))))
    await sending
    assert.equal(composing.input.value, '下一条草稿')
  }
  const state = useRequirementConversation()
  await state.initialize('test-account', false)
  state.openEmpty('test-session')
  state.input.value = '杭州吃什么'
  let controller
  const requests = []
  globalThis.fetch = async (_url, init) => {
    requests.push(JSON.parse(init.body))
    return streamResponse(new ReadableStream({ start(value) { controller = value } }))
  }
  const firstSend = state.send()
  assert.equal(state.input.value, '', '点击发送立即清空，不等待模型或网络')
  await nextEvent()
  controller.enqueue(encoder.encode(frame('progress', { stage: 'answer', message: '正在生成回答' }) + frame('draft', { text: '未完成的公开草稿' })))
  await nextEvent()
  assert.equal(state.draft.value, '未完成的公开草稿')
  assert.equal(state.progress.value[0].message, '正在生成回答')
  assert.equal(state.messages.value.some(message => message.text.includes('公开草稿')), false)
  controller.enqueue(encoder.encode(frame('reset', {})))
  await nextEvent()
  assert.equal(state.draft.value, '')
  controller.close()
  await firstSend
  assert.match(state.error.value, /尚未确认保存/)
  const pending = JSON.parse(storage.get('travelmind.requirement-conversation.v2.test-account')).pending
  assert.equal(pending.message_id, requests[0].message_id)
  assert.equal(state.input.value, '杭州吃什么')
  globalThis.fetch = async (_url, init) => {
    requests.push(JSON.parse(init.body))
    return streamResponse(chunks(frame('progress', { stage: 'answer', message: '已生成最终回答' })
      + frame('draft', { text: '最终公开草稿' }) + frame('done', { ...saved, message_id: pending.message_id })))
  }
  await state.send()
  assert.equal(requests[1].message_id, requests[0].message_id)
  assert.equal(state.error.value, '')
  assert.equal(state.messages.value.at(-1).text, saved.response.reply)
  assert.deepEqual(state.messages.value.at(-1).restaurants, [restaurant])
  assert.deepEqual(state.messages.value.at(-1).knowledge, knowledge)
  assert.deepEqual(state.messages.value.at(-1).process.steps, [{ stage: 'answer', message: '已生成最终回答' }])
  assert.equal(state.messages.value.at(-1).process.draft, '最终公开草稿', 'done后先把本页过程保存到消息，再清理在途状态')
  assert.equal(state.draft.value, '')
  const remembered = JSON.parse(storage.get('travelmind.requirement-conversation.v2.test-account'))
  assert.equal(remembered.pending, null)
  assert.equal('draft' in remembered, false, '公开草稿不得写入浏览器会话状态')
  assert.equal('progress' in remembered, false, '处理步骤不得写入浏览器会话状态')

  globalThis.fetch = async () => Response.json({ session_id: 'test-session', revision: 1, turns: [saved] })
  await state.reloadConversation()
  assert.deepEqual(state.messages.value.at(-1).knowledge, knowledge, '读取历史恢复原文，不重新检索')
  assert.equal(state.messages.value.at(-1).process, undefined, '刷新只恢复服务端最终回答，不恢复页面草稿')
  assert.equal(state.messages.value.at(-1).text, saved.response.reply)

  state.input.value = '改成火锅'
  globalThis.fetch = async (_url, init) => init?.method === 'POST'
    ? streamResponse(chunks(frame('error', { status: 409, message: '对话已更新', request_id: 'test' })))
    : Response.json({ session_id: 'test-session', revision: 2, turns: [{ ...saved, revision: 2 }] })
  await state.send()
  assert.equal(state.busy.value, false)
  assert.equal(state.revision.value, 2)
  assert.match(state.error.value, /已恢复最新对话/)

  state.input.value = '烧烤'
  globalThis.fetch = async () => streamResponse(new ReadableStream({ start(value) { controller = value } }))
  const staleSend = state.send()
  await nextEvent()
  state.clearConversation()
  controller.enqueue(encoder.encode(frame('draft', { text: '旧账号草稿' }) + frame('done', saved)))
  controller.close()
  await staleSend
  assert.equal(state.draft.value, '')
  assert.equal(state.sessionId.value, null)
  assert.equal(state.messages.value.length, 1)

  // 行程只取已提交快照；撤销失败使用相同请求重试，旧请求不得写回新会话。
  const snapshot = { itinerary_id: 'itinerary-2', version: 2, previous_version: 1, operation: 'modify', can_undo: true,
    plan: { format: 'daily-plan-v1', title: '杭州两日慢游', destination: '杭州',
      days: [1, 2].map(day => ({ day, date: null, activities: [{ start_time: '09:00', duration_minutes: 90, transport: 'walk', transfer_minutes: 30,
        place: { id: `place-${day}`, map: { city: '杭州', name: '西湖', status: 'found', address: '杭州西湖', location: restaurant.location },
          sources: [{ id: 's1', kind: 'knowledge', title: '散步资料', text: '适合沿湖散步。', url: null }] } }] })),
      budget: { days: 2, travelers: 2, nights: 1, rooms: 1, lodging: 'economy', total_budget: '5000.00', price_version: 'demo-cny-v1',
        unit_prices: {}, costs: { accommodation: '200.00', intercity_transport: '600.00' }, subtotal: '800.00', contingency_rate: '0.10', contingency: '80.00', total: '880.00', remaining: '4120.00', over_budget: false, assumptions: ['标准演示单价'] }, warnings: ['路线耗时待核实'] }, changes: ['第2天减少一个景点'] }
  const { default: ItineraryCard } = await server.ssrLoadModule('/src/components/ItineraryCard.vue')
  const card = await renderToString(createSSRApp({ render: () => h(ItineraryCard, { snapshot, undoAvailable: true, busy: false }) }))
  for (const expected of ['第 1 天', '第 2 天', '09:00', 'api.map.baidu.com/marker', 'coord_type=gcj02', '适合沿湖散步', '城际交通', '非实时', '撤销这次修改']) assert.ok(card.includes(expected), expected)
  assert.ok(!(await renderToString(createSSRApp({ render: () => h(ItineraryCard, { snapshot, undoAvailable: false, busy: false }) }))).includes('撤销这次修改'))
  const planTurn = { ...saved, message_id: 'plan-message', revision: 3, response: { ...saved.response, status: 'complete', itinerary: snapshot } }
  assert.deepEqual((await readRequirementStream(chunks(frame('done', planTurn), 7), () => {})).response.itinerary, snapshot)
  // 新行程完成后先露出标题；旧行程之后的流式进度仍跟随底部。
  const scrollingState = useRequirementConversation()
  const scrolls = []
  scrollingState.chatArea.value = { scrollTop: 100, scrollHeight: 2000,
    getBoundingClientRect: () => ({ top: 50 }),
    querySelector: () => ({ getBoundingClientRect: () => ({ top: 350 }) }),
    scrollTo: options => scrolls.push(options.top) }
  scrollingState.openEmpty('scroll-session')
  globalThis.fetch = async () => Response.json({ session_id: 'scroll-session', revision: 3, turns: [planTurn] })
  await scrollingState.reloadConversation()
  await nextEvent()
  assert.equal(scrolls.at(-1), 388, '最终行程顶部留12px边距')
  scrollingState.input.value = '第二天晚一点'
  globalThis.fetch = async () => streamResponse(new ReadableStream({ start(value) { controller = value } }))
  const scrollingSend = scrollingState.send()
  await nextEvent()
  controller.enqueue(encoder.encode(frame('progress', { stage: 'planning', message: '正在调整行程' })))
  await nextEvent()
  assert.equal(scrolls.at(-1), 2000, '在途进度不能跳回旧行程')
  controller.enqueue(encoder.encode(frame('done', planTurn)))
  controller.close()
  await scrollingSend
  await nextEvent()
  assert.equal(scrolls.at(-1), 388)
  scrollingState.input.value = '谢谢'
  globalThis.fetch = async () => streamResponse(chunks(frame('done', saved)))
  await scrollingState.send()
  await nextEvent()
  assert.equal(scrolls.at(-1), 2000, '普通回复仍定位末尾')
  await state.initialize('test-account', false)
  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 3, turns: [planTurn] })
  state.openEmpty('plan-session')
  await state.reloadConversation()
  assert.deepEqual(state.messages.value.at(-1).itinerary, snapshot)
  assert.equal(state.undoTarget.value, planTurn.message_id)
  const undoRequests = []
  globalThis.fetch = async (url, init) => { undoRequests.push({ url, body: JSON.parse(init.body) }); throw new Error('撤销请求断网') }
  await state.undoDraft(planTurn.message_id)
  assert.equal(state.busy.value, false)
  assert.equal(state.pendingUndo.value.target_message_id, planTurn.message_id)
  assert.equal(undoRequests[0].url, '/api/v1/sessions/plan-session/drafts/undo')
  assert.equal(undoRequests[0].body.expected_revision, 3)
  assert.equal(undoRequests[0].body.expected_itinerary_version, 2)
  const retryPayload = { ...state.pendingUndo.value }
  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 3, turns: [planTurn] })
  const restoredState = useRequirementConversation()
  await restoredState.initialize('test-account')
  assert.deepEqual(restoredState.pendingUndo.value, retryPayload, '刷新后保留完整撤销请求')
  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 4,
    turns: [planTurn, { ...planTurn, message_id: retryPayload.operation_id, revision: 4,
      response: { ...planTurn.response, itinerary: { ...snapshot, operation: 'undo', can_undo: false } } }] })
  await restoredState.reloadConversation()
  assert.equal(restoredState.pendingUndo.value, null, '历史已确认成功的撤销无需再重试')
  state.input.value = '下一条消息'
  await state.send()
  assert.equal(undoRequests.length, 1, '未确认的撤销不能被普通发送覆盖')
  globalThis.fetch = async (url, init) => {
    undoRequests.push({ url, body: JSON.parse(init.body) })
    return Response.json({ ...planTurn, message_id: undoRequests[0].body.operation_id, revision: 4,
      response: { ...planTurn.response, itinerary: { ...snapshot, version: 3, operation: 'undo', can_undo: false } } })
  }
  await state.undoDraft(planTurn.message_id)
  assert.deepEqual(undoRequests[1], undoRequests[0])
  assert.equal(state.revision.value, 4)
  assert.equal(state.response.value.itinerary.operation, 'undo')
  assert.equal(state.pendingUndo.value, null)
  assert.equal(state.undoTarget.value, null)
  await state.undoDraft(planTurn.message_id)
  assert.equal(undoRequests.length, 2, '旧卡片不能再次提交撤销')

  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 3, turns: [planTurn] })
  await state.reloadConversation()
  let completeUndo
  globalThis.fetch = async () => new Promise(resolve => { completeUndo = resolve })
  const staleUndo = state.undoDraft(planTurn.message_id)
  await nextEvent()
  state.openEmpty('other-session')
  completeUndo(Response.json(planTurn))
  await staleUndo
  assert.equal(state.sessionId.value, 'other-session')
  assert.equal(state.revision.value, 0)
  assert.equal(state.messages.value.length, 1)
  assert.equal(state.pendingUndo.value, null)

  state.openEmpty('plan-session')
  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 3, turns: [planTurn] })
  await state.reloadConversation()
  globalThis.fetch = async () => new Promise(resolve => { completeUndo = resolve })
  const signedOutUndo = state.undoDraft(planTurn.message_id)
  await nextEvent()
  state.clearConversation()
  completeUndo(Response.json(planTurn))
  await signedOutUndo
  assert.equal(state.sessionId.value, null)
  assert.equal(state.messages.value.length, 1)
  assert.equal(state.pendingUndo.value, null)

  state.openEmpty('plan-session')
  globalThis.fetch = async () => Response.json({ session_id: 'plan-session', revision: 3, turns: [planTurn] })
  await state.reloadConversation()
  globalThis.fetch = async (_url, init) => init?.method === 'POST'
    ? Response.json({ error: { message: '版本已变化' } }, { status: 409 })
    : Response.json({ session_id: 'plan-session', revision: 5, turns: [{ ...planTurn, revision: 5, response: { ...planTurn.response, itinerary: null } }] })
  await state.undoDraft(planTurn.message_id)
  assert.equal(state.revision.value, 5)
  assert.equal(state.pendingUndo.value, null)
  assert.equal(state.undoTarget.value, null)
  assert.equal(state.busy.value, false)
  assert.match(state.error.value, /已恢复最新对话/)

  // 整段删除成功才清理当前对话；失败、删除其他对话和账号切换不得误清空。
  await state.initialize('delete-account', false)
  state.openEmpty('delete-session')
  globalThis.fetch = async () => Response.json({ session_id: 'delete-session', revision: 3, turns: [planTurn] })
  await state.reloadConversation()
  state.input.value = '保留未发送输入'
  const deleteRequests = []
  globalThis.fetch = async (url, init) => {
    deleteRequests.push({ url, method: init.method, source: init.headers.get('X-Requested-With') })
    return Response.json({ error: { message: '删除失败' } }, { status: 503 })
  }
  await assert.rejects(state.deleteConversation('delete-session'), /删除失败/)
  assert.equal(state.sessionId.value, 'delete-session')
  assert.equal(state.input.value, '保留未发送输入')
  assert.equal(state.messages.value.at(-1).messageId, 'plan-message')
  globalThis.fetch = async () => new Response(null, { status: 204 })
  assert.equal(await state.deleteConversation('other-session'), true)
  assert.equal(state.sessionId.value, 'delete-session')
  assert.equal(state.response.value.itinerary.version, 2)
  globalThis.fetch = async () => { throw new Error('撤销尚未到达') }
  await state.undoDraft(planTurn.message_id)
  assert.ok(state.pendingUndo.value)
  globalThis.fetch = async () => new Response(null, { status: 204 })
  assert.equal(await state.deleteConversation('delete-session'), true)
  assert.equal(state.sessionId.value, null)
  assert.equal(state.response.value, null)
  assert.equal(state.messages.value.length, 1)
  assert.equal(state.input.value, '')
  assert.equal(state.pendingUndo.value, null)
  assert.equal(state.undoTarget.value, null)
  assert.equal(storage.has('travelmind.requirement-conversation.v2.delete-account'), false)
  assert.deepEqual(deleteRequests[0], { url: '/api/v1/sessions/delete-session', method: 'DELETE', source: 'TravelMindAI' })
  globalThis.fetch = async (url) => url === '/api/v1/sessions'
    ? Response.json({ session: { id: 'after-delete-session' } })
    : streamResponse(chunks(frame('done', saved)))
  state.input.value = '删除后仍可发送'
  await state.send()
  assert.equal(JSON.parse(storage.get('travelmind.requirement-conversation.v2.delete-account')).sessionId, 'after-delete-session')
  let completeDelete
  globalThis.fetch = async () => new Promise(resolve => { completeDelete = resolve })
  const staleDelete = state.deleteConversation('after-delete-session')
  await nextEvent()
  state.clearConversation()
  await state.initialize('new-account', false)
  state.openEmpty('new-account-session')
  completeDelete(new Response(null, { status: 204 }))
  assert.equal(await staleDelete, false)
  assert.equal(state.sessionId.value, 'new-account-session')
  state.input.value = '在途消息'
  globalThis.fetch = async () => streamResponse(new ReadableStream({ start(value) { controller = value } }))
  const deletedStream = state.send()
  await nextEvent()
  assert.ok(JSON.parse(storage.get('travelmind.requirement-conversation.v2.new-account')).pending)
  globalThis.fetch = async () => new Response(null, { status: 204 })
  await state.deleteConversation('new-account-session')
  controller.enqueue(encoder.encode(frame('draft', { text: '迟到草稿' }) + frame('done', saved)))
  controller.close()
  await deletedStream
  assert.equal(state.sessionId.value, null)
  assert.equal(state.messages.value.length, 1)
  assert.equal(state.draft.value, '')
  assert.equal(state.busy.value, false)
  assert.equal(storage.has('travelmind.requirement-conversation.v2.new-account'), false)

  // 通过真实页面状态验证确认后才删除，以及迟到的历史列表不会复活已删记录。
  const { default: App } = await server.ssrLoadModule('/src/App.vue')
  const { Modal } = await import('@arco-design/web-vue')
  const originalConfirm = Modal.confirm
  const row = { id: 'history-to-delete', title: '待删除历史', updated_at: '2026-09-16T00:00:00Z' }
  let pageState
  const pageApp = createSSRApp(App)
  pageApp.mixin({ created() {
    if (this.$options.__name !== 'App') return
    pageState = this.$.setupState
    pageState.account = { id: 'page-account', username: '测试账号', role: 'user' }
    pageState.modelState = 'configured'
    pageState.sessions = [row]
    pageState.messages = [{ id: 'welcome', role: 'assistant', text: '欢迎' },
      { id: 'card-answer', role: 'assistant', text: '这是餐馆总体建议。', restaurants: [restaurant], nearby: saved.response.dining }]
  } })
  const historyHtml = await renderToString(pageApp)
  assert.ok(historyHtml.includes('待删除历史：更多操作'), '历史操作集中在三点菜单')
  assert.ok(historyHtml.includes('这是餐馆总体建议。'), '带卡片的最终正文仍需单独渲染')
  assert.ok(historyHtml.includes('测试餐馆'), '最终正文之后继续渲染结构化卡片')
  assert.ok(historyHtml.indexOf('这是餐馆总体建议。') < historyHtml.indexOf('测试餐馆'))
  let confirmation
  Modal.confirm = options => { confirmation = options; return { close() { options.onClose?.() }, update() {} } }
  try {
    await pageState.initialize('page-account', false)
    globalThis.fetch = async () => Response.json({ session_id: row.id, revision: 0, turns: [] })
    await pageState.chooseSession(row.id)
    let completeList
    globalThis.fetch = async () => new Promise(resolve => { completeList = resolve })
    const oldList = pageState.loadSessions()
    await nextEvent()
    pageState.confirmDelete(row)
    assert.equal(pageState.sessions.length, 1, '等待用户确认时不删除')
    assert.equal(confirmation.okText, '确认删除')
    assert.equal(confirmation.cancelText, '取消')
    globalThis.fetch = async () => new Response(null, { status: 204 })
    await confirmation.onBeforeOk()
    confirmation.onClose()
    assert.equal(pageState.sessions.length, 0)
    assert.equal(pageState.sessionId, null)
    completeList(Response.json({ items: [row], next_cursor: null }))
    await oldList
    assert.equal(pageState.sessions.length, 0, '旧分页响应不能放回删除项')
    pageState.sessions = [row]
    globalThis.fetch = async () => Response.json({ error: { message: '删除服务暂不可用' } }, { status: 503 })
    pageState.confirmDelete(row)
    await confirmation.onBeforeOk()
    confirmation.onClose()
    assert.equal(pageState.sessions.length, 1, '删除失败必须保留历史')
    assert.match(pageState.listError, /删除服务暂不可用/)
    pageState.confirmDelete(row)
    pageState.clearPrivate()
    let crossAccountDeletes = 0
    globalThis.fetch = async () => { ++crossAccountDeletes; return new Response(null, { status: 204 }) }
    await confirmation.onBeforeOk()
    assert.equal(crossAccountDeletes, 0, '旧确认框不能在切换账号后发删除请求')
  } finally { Modal.confirm = originalConfirm }

  let unauthorized = false
  window.addEventListener('travelmind:unauthorized', () => { unauthorized = true })
  globalThis.fetch = async () => Response.json({ error: { message: '请先登录' } }, { status: 401 })
  await assert.rejects(sendRequirement('test-session', { message: '杭州', message_id: 'test', expected_revision: 0 }, () => {}), error => error instanceof ApiError && error.status === 401)
  assert.equal(unauthorized, true)
  console.log('PASS: SSE parsing; pending retry; itinerary snapshots; undo retry/isolation/conflict; session deletion/current reset/account isolation/list races; stale stream; 401.')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
