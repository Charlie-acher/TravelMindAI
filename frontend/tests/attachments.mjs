/** 私人附件回归：使用真实状态层和HTTP封装，网络由本地响应替代。 */
import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const originalFetch = globalThis.fetch
const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout })
const attachment = { id: 'attachment-one', session_id: 'private-session', file_name: '路线.png', size_bytes: 19_574_663, status: 'uploaded', analysis: null, error_message: null }
const calls = []
let offline = true
let uploadWait = null
let listOffline = false
const analysis = { city: '杭州', summary: '路线文字', waypoints: [{ name: '疑似西湖', order: 1, evidence: '西<湖>', needs_confirmation: true }], warnings: ['道路是否可通行尚未核对'] }
let turn
let historyResponse = { revision: 0, turns: [] }
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url, init })
  if (url === '/api/v1/sessions') return Response.json({ session: { id: 'private-session' } })
  if (url.endsWith('/attachments')) {
    if (init.method === 'POST') { if (uploadWait) await uploadWait; return Response.json(attachment) }
    if (listOffline) throw new Error('附件列表暂不可用')
    return Response.json([attachment])
  }
  if (url.endsWith('/requirement-messages')) return Response.json(historyResponse)
  if (url.endsWith('/stream')) {
    if (offline) throw new Error('网络中断')
    const request = JSON.parse(init.body)
    turn = { message_id: request.message_id, revision: 1, response: { status: 'knowledge', reply: '已读取，原行程保持不变。', result: { original_message: request.message }, attachments: [{ ...attachment, analysis }] } }
    return new Response(`event: done\ndata: ${JSON.stringify(turn)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  throw new Error(`未预期请求：${url}`)
}
try {
  const { useRequirementConversation } = await server.ssrLoadModule('/src/useRequirementConversation.ts')
  const state = useRequirementConversation()
  await state.initialize('attachment-user', false)
  assert.equal(typeof state.addAttachments, 'function', '状态层应支持选择私人附件')
  await state.addAttachments([new File(['png'], '路线.png', { type: 'image/png' })])
  assert.equal(state.sessionId.value, 'private-session')
  assert.equal(state.selectedAttachments.value[0].file_name, '路线.png')
  const upload = calls.find(call => call.init.method === 'POST' && call.url.endsWith('/attachments'))
  assert.equal(upload.init.body.get('file').name, '路线.png')
  assert.equal(upload.init.headers.get('X-Requested-With'), 'TravelMindAI')
  assert.equal(upload.init.headers.has('Content-Type'), false, '浏览器负责multipart边界')
  assert.equal(calls.some(call => call.url.endsWith('/stream')), false, '上传不请求模型')
  await state.send()
  const first = JSON.parse(calls.find(call => call.url.endsWith('/stream')).init.body)
  assert.deepEqual(first.attachment_ids, ['attachment-one'])
  assert.equal(first.message, '请帮我读取这些附件')
  assert.deepEqual(JSON.parse(storage.values().next().value).pending.attachment_ids, ['attachment-one'])
  listOffline = true
  const missingMetadata = useRequirementConversation()
  await missingMetadata.initialize('attachment-user')
  assert.deepEqual(missingMetadata.selectedAttachments.value.map(item => item.id), ['attachment-one'], '列表失败也不得丢弃待重试编号')
  await missingMetadata.send()
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), first)
  listOffline = false
  const restored = useRequirementConversation()
  await restored.initialize('attachment-user')
  assert.equal(restored.selectedAttachments.value[0].file_name, '路线.png')
  await restored.send()
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), first, '刷新后的重试沿用编号和附件')
  restored.removeAttachment('attachment-one')
  offline = false
  await restored.send()
  const changed = JSON.parse(calls.at(-1).init.body)
  assert.notEqual(changed.message_id, first.message_id, '移除附件后不能复用旧编号')
  assert.deepEqual(changed.attachment_ids, [])
  assert.equal(restored.messages.value.at(-1).attachments[0].analysis.city, '杭州')
  const { default: AttachmentCards } = await server.ssrLoadModule('/src/components/AttachmentCards.vue')
  const html = await renderToString(createSSRApp({ render: () => h(AttachmentCards, { sessionId: 'private-session', items: turn.response.attachments }) }))
  for (const expected of ['路线.png', '待确认', '西&lt;湖&gt;', '原行程', '/attachments/attachment-one/content']) assert.ok(html.includes(expected), expected)
  const { default: AttachmentFileCard } = await server.ssrLoadModule('/src/components/AttachmentFileCard.vue')
  const draftCard = await renderToString(createSSRApp({ render: () => h(AttachmentFileCard, { sessionId: 'private-session', item: attachment, removable: true }) }))
  const sentCard = await renderToString(createSSRApp({ render: () => h(AttachmentCards, { sessionId: 'private-session', items: [attachment], originalsOnly: true }) }))
  for (const card of [draftCard, sentCard]) {
    for (const expected of ['attachment-file-card', '19.6 MB', '已上传', 'content?preview=true', '打开附件 路线.png']) assert.ok(card.includes(expected), expected)
    assert.equal(card.includes('发送后识别'), false)
    assert.equal(card.includes('download='), false, '预览链接不能被下载属性覆盖')
  }
  assert.ok(draftCard.includes('移除附件'))
  assert.equal(sentCard.includes('移除附件'), false)
  const beforeInvalid = calls.length
  await restored.addAttachments([new File(['exe'], 'bad.exe')])
  assert.equal(calls.length, beforeInvalid)
  assert.match(restored.error.value, /格式/)
  await restored.addAttachments(Array.from({ length: 4 }, (_, i) => new File(['txt'], `${i}.txt`)))
  assert.equal(calls.length, beforeInvalid)
  assert.match(restored.error.value, /3/)
  await restored.addAttachments([new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'big.txt')])
  assert.equal(calls.length, beforeInvalid)
  assert.match(restored.error.value, /10/)
  await restored.addAttachments([new File([new Uint8Array(30_000_001)], 'big.pdf', { type: 'application/pdf' })])
  assert.equal(calls.length, beforeInvalid)
  assert.match(restored.error.value, /30/)
  await restored.addAttachments([new File([new Uint8Array(30_000_000)], 'route.PDF', { type: 'application/pdf' })])
  assert.equal(restored.error.value, '')
  assert.equal(calls.at(-1).init.body.get('file').size, 30_000_000)
  restored.removeAttachment('attachment-one')
  let finishUpload
  uploadWait = new Promise(resolve => { finishUpload = resolve })
  const uploading = restored.addAttachments([new File(['txt'], 'late.txt')])
  await new Promise(resolve => setImmediate(resolve))
  restored.clearConversation()
  finishUpload()
  await uploading
  assert.equal(restored.sessionId.value, null)
  assert.deepEqual(restored.selectedAttachments.value, [])
  assert.equal(restored.uploading.value, false)
  uploadWait = null
  await restored.initialize('attachment-user', false)
  await restored.addAttachments([new File(['png'], '路线.png')])
  await restored.send()
  assert.equal(restored.selectedAttachments.value.length, 0, '成功后清空本轮选择')
  assert.equal(JSON.parse(calls.at(-1).init.body).attachment_ids[0], 'attachment-one')
  await restored.addAttachments([new File(['png'], '路线.png')])
  await restored.selectConversation('other-session')
  assert.equal(restored.selectedAttachments.value.length, 0, '切换会话清空附件选择')
  const legacy = structuredClone(turn)
  delete legacy.response.attachments[0].size_bytes
  historyResponse = { revision: 1, turns: [legacy] }
  await restored.selectConversation('private-session')
  const userFile = restored.messages.value.find(message => message.role === 'user').attachments[0]
  assert.equal(userFile.size_bytes, attachment.size_bytes, '旧历史从元数据补齐大小')
  assert.equal(restored.messages.value.at(-1).attachments[0].analysis.city, '杭州', '不能覆盖旧识别快照')
  console.log('私人附件协议、重试恢复、隔离和统一可打开卡片检查通过')
} finally { globalThis.fetch = originalFetch; await server.close() }
