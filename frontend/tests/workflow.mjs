/** 暂停恢复回归：真实状态层在刷新和断网后仍沿用执行号、选择和消息号。 */
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout })
const waiting = { message_id: 'first', revision: 1, response: { result: { original_message: '杭州两天' }, status: 'needs_clarification', reply: '是否采用？\n你可以补充条件继续，或选择保留现状。', workflow: { run_id: 'run-one', status: 'waiting', attempts: 1, can_accept: true } } }
const requests = []
let offline = true
let conflict = false
globalThis.fetch = async (url, init) => {
  if (url.endsWith('/requirement-messages')) return Response.json({ revision: 1, turns: [waiting] })
  if (url.endsWith('/stream')) {
    const request = JSON.parse(init.body)
    requests.push(request)
    if (offline) throw new Error('断网')
    if (conflict) return new Response('event: error\ndata: {"status":409,"message":"仍在处理"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    const turn = { message_id: request.message_id, revision: 2, response: { ...waiting.response, result: { original_message: request.message }, status: 'complete', workflow: { ...waiting.response.workflow, status: 'completed' } } }
    return new Response(`event: done\ndata: ${JSON.stringify(turn)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  throw new Error(url)
}
try {
  const { useRequirementConversation } = await server.ssrLoadModule('/src/useRequirementConversation.ts')
  const first = useRequirementConversation()
  await first.initialize('user', false)
  await first.selectConversation('session')
  assert.equal(first.workflowTarget.value, 'first')
  assert.equal(first.messages.value.at(-1).text, '是否采用？', '旧历史也不显示废弃的按钮引导')
  first.input.value = '先出一版方案'
  await first.send()
  assert.deepEqual(requests[0].workflow_resume, { run_id: 'run-one', action: 'continue' })
  const reloaded = useRequirementConversation()
  await reloaded.initialize('user')
  offline = false
  conflict = true
  await reloaded.send()
  assert.deepEqual(requests[1], requests[0])
  assert.equal(JSON.parse(storage.values().next().value).pending.message_id, requests[0].message_id, '运行锁冲突不能丢失原请求')
  reloaded.input.value = '修改了失败消息'
  await reloaded.send()
  assert.equal(requests.length, 2, '不能用新编号覆盖未确认恢复')
  conflict = false
  await reloaded.retryWorkflow()
  assert.deepEqual(requests[2], requests[0])
  assert.equal(reloaded.workflowTarget.value, null)
  reloaded.input.value = '好吧'
  await reloaded.send()
  assert.equal(requests[3].workflow_resume, undefined, '完成后不再接续旧规划')
  console.log('workflow state checks passed')
} finally { await server.close() }
