/** 模型选择回归：真实状态层冻结发送选择、恢复历史，并展示备用模型事件。 */
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout })
const requests = []
let turns = []
globalThis.fetch = async (url, init) => {
  if (url.endsWith('/requirement-messages')) return Response.json({ revision: turns.length, turns })
  if (url.endsWith('/stream')) {
    const request = JSON.parse(init.body)
    requests.push(request)
    const turn = { message_id: request.message_id, revision: 1, response: {
      result: { original_message: request.message }, status: 'knowledge', reply: '回答',
      selected_provider: request.selected_provider, used_providers: ['kimi'],
    } }
    turns = [turn]
    return new Response(`event: fallback\ndata: {"from_alias":"qwen","to_alias":"kimi","reason_category":"rate_limit"}\n\nevent: done\ndata: ${JSON.stringify(turn)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  throw new Error(url)
}
try {
  const { useRequirementConversation } = await server.ssrLoadModule('/src/useRequirementConversation.ts')
  const first = useRequirementConversation()
  await first.initialize('user', false)
  await first.selectConversation('session')
  first.selectedProvider.value = 'qwen'
  first.input.value = '杭州有什么好玩的'
  await first.send()
  assert.equal(requests[0].selected_provider, 'qwen')
  assert.deepEqual(first.messages.value.at(-1).usedProviders, ['kimi'])
  assert.ok(first.messages.value.at(-1).process.steps.some(step => step.message.includes('Kimi')))
  assert.ok(first.messages.value.at(-1).process.steps.some(step => step.message.includes('请求频率受限')))
  const restored = useRequirementConversation()
  await restored.initialize('user')
  assert.equal(restored.selectedProvider.value, 'qwen', '备用不能覆盖会话首选')
  restored.clearConversation(false)
  assert.equal(restored.selectedProvider.value, 'deepseek', '新会话回到默认模型')
  // 停止必须等服务释放，再恢复同一输入；换模型重发不能复用旧编号。
  const stopped = useRequirementConversation()
  await stopped.initialize('stop-user', false)
  stopped.openEmpty('stop-session')
  stopped.selectedProvider.value = 'qwen'
  stopped.input.value = '杭州两天慢游'
  let stream, stoppedRequest
  turns = []
  globalThis.fetch = async (url, init) => {
    if (url.endsWith('/requirement-messages')) return Response.json({ revision: 0, turns: [] })
    if (url.endsWith('/execution')) return Response.json({ running: false })
    if (url.endsWith('/stop')) {
      stream.enqueue(new TextEncoder().encode('event: error\ndata: {"status":499,"message":"已停止"}\n\n'))
      stream.close()
      return Response.json({ running: true })
    }
    if (url.endsWith('/stream')) {
      stoppedRequest = JSON.parse(init.body)
      return new Response(new ReadableStream({ start(controller) {
        stream = controller
        controller.enqueue(new TextEncoder().encode('event: progress\ndata: {"stage":"received","message":"已收到"}\n\n'))
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    throw new Error(url)
  }
  const sending = stopped.send()
  for (let attempt = 0; !stopped.canStop.value && attempt < 100; attempt++) await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(stopped.canStop.value, true)
  await stopped.stop()
  await sending
  assert.equal(stopped.busy.value, false)
  assert.equal(stopped.input.value, stoppedRequest.message)
  assert.equal(stopped.selectedProvider.value, 'qwen')
  const stoppedId = stoppedRequest.message_id
  stopped.selectedProvider.value = 'kimi'
  globalThis.fetch = async (url, init) => {
    const next = JSON.parse(init.body)
    assert.equal(next.selected_provider, 'kimi')
    assert.notEqual(next.message_id, stoppedId)
    return new Response(`event: done\ndata: ${JSON.stringify({ message_id: next.message_id, revision: 1, response: { result: { original_message: next.message }, status: 'knowledge', reply: '重试成功', selected_provider: 'kimi', used_providers: ['kimi'] } })}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  await stopped.send()
  assert.equal(stopped.messages.value.at(-1).text, '重试成功')
  console.log('model selection checks passed')
} finally { await server.close() }
