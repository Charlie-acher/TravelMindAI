/** 流式对话回归：使用已有Vite读取TS，不引入测试依赖。运行 node tests/requirement-stream.mjs。 */
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

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
    return streamResponse(chunks(frame('done', { ...saved, message_id: pending.message_id })))
  }
  await state.send()
  assert.equal(requests[1].message_id, requests[0].message_id)
  assert.equal(state.error.value, '')
  assert.equal(state.messages.value.at(-1).text, saved.response.reply)
  assert.deepEqual(state.messages.value.at(-1).restaurants, [restaurant])
  assert.equal(state.draft.value, '')
  assert.equal(JSON.parse(storage.get('travelmind.requirement-conversation.v2.test-account')).pending, null)

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

  let unauthorized = false
  window.addEventListener('travelmind:unauthorized', () => { unauthorized = true })
  globalThis.fetch = async () => Response.json({ error: { message: '请先登录' } }, { status: 401 })
  await assert.rejects(sendRequirement('test-session', { message: '杭州', message_id: 'test', expected_revision: 0 }, () => {}), error => error instanceof ApiError && error.status === 401)
  assert.equal(unauthorized, true)
  console.log('PASS: SSE UTF-8/CRLF/multiline/heartbeat/reset/done/error; pending retry; 409 recovery; stale stream; 401.')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
