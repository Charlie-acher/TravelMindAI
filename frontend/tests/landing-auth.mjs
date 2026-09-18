/** 首页入口回归：运行 node tests/landing-auth.mjs；模拟HTTP，不使用真实账号或模型。 */
import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createRenderer, nextTick, ssrContextKey } from 'vue'

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true } })
const originalFetch = globalThis.fetch
const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout })
// 渲染空根节点，只运行真实App的状态与生命周期；页面外观另由浏览器检查。
const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {} })
let app
try {
  const { default: App } = await server.ssrLoadModule('/src/App.vue')
  let state
  const calls = []
  const identity = { id: 'landing-test-user', username: '旅行测试', role: 'user' }
  let authenticated = false
  let offline = false
  let checkWait = null
  let rejectLogin = false
  let rejectRename = false
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init })
    if (offline) throw new TypeError('网络暂不可用')
    if (url === '/api/v1/auth/me') {
      if (checkWait) await checkWait
      return authenticated ? Response.json(identity) : Response.json({ error: { message: '请先登录' } }, { status: 401 })
    }
    if (url === '/api/v1/auth/login') {
      if (rejectLogin) return Response.json({ error: { message: '用户名或密码错误' } }, { status: 401 })
      authenticated = true; return Response.json(identity)
    }
    if (url === '/api/v1/auth/logout') { authenticated = false; return new Response(null, { status: 204 }) }
    if (url === '/api/v1/requirements/status') return Response.json({ configured: true, model: 'test-model' })
    if (url === '/api/v1/sessions/rename-test' && init.method === 'PATCH') {
      if (rejectRename) return Response.json({ error: { message: '改名保存失败' } }, { status: 503 })
      return Response.json({ session: { id: 'rename-test', title: JSON.parse(init.body).title } })
    }
    if (url === '/api/v1/sessions' && init.method === 'POST') return Response.json({ session: { id: 'new-landing-session' } })
    if (url === '/api/v1/sessions?limit=30') return Response.json({ items: [], next_cursor: null })
    if (url === '/api/v1/sessions/new-landing-session/requirement-messages') return Response.json({ session_id: 'new-landing-session', revision: 0, turns: [] })
    if (url === '/api/v1/sessions/new-landing-session/attachments' && init.method === 'POST') {
      const file = init.body.get('file')
      return Response.json({ id: 'drop-file', file_name: file.name, size_bytes: file.size, analysis: null, error_message: null })
    }
    if (url === '/api/v1/sessions/new-landing-session/requirement-messages/stream') {
      const request = JSON.parse(init.body)
      return new Response(`event: done\ndata: ${JSON.stringify({ message_id: request.message_id, revision: 1, response: { reply: '收到旅行想法', status: 'knowledge', result: { original_message: request.message } } })}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
    }
    throw new Error(`未预期请求：${url}`)
  }
  app = renderer.createApp({ setup() { state = App.setup({}, { expose() {} }); return () => null } })
  app.provide(ssrContextKey, {})
  app.mount({})
  await nextTick()
  assert.equal(calls.length, 0, '首页加载不检查身份，也不读取私人会话')
  state.authDialog.value = { open: false, showModal() { this.open = true }, close() { this.open = false } }

  state.landingInput.value = '想去杭州玩三天'
  await state.enterAgent(state.landingInput.value)
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/auth/me'])
  assert.equal(state.authDialog.value.open, true)
  assert.equal(state.pendingQuestion.value, '想去杭州玩三天')
  state.closeAuth()
  assert.equal(state.landingInput.value, '想去杭州玩三天', '取消登录不丢输入')
  assert.equal(state.pendingQuestion.value, null)

  await state.enterAgent(state.landingInput.value)
  state.username.value = 'tester'; state.password.value = 'testpass'
  rejectLogin = true
  await state.submitAuth()
  assert.equal(state.pendingQuestion.value, '想去杭州玩三天', '登录失败仍保留原问题')
  assert.equal(state.account.value, null)
  assert.equal(calls.filter(call => call.url.endsWith('/stream')).length, 0)
  rejectLogin = false
  await state.submitAuth()
  assert.equal(state.account.value.id, identity.id)
  assert.equal(state.authDialog.value.open, false)
  assert.equal(calls.filter(call => call.url.endsWith('/stream')).length, 1, '登录后原问题只发送一次')
  assert.equal(state.messages.value.at(-1).text, '收到旅行想法')
  assert.equal(state.landingInput.value, '')

  await state.signOut()
  authenticated = true
  calls.length = 0
  state.landingInput.value = '稍后完善这段行程'
  await state.enterAgent()
  assert.equal(calls[0].url, '/api/v1/auth/me')
  assert.equal(state.account.value.id, identity.id, '有效Cookie直接进入智能体')
  assert.equal(state.restoreFailed.value, false)
  assert.equal(calls.some(call => call.url.endsWith('/stream')), false, '开始规划不发送空问题')
  assert.equal(state.input.value, '稍后完善这段行程', '开始规划携带已有输入但不自动发送')

  await state.signOut()
  authenticated = true
  calls.length = 0
  let releaseCheck
  checkWait = new Promise(resolve => { releaseCheck = resolve })
  const entry = state.enterAgent('已登录后直接发送')
  await state.enterAgent('重复点击')
  assert.equal(calls.length, 1, '校验中重复点击不会重复请求')
  releaseCheck(); checkWait = null
  await entry
  assert.equal(calls.some(call => call.url.endsWith('/requirement-messages')), false, '首页发送开启新会话，不混入以前的上下文')
  assert.equal(calls.filter(call => call.url.endsWith('/stream')).length, 1)
  assert.equal(state.messages.value.at(-2).text, '已登录后直接发送')

  calls.length = 0
  await state.newConversation()
  assert.equal(calls.length, 0, '新建进入空白状态，首次发送才创建服务端记录')
  assert.equal(state.sessionId.value, null)
  assert.equal(state.emptyChat.value, true)
  state.renameDialog.value = { open: false, showModal() { this.open = true }, close() { this.open = false } }
  const row = { id: 'rename-test', title: '旧标题', updated_at: new Date().toISOString() }
  state.sessions.value = [row]
  state.openRename(row)
  state.renameTitle.value = '  新标题  '
  await state.saveRename()
  assert.equal(state.sessions.value[0].title, '新标题', '使用后端返回标题')
  state.openRename(state.sessions.value[0])
  state.renameTitle.value = '不能保存的标题'; rejectRename = true
  await state.saveRename()
  assert.equal(state.sessions.value[0].title, '新标题', '保存失败不改变历史')
  assert.match(state.renameError.value, /失败/)
  assert.equal(state.renameDialog.value.open, true, '失败保留弹窗以便重试')
  state.closeRename()
  calls.length = 0
  state.input.value = '请参考这份攻略'
  let prevented = 0
  const drop = { dataTransfer: { types: ['Files'], files: [new File(['长沙攻略'], '长沙.markdown')], dropEffect: 'none' }, preventDefault() { prevented++ } }
  state.onFileDragEnter(drop)
  state.onFileDragEnter(drop)
  assert.equal(state.fileDragDepth.value, 2, '进入子元素不会关闭拖入提示')
  state.onFileDragOver(drop)
  assert.equal(drop.dataTransfer.dropEffect, 'copy')
  await state.onFileDrop(drop)
  assert.ok(prevented >= 3)
  assert.equal(state.fileDragDepth.value, 0)
  assert.equal(state.selectedAttachments.value[0].file_name, '长沙.markdown')
  assert.equal(state.input.value, '请参考这份攻略', '拖入不丢输入草稿')
  assert.equal(calls.filter(call => call.url.endsWith('/attachments')).length, 1)
  assert.equal(calls.some(call => call.url.endsWith('/stream')), false, '拖入只上传，不调用识别')
  const beforeIgnored = calls.length
  await state.onFileDrop({ dataTransfer: { types: ['text/plain'], files: [] }, preventDefault() { throw new Error('不应接管普通文字拖入') } })
  state.uploading.value = true
  await state.onFileDrop(drop)
  state.uploading.value = false
  assert.equal(calls.length, beforeIgnored, '普通文字和上传中重复拖入不触发请求')
  state.removeAttachment('drop-file')
  await state.signOut()
  await state.onFileDrop(drop)
  assert.equal(state.selectedAttachments.value.length, 0, '退出后不接收文件')
  offline = true
  state.landingInput.value = '周末去哪里'
  await state.enterAgent(state.landingInput.value)
  assert.equal(state.account.value, null)
  assert.equal(state.authDialog.value.open, false, '断网不能误判成未登录')
  assert.match(state.authError.value, /网络/)
  assert.equal(state.landingInput.value, '周末去哪里')
  assert.equal(state.checkingAuth.value, false)
  console.log('PASS: landing auth, cancellation, failed login retry, resume once, valid cookie, duplicate clicks, empty chat, rename success/failure, offline')
} finally {
  app?.unmount()
  globalThis.fetch = originalFetch
  await server.close()
}
