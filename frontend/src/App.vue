<script setup lang="ts">
/** 页面组合层：登录后读取自己的会话；管理员另可管理共享资料。 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Card as ACard, Textarea as ATextarea } from '@arco-design/web-vue'
import { currentAccount, login, logout, register, type Account } from './api/auth'
import { createConversation, getModelStatus, listSessions, type SessionSummary } from './api/requirements'
import { advanceAuthGeneration, ApiError } from './api/http'
import { useRequirementConversation } from './useRequirementConversation'
import DocumentPanel from './components/DocumentPanel.vue'
import AttractionCards from './components/AttractionCards.vue'
import RestaurantCards from './components/RestaurantCards.vue'

const account = ref<Account | null>(null)
const checkingAuth = ref(true)
const authBusy = ref(false)
const authError = ref('')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const registering = ref(false)
const authDialog = ref<HTMLDialogElement | null>(null)
const pendingQuestion = ref<string | null>(null)
const activePage = ref<'chat' | 'documents'>('chat')
const documentsOpened = ref(false)
const sessions = ref<SessionSummary[]>([])
const nextCursor = ref<string | null>(null)
const listBusy = ref(false)
const listError = ref('')
const newBusy = ref(false)
let authGeneration = 0
let listGeneration = 0
const { messages, input, sessionId, busy, restoring, restoreFailed, error, storageWarning, draft, progress,
  chatArea, initialize, reloadConversation, send: sendConversation, openEmpty,
  selectConversation, clearConversation } = useRequirementConversation()
const modelState = ref<'checking' | 'configured' | 'missing' | 'offline'>('checking')
const modelName = ref('DeepSeek')
const examples = ['想去杭州', '三天，两个人，总预算五千元', '杭州有哪些适合散步的景点？', '改成三个人']

/** 退出或401同步清除私人界面；代数防止旧异步请求回写。 */
function clearPrivate(): void {
  advanceAuthGeneration()
  ++authGeneration; ++listGeneration
  account.value = null; activePage.value = 'chat'; documentsOpened.value = false; sessions.value = []; nextCursor.value = null
  listError.value = ''; listBusy.value = false; newBusy.value = false
  modelState.value = 'checking'; modelName.value = 'DeepSeek'; clearConversation()
}
/** 访客首次探测401不打断输入；已登录失效时清除私人数据并提示重新登录。 */
function unauthorized(): void {
  if (account.value) { clearPrivate(); void openAuth() }
  checkingAuth.value = false
}

/** 发送前登录：原问题只保存在当前页面，取消弹窗不会丢掉输入框内容。 */
async function openAuth(question: string | null = null): Promise<void> {
  pendingQuestion.value = question
  authError.value = ''; registering.value = false
  await nextTick()
  if (!authDialog.value?.open) authDialog.value?.showModal()
}

function closeAuth(): void {
  if (authBusy.value) return
  authDialog.value?.close()
  pendingQuestion.value = null
  password.value = ''; confirmPassword.value = ''; authError.value = ''
}

async function checkModel(): Promise<void> {
  const token = authGeneration
  modelState.value = 'checking'
  try {
    const status = await getModelStatus()
    if (token !== authGeneration) return
    modelName.value = status.model
    modelState.value = status.configured ? 'configured' : 'missing'
  } catch { if (token === authGeneration) modelState.value = 'offline' }
}

/** 历史和分页均来自服务端，翻页失败保留已读列表。 */
async function loadSessions(more = false): Promise<void> {
  if (!account.value || (more && (listBusy.value || !nextCursor.value))) return
  const token = ++listGeneration
  listBusy.value = true; listError.value = ''
  try {
    const page = await listSessions(more ? nextCursor.value : null)
    if (token !== listGeneration || !account.value) return
    sessions.value = more ? [...sessions.value, ...page.items] : page.items
    nextCursor.value = page.next_cursor
  } catch (cause) {
    if (token === listGeneration) listError.value = cause instanceof Error ? cause.message : '历史读取失败，请重试。'
  } finally { if (token === listGeneration) listBusy.value = false }
}

/** 登录后只用缓存编号选中当前会话，内容仍回读服务端。 */
async function start(value: Account, fresh = false): Promise<void> {
  advanceAuthGeneration()
  ++authGeneration
  account.value = value; checkingAuth.value = false; authError.value = ''
  password.value = ''; confirmPassword.value = ''
  await Promise.all([loadSessions(), initialize(value.id, !fresh), checkModel()])
}

async function submitAuth(): Promise<void> {
  if (authBusy.value) return
  authError.value = ''
  if (password.value.length < 6 || password.value.length > 12) { authError.value = '密码需为6～12个字符。'; return }
  if (registering.value) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$/.test(username.value.trim())) { authError.value = '用户名为3～64位字母、数字、下划线、点或短横线，首位需为字母或数字。'; return }
    if (password.value !== confirmPassword.value) { authError.value = '两次输入的密码不一致。'; return }
  }
  authBusy.value = true
  try {
    const question = pendingQuestion.value
    const identity = registering.value ? await register(username.value.trim(), password.value) : await login(username.value.trim(), password.value)
    await start(identity, question !== null)
    if (account.value?.id !== identity.id) return
    authDialog.value?.close(); pendingQuestion.value = null
    if (question !== null) {
      input.value = question
      // 创建新会话后再回答访客的问题，不把它接进该账号以前的对话。
      await send()
    }
  }
  catch (cause) { authError.value = cause instanceof Error ? cause.message : '账号操作失败，请重试。' }
  finally { authBusy.value = false }
}

/** 成功发送后刷新服务端生成的标题和排序。 */
async function send(): Promise<void> {
  if (!input.value.trim() || busy.value || checkingAuth.value) return
  if (!account.value) { await openAuth(input.value.trim()); return }
  if (modelState.value !== 'configured') return
  await sendConversation()
  if (account.value && !error.value) void loadSessions()
}

async function newConversation(): Promise<void> {
  if (!account.value || busy.value || newBusy.value) return
  newBusy.value = true; listError.value = ''
  const token = authGeneration
  try {
    const id = await createConversation()
    if (token !== authGeneration) return
    openEmpty(id); activePage.value = 'chat'; await loadSessions()
  } catch (cause) { if (token === authGeneration) listError.value = cause instanceof Error ? cause.message : '新建对话失败，请重试。' }
  finally { if (token === authGeneration) newBusy.value = false }
}

async function chooseSession(id: string): Promise<void> {
  if (!account.value || busy.value || newBusy.value || id === sessionId.value) return
  activePage.value = 'chat'; await selectConversation(id)
}

async function signOut(): Promise<void> {
  if (!account.value || busy.value || newBusy.value || authBusy.value) return
  authBusy.value = true; authError.value = ''
  try { await logout(); clearPrivate() }
  catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) clearPrivate()
    else authError.value = cause instanceof Error ? cause.message : '退出失败，请重试。'
  } finally { authBusy.value = false }
}

function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); void send() }
}

onMounted(async () => {
  window.addEventListener('travelmind:unauthorized', unauthorized)
  try { await start(await currentAccount()) }
  catch (cause) { if (!(cause instanceof ApiError && cause.status === 401)) authError.value = cause instanceof Error ? cause.message : '无法检查登录状态。'; checkingAuth.value = false }
})
onBeforeUnmount(() => window.removeEventListener('travelmind:unauthorized', unauthorized))
</script>

<template>
  <div class="app-shell" :class="{ 'guest-shell': !account }">
    <header class="page-header"><div class="brand"><span class="brand-mark">T</span><div><strong>TravelMindAI</strong><span>从一个想法，开始一段旅行</span></div></div>
      <nav v-if="account" aria-label="功能导航"><a-button :type="activePage === 'chat' ? 'primary' : 'text'" @click="activePage = 'chat'">旅行对话</a-button>
        <a-button v-if="account.role === 'admin'" :type="activePage === 'documents' ? 'primary' : 'text'" @click="documentsOpened = true; activePage = 'documents'">旅行资料</a-button>
        <details class="account-menu"><summary :aria-label="`${account.username}的账号菜单`"><span class="account-avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span><span class="account-name">{{ account.username }}</span><span aria-hidden="true">⌄</span></summary>
          <div class="account-dropdown"><strong>{{ account.username }}</strong><small>{{ account.role === 'admin' ? '管理员' : '旅行探索者' }}</small><button :disabled="busy || newBusy || authBusy" @click="signOut">退出登录</button></div>
        </details></nav><button v-else class="header-login" :disabled="checkingAuth" @click="openAuth()">登录 / 注册</button></header>
    <dialog ref="authDialog" class="auth-dialog" aria-labelledby="auth-heading" @cancel.prevent="closeAuth" @click="($event.target === authDialog) && closeAuth()">
      <div class="auth-card"><button class="auth-close" type="button" aria-label="关闭登录窗口" :disabled="authBusy" @click="closeAuth">×</button>
        <div class="auth-emblem" aria-hidden="true">T</div><span class="auth-eyebrow">TRAVELMIND AI</span>
        <h2 id="auth-heading">{{ registering ? '开启你的旅行故事' : '欢迎回来，旅行家' }}</h2>
        <p class="auth-subtitle">{{ pendingQuestion ? '还未登录，登录后继续回答刚才的问题' : '把每一个旅行灵感，留在自己的对话里' }}</p>
        <div class="auth-tabs" aria-label="账号操作"><button type="button" :class="{ active: !registering }" :aria-pressed="!registering" :disabled="authBusy" @click="registering = false; authError = ''">登录</button><button type="button" :class="{ active: registering }" :aria-pressed="registering" :disabled="authBusy" @click="registering = true; authError = ''">注册</button></div>
        <form class="login-form" @submit.prevent="submitAuth">
          <label>用户名<input v-model="username" autocomplete="username" placeholder="请输入用户名" maxlength="64" :disabled="authBusy" required autofocus /></label>
          <label>密码<input v-model="password" type="password" :autocomplete="registering ? 'new-password' : 'current-password'" placeholder="6～12位密码" minlength="6" maxlength="12" :disabled="authBusy" required /></label>
          <label v-if="registering">确认密码<input v-model="confirmPassword" type="password" autocomplete="new-password" placeholder="再次输入密码" minlength="6" maxlength="12" :disabled="authBusy" required /></label>
          <p v-if="registering" class="auth-hint">用户名为3～64位字母、数字、点、下划线或连字符；密码为6～12位。</p>
          <a-alert v-if="authError" type="error">{{ authError }}</a-alert>
          <button class="auth-submit" type="submit" :disabled="authBusy">{{ authBusy ? '正在进入…' : registering ? '注册并继续' : '登录并继续' }}<span aria-hidden="true">↗</span></button>
        </form><p class="auth-footnote">你的灵感与历史对话，只属于你。</p>
      </div>
    </dialog>
    <a-alert v-if="authError && !authDialog?.open" type="error" class="send-error">{{ authError }}</a-alert>
      <section v-if="!account" class="guest-intro"><span class="guest-eyebrow">让灵感成为下一站</span><h1>一句话，<span>开启下一段旅程</span></h1><p>想去的地方、期待的风景，随时和我聊聊。</p></section>
      <main v-show="activePage === 'chat'" class="workspace"><aside v-if="account" class="history-panel" aria-label="历史对话">
        <a-button type="primary" long :loading="newBusy" :disabled="busy || newBusy || listBusy" @click="newConversation">新建对话</a-button>
        <a-alert v-if="listError" type="error" class="history-error">{{ listError }}<a-button size="mini" @click="loadSessions()">重试加载</a-button></a-alert>
        <p v-if="listBusy && !sessions.length" role="status">正在读取历史…</p><p v-else-if="!sessions.length && !listError">还没有历史对话。</p>
        <ul><li v-for="item in sessions" :key="item.id"><button type="button" :class="{ selected: sessionId === item.id }" :disabled="busy || newBusy" @click="chooseSession(item.id)">
          <strong>{{ item.title || '新建对话' }}</strong><small>{{ new Date(item.updated_at).toLocaleString('zh-CN') }}</small></button></li></ul>
        <a-button v-if="nextCursor" :loading="listBusy" :disabled="busy || newBusy" @click="loadSessions(true)">加载更早对话</a-button>
      </aside><a-card class="chat-panel" :bordered="false"><template #title><div class="chat-title">聊聊你的旅行<span>说说你的想法，随时调整</span></div></template>
        <template #extra><a-button v-if="restoreFailed" :disabled="busy" @click="reloadConversation">重试加载</a-button></template>
        <div v-if="account" class="model-strip"><span class="status-dot" :class="modelState" /><span v-if="modelState === 'configured'">{{ modelName }} · 已配置</span>
          <span v-else-if="modelState === 'checking'">正在检查服务配置…</span><span v-else-if="modelState === 'missing'">后端尚未配置模型密钥</span><span v-else>暂时无法连接后端服务</span>
          <a-button v-if="modelState === 'offline' || modelState === 'missing'" type="text" size="mini" @click="checkModel">重新检查</a-button></div>
        <div v-show="account" ref="chatArea" class="chat-messages" role="log" aria-label="旅行对话" aria-live="polite"><div v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
          <span class="message-avatar">{{ message.role === 'assistant' ? 'T' : '我' }}</span><div class="message-content"><span class="message-name">{{ message.role === 'assistant' ? '旅行助手' : '你' }}</span>
            <AttractionCards v-if="message.role === 'assistant' && message.attractions?.length" :items="message.attractions" /><div v-else class="message-bubble">{{ message.text }}</div>
            <RestaurantCards v-if="message.role === 'assistant' && message.restaurants?.length" :items="message.restaurants" />
            <div v-if="message.attractions?.length && message.clarification" class="message-bubble">{{ message.clarification }}</div><span v-if="message.failed" class="failed-note">本条未确认保存，可重试或重新读取</span></div></div>
          <div v-if="restoring" class="thinking" role="status"><span class="thinking-dot" />正在读取已保存对话…</div>
          <div v-else-if="busy" class="message-row assistant stream-message">
            <span class="message-avatar">T</span><div class="message-content">
              <details class="stream-progress"><summary><span class="thinking-dot" /><span role="status">{{ progress.at(-1)?.message || '正在连接旅行助手…' }}</span><span class="progress-toggle">查看过程</span></summary>
                <ol v-if="progress.length"><li v-for="(step, index) in progress" :key="index" :class="{ current: index === progress.length - 1 }">{{ step.message }}</li></ol>
                <p v-else>连接成功后，将在这里显示实际处理进度。</p>
              </details>
              <template v-if="draft"><span class="draft-note">正在生成，完成后核对</span><div class="message-bubble draft-bubble">{{ draft }}</div></template>
            </div>
          </div></div>
        <div class="composer"><a-alert v-if="storageWarning" type="warning" class="send-error">{{ storageWarning }}</a-alert><a-alert v-if="error" type="error" class="send-error">{{ error }}</a-alert>
          <a-alert v-if="account && modelState === 'missing'" type="warning" class="send-error">请配置模型密钥后重新检查。</a-alert>
          <div class="example-prompts"><span>试着说</span><a-button v-for="example in examples" :key="example" size="mini" :disabled="busy" @click="input = example">{{ example }}</a-button></div>
          <a-textarea v-model="input" :auto-size="{ minRows: 3, maxRows: 6 }" :max-length="6000" :disabled="busy || restoreFailed || checkingAuth" :textarea-attrs="{ 'aria-label': '旅行需求输入' }"
            placeholder="例如：从上海去杭州玩三天，两个人，预算五千元…" @keydown="onComposerKeydown" />
          <div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><a-button type="primary" :loading="busy" :disabled="checkingAuth || busy || restoreFailed || !input.trim() || (!!account && modelState !== 'configured')" @click="send">发送 <span aria-hidden="true">↑</span></a-button></div>
        </div></a-card></main>
      <p v-if="!account" class="guest-note">从一个想法开始，慢慢找到你的旅行方式。</p>
      <main v-if="account?.role === 'admin' && documentsOpened" v-show="activePage === 'documents'"><DocumentPanel :active="activePage === 'documents'" /></main>
  </div>
</template>
