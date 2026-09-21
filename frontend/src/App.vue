<script setup lang="ts">
/** 页面组合层：登录后读取自己的会话；管理员另可管理共享资料。 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Modal } from '@arco-design/web-vue'
import { currentAccount, login, logout, register, type Account } from './api/auth'
import { modelNames, type ModelOption, getModelStatus, listSessions, renameSession, type SessionSummary } from './api/requirements'
import { advanceAuthGeneration, ApiError } from './api/http'
import { useRequirementConversation } from './useRequirementConversation'
import DocumentPanel from './components/DocumentPanel.vue'
import AttractionCards from './components/AttractionCards.vue'
import RestaurantCards from './components/RestaurantCards.vue'
import ItineraryCard from './components/ItineraryCard.vue'
import LandingPage from './components/LandingPage.vue'
import ChatIcon from './components/ChatIcon.vue'
import ThinkingProcess from './components/ThinkingProcess.vue'
import AnswerSources from './components/AnswerSources.vue'
import AttachmentCards from './components/AttachmentCards.vue'
import AttachmentFileCard from './components/AttachmentFileCard.vue'
import { attachmentAccept } from './api/attachments'
import { renderMarkdown } from './markdown'

const account = ref<Account | null>(null)
const checkingAuth = ref(false)
const landingInput = ref('')
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
const deletingId = ref<string | null>(null)
let deleteDialog: ReturnType<typeof Modal.confirm> | null = null
let authGeneration = 0
let listGeneration = 0
const { messages, input, selectedProvider, sessionId, busy, stopping, canStop, stop, restoring, restoreFailed, error, storageWarning, progress,
  chatArea, initialize, reloadConversation, send: sendConversation,
  selectConversation, clearConversation, deleteConversation, undoDraft, undoTarget, pendingUndo,
  selectedAttachments, uploading, addAttachments, removeAttachment, workflowTarget, chooseWorkflow, pendingResume, retryWorkflow } = useRequirementConversation()
const attachmentInput = ref<HTMLInputElement | null>(null)
const uploadMenu = ref<HTMLElement | null>(null)
const fileDragDepth = ref(0)
const attachmentBlocked = computed(() => !account.value || checkingAuth.value || authBusy.value || busy.value || uploading.value || restoreFailed.value || !!pendingUndo.value || !!deletingId.value || !!renamingId.value || selectedAttachments.value.length >= 3)
const modelOptions = ref<ModelOption[]>([])
const modelState = ref<'checking' | 'configured' | 'missing' | 'offline'>('checking')
const search = ref('')
const collapsed = ref(false)
const mobileOpen = ref(false)
const sessionMenu = ref<HTMLElement | null>(null)
const menuItem = ref<SessionSummary | null>(null)
const renameDialog = ref<HTMLDialogElement | null>(null)
const renameTitle = ref('')
const renameError = ref('')
const renamingId = ref<string | null>(null)
const renameTarget = ref<SessionSummary | null>(null)
const visibleMessages = computed(() => messages.value.filter(message => message.id !== 'welcome'))
const emptyChat = computed(() => !visibleMessages.value.length && !busy.value && !restoreFailed.value)
const currentTitle = computed(() => sessions.value.find(item => item.id === sessionId.value)?.title || '新对话')
const sessionGroups = computed(() => {
  const groups = new Map<string, SessionSummary[]>()
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
  for (const item of sessions.value) {
    if (!item.title.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())) continue
    const date = new Date(item.updated_at)
    const label = date >= today ? '今天' : date >= yesterday ? '昨天' : '更早'
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label)!.push(item)
  }
  return [...groups].map(([label, items]) => ({ label, items }))
})

/** 历史操作只定位菜单，不读取或发送对话。 */
async function showSessionMenu(item: SessionSummary, event: MouseEvent): Promise<void> {
  if (busy.value || renamingId.value || deletingId.value) return
  menuItem.value = item
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  await nextTick()
  const menu = sessionMenu.value
  if (!menu) return
  menu.showPopover()
  menu.style.left = `${Math.max(12, Math.min(rect.right + 6, window.innerWidth - menu.offsetWidth - 12))}px`
  menu.style.top = `${Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 12))}px`
}

/** 改名失败保留原列表和弹窗，成功才采用服务端返回值。 */
function openRename(item: SessionSummary): void {
  if (busy.value || deletingId.value || renamingId.value) return
  sessionMenu.value?.hidePopover()
  renameTarget.value = item; renameTitle.value = item.title; renameError.value = ''
  renameDialog.value?.showModal()
}
function closeRename(): void { if (!renamingId.value) { renameDialog.value?.close(); renameTarget.value = null } }
async function saveRename(): Promise<void> {
  const item = renameTarget.value
  const title = renameTitle.value.trim()
  if (!account.value || !item || renamingId.value || busy.value || deletingId.value) return
  if (!title || [...title].length > 200) { renameError.value = '名称需要为1～200个字符。'; return }
  const token = authGeneration
  renamingId.value = item.id; renameError.value = ''
  ++listGeneration; listBusy.value = false
  try {
    const saved = await renameSession(item.id, title)
    if (token !== authGeneration) return
    sessions.value = sessions.value.map(row => row.id === saved.id ? { ...row, ...saved } : row)
    renameDialog.value?.close(); renameTarget.value = null
  } catch (cause) {
    if (token === authGeneration) renameError.value = cause instanceof Error ? cause.message : '改名失败，请重试。'
  } finally { if (token === authGeneration) renamingId.value = null }
}

/** 快捷键复用新建入口；手机抽屉与桌面收起各自独立。 */
function onShortcut(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && account.value && !renameDialog.value?.open) {
    event.preventDefault(); void newConversation()
  }
  if (event.key === 'Escape') mobileOpen.value = false
}

/** 退出或401同步清除私人界面；代数防止旧异步请求回写。 */
function clearPrivate(): void {
  advanceAuthGeneration()
  ++authGeneration; ++listGeneration
  deleteDialog?.close(); deleteDialog = null; deletingId.value = null
  sessionMenu.value?.hidePopover(); renameDialog.value?.close(); renameTarget.value = null; renamingId.value = null
  menuItem.value = null; search.value = ''; mobileOpen.value = false
  account.value = null; activePage.value = 'chat'; documentsOpened.value = false; sessions.value = []; nextCursor.value = null
  listError.value = ''; listBusy.value = false
  modelState.value = 'checking'; clearConversation()
}
/** 访客校验401不打断输入；已登录失效时清除私人数据并提示重新登录。 */
function unauthorized(): void {
  if (account.value) { clearPrivate(); if (!checkingAuth.value) void openAuth(pendingQuestion.value) }
}

/** 两个首页入口共用服务端校验：Cookie有效才加载私人会话，401才弹出登录。 */
async function enterAgent(question: string | null = null): Promise<void> {
  if (checkingAuth.value || authBusy.value) return
  checkingAuth.value = true; authError.value = ''
  try {
    const identity = await currentAccount()
    await start(identity, question !== null)
    if (account.value?.id !== identity.id) { await openAuth(question); return }
    // 点击开始规划时把已有文字带入聊天框，但不替用户发送。
    input.value = question ?? (landingInput.value || input.value)
    if (question !== null) {
      await send()
    }
    landingInput.value = ''
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) await openAuth(question)
    else authError.value = cause instanceof Error ? cause.message : '无法检查登录状态，请稍后重试。'
  } finally { checkingAuth.value = false }
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
    modelOptions.value = status.providers ?? []
    modelState.value = status.configured ? 'configured' : 'missing'
  } catch { if (token === authGeneration) modelState.value = 'offline' }
}

/** 历史和分页均来自服务端，翻页失败保留已读列表。 */
async function loadSessions(more = false): Promise<void> {
  if (!account.value || deletingId.value || renamingId.value || (more && (listBusy.value || !nextCursor.value))) return
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
    if (question === null && landingInput.value) input.value = landingInput.value
    landingInput.value = ''
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
  if ((!input.value.trim() && !selectedAttachments.value.length) || busy.value || uploading.value || deletingId.value || renamingId.value || checkingAuth.value) return
  if (!account.value) { await openAuth(input.value.trim()); return }
  if (modelState.value !== 'configured') return
  await sendConversation()
  if (account.value && !error.value) void loadSessions()
}

async function newConversation(): Promise<void> {
  if (!account.value || busy.value || deletingId.value || renamingId.value) return
  clearConversation(false); activePage.value = 'chat'; search.value = ''; mobileOpen.value = false
  sessionMenu.value?.hidePopover()
}

/** 撤销成功后刷新会话排序；请求细节和失败重试由会话状态统一保存。 */
async function undo(targetMessageId: string): Promise<void> {
  if (!account.value || deletingId.value) return
  const token = authGeneration
  await undoDraft(targetMessageId)
  if (token === authGeneration && account.value && !error.value) void loadSessions()
}

/** 删除确认函数：成功后移除历史，失败保留原列表；旧分页响应不能重新放回已删会话。 */
function confirmDelete(item: SessionSummary): void {
  if (!account.value || busy.value || renamingId.value || deletingId.value || deleteDialog) return
  sessionMenu.value?.hidePopover()
  const token = authGeneration
  deleteDialog = Modal.confirm({
    title: '删除这段对话？', content: `“${item.title || '新建对话'}”的消息、需求和行程将永久删除，无法恢复。`,
    okText: '确认删除', cancelText: '取消', okButtonProps: { status: 'danger' },
    hideCancel: false, maskClosable: false,
    onBeforeOk: async () => {
      if (token !== authGeneration || !account.value || busy.value) return true
      deletingId.value = item.id; listError.value = ''
      ++listGeneration; listBusy.value = false
      try {
        const deleted = await deleteConversation(item.id)
        if (token !== authGeneration) return true
        if (deleted) sessions.value = sessions.value.filter(row => row.id !== item.id)
        return true
      } catch (cause) {
        if (token === authGeneration) listError.value = cause instanceof Error ? cause.message : '删除失败，请重试。'
        return true
      } finally { if (token === authGeneration) deletingId.value = null }
    },
    onClose: () => { deleteDialog = null },
  })
}

async function chooseSession(id: string): Promise<void> {
  if (!account.value || busy.value || renamingId.value || deletingId.value) return
  mobileOpen.value = false; sessionMenu.value?.hidePopover(); activePage.value = 'chat'
  if (id === sessionId.value) return
  await selectConversation(id)
}

async function signOut(): Promise<void> {
  if (!account.value || busy.value || renamingId.value || deletingId.value || authBusy.value) return
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

/** 文件选择函数：登录后才能选择，清空原生输入以支持再次选择同一个文件。 */
async function onAttachmentChange(event: Event): Promise<void> {
  const element = event.target as HTMLInputElement
  const files = Array.from(element.files ?? [])
  element.value = ''
  if (attachmentBlocked.value) return
  await addAttachments(files)
}

/** 菜单函数：贴近加号打开，同时保持窄屏和页面边缘可见。 */
function toggleUploadMenu(event: MouseEvent): void {
  const menu = uploadMenu.value
  if (!menu || attachmentBlocked.value) return
  if (menu.matches(':popover-open')) { menu.hidePopover(); return }
  menu.showPopover()
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  menu.style.left = `${Math.max(12, Math.min(box.left, window.innerWidth - menu.offsetWidth - 12))}px`
  menu.style.top = `${Math.max(12, box.top - menu.offsetHeight - 10)}px`
}

/** 拖拽判断函数：只接管本地文件，不影响选中文字和普通链接。 */
function isFileDrag(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

/** 拖入函数：使用计数避免经过输入框子元素时提示闪烁。 */
function onFileDragEnter(event: DragEvent): void {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (!attachmentBlocked.value) { fileDragDepth.value++; uploadMenu.value?.hidePopover() }
}

/** 拖过函数：禁用状态仍拦住浏览器打开文件，但不接收上传。 */
function onFileDragOver(event: DragEvent): void {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = attachmentBlocked.value ? 'none' : 'copy'
}

/** 放下函数：复用选择文件的上传流程，保留输入文字且不自动发送消息。 */
async function onFileDrop(event: DragEvent): Promise<void> {
  if (!isFileDrag(event)) return
  event.preventDefault()
  fileDragDepth.value = 0
  if (attachmentBlocked.value) return
  await addAttachments(Array.from(event.dataTransfer?.files ?? []))
}

/** 页面保护函数：文件落在输入框外时不导航离开当前对话。 */
function preventFileNavigation(event: DragEvent): void {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.type === 'drop') fileDragDepth.value = 0
}

onMounted(() => {
  window.addEventListener('travelmind:unauthorized', unauthorized)
  window.addEventListener('keydown', onShortcut)
  window.addEventListener('dragover', preventFileNavigation)
  window.addEventListener('drop', preventFileNavigation)
})
onBeforeUnmount(() => { window.removeEventListener('travelmind:unauthorized', unauthorized); window.removeEventListener('keydown', onShortcut); window.removeEventListener('dragover', preventFileNavigation); window.removeEventListener('drop', preventFileNavigation); deleteDialog?.close() })
</script>

<template>
  <LandingPage v-if="!account" v-model="landingInput" :busy="checkingAuth || authBusy" :error="authError" @plan="enterAgent()" @send="enterAgent" />
  <div v-else class="shell" :class="{ collapsed, 'mobile-open': mobileOpen }">
    <button v-if="mobileOpen" class="sidebar-scrim" aria-label="关闭侧栏" @click="mobileOpen = false" />
    <aside class="sidebar" aria-label="对话导航">
      <div class="sidebar-brand">
        <button class="brand" aria-label="TravelMindAI 新对话" :disabled="busy || !!deletingId || !!renamingId" @click="newConversation"><img src="/brand/logo-mark.svg" alt="" /><span>TravelMind<em>AI</em></span></button>
        <button class="icon-button collapse" aria-label="收起侧栏" @click="collapsed = true; mobileOpen = false"><ChatIcon name="panel" /></button>
      </div>
      <div class="sidebar-actions">
        <button class="new-chat" :disabled="busy || !!deletingId || !!renamingId" @click="newConversation"><ChatIcon name="plus" /><span>新建对话</span><kbd>Ctrl K</kbd></button>
        <label class="search"><ChatIcon name="search" /><input v-model="search" type="search" placeholder="搜索历史对话" aria-label="搜索已加载的历史对话" /></label>
      </div>
      <div class="history-heading">历史对话<span>{{ sessions.length }}</span></div>
      <nav class="history-list" aria-label="历史对话">
        <a-alert v-if="listError" type="error" class="history-error">{{ listError }}<a-button size="mini" @click="loadSessions()">重试加载</a-button></a-alert>
        <p v-if="listBusy && !sessions.length" class="empty-history" role="status">正在读取历史…</p>
        <p v-else-if="!sessionGroups.length" class="empty-history">{{ search ? '已加载的对话中没有匹配结果。' : '下一段旅程，从新对话开始。' }}</p>
        <template v-for="group in sessionGroups" :key="group.label">
          <h2 class="history-group">{{ group.label }}</h2>
          <div v-for="item in group.items" :key="item.id" class="history-row" :class="{ selected: activePage === 'chat' && sessionId === item.id }">
            <button class="history-select" :title="item.title" :aria-current="sessionId === item.id && activePage === 'chat' ? 'page' : undefined" :disabled="busy || !!deletingId || !!renamingId" @click="chooseSession(item.id)"><ChatIcon name="chat" /><span>{{ item.title || '新建对话' }}</span></button>
            <button class="icon-button row-more" :aria-label="`${item.title || '新建对话'}：更多操作`" aria-haspopup="menu" :disabled="busy || !!deletingId || !!renamingId" @click="showSessionMenu(item, $event)"><ChatIcon name="more" /></button>
          </div>
        </template>
        <button v-if="nextCursor" class="load-more" :disabled="listBusy || !!deletingId || !!renamingId" @click="loadSessions(true)">{{ listBusy ? '正在加载…' : '加载更早对话' }}</button>
      </nav>
      <div class="sidebar-bottom">
        <button v-if="account.role === 'admin'" class="knowledge-entry" :class="{ selected: activePage === 'documents' }" @click="documentsOpened = true; activePage = 'documents'; mobileOpen = false"><ChatIcon name="books" /><span>知识库管理</span><span class="admin-tag">管理</span></button>
        <details class="profile">
          <summary class="account" :aria-label="`${account.username}的账号菜单`"><span class="avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span><span class="account-label"><strong>{{ account.username }}</strong><small>{{ account.role === 'admin' ? '管理员账号' : '个人账号' }}</small></span><ChatIcon name="chevron" /></summary>
          <div class="profile-menu"><strong>{{ account.username }}</strong><small>{{ account.role === 'admin' ? '管理员 · 共享知识库管理' : '个人账号 · 私人旅行对话' }}</small><button :disabled="busy || authBusy || !!deletingId || !!renamingId" @click="signOut"><ChatIcon name="logout" />退出登录</button></div>
        </details>
      </div>
    </aside>
    <main class="workspace">
      <header class="topbar"><div class="topbar-left"><button class="icon-button expand" aria-label="展开侧栏" @click="collapsed = false; mobileOpen = true"><ChatIcon name="panel" /></button><span class="conversation-title">{{ activePage === 'documents' ? '知识库管理' : currentTitle }}</span></div>
        <div v-if="activePage === 'chat' && modelState !== 'configured'" class="service-state" role="status"><span>{{ modelState === 'checking' ? '正在连接…' : '服务暂不可用' }}</span><button v-if="modelState !== 'checking'" @click="checkModel">重新检查</button></div>
        <button v-if="activePage === 'documents'" class="back-chat" @click="activePage = 'chat'">返回对话 ↗</button>
      </header>
      <a-alert v-if="authError" type="error">{{ authError }}</a-alert>
      <section v-show="activePage === 'chat'" class="chat-area" :class="{ 'is-empty': emptyChat }" aria-label="旅行对话">
        <div v-if="emptyChat" class="welcome"><div class="welcome-brand"><img src="/brand/logo-mark.svg" alt="" /><span>TravelMind<em>AI</em></span></div><h1>这次，想去哪里？</h1></div>
        <div v-show="!emptyChat" ref="chatArea" class="messages chat-messages" role="log" aria-label="旅行对话" aria-live="polite"><div v-for="message in visibleMessages" :key="message.id" class="message-row" :class="message.role">
          <img v-if="message.role === 'assistant'" class="message-avatar" src="/brand/logo-mark.svg" alt="" /><div class="message-content"><span class="message-name">{{ message.role === 'assistant' ? 'TravelMind AI' : '你' }}</span>
            <ThinkingProcess v-if="message.process" :steps="message.process.steps" :seconds="message.process.seconds" />
            <AttachmentCards v-if="message.role === 'user' && sessionId && message.attachments?.length" class="user-attachments" :session-id="sessionId" :items="message.attachments" originals-only />
            <div v-if="message.role === 'assistant'" class="message-bubble markdown-answer" v-html="renderMarkdown(message.text)" /><div v-else-if="message.text" class="message-bubble">{{ message.text }}</div><AttractionCards v-if="message.role === 'assistant' && message.attractions?.length" :items="message.attractions" />
            <p v-if="message.attractions?.length && message.knowledge?.clarification && !message.text.includes(message.knowledge.clarification)" class="answer-followup">{{ message.knowledge.clarification }}</p>
            <RestaurantCards v-if="message.role === 'assistant' && message.restaurants?.length" :items="message.restaurants" :category="message.nearby?.category" :provider="message.nearby?.provider" />
            <ItineraryCard v-if="message.role === 'assistant' && message.itinerary" :snapshot="message.itinerary" :origin="message.origin" :undo-available="message.messageId === undoTarget && !pendingUndo" :busy="busy || restoreFailed || checkingAuth || !!deletingId || !!renamingId" @undo="undo(message.messageId!)" @query="input = $event" />
            <section v-if="message.role === 'assistant' && message.workflow?.status === 'waiting'" class="workflow-choice" aria-label="待补充或选择">
              <details v-if="message.workflow.preview" open><summary>待采用的候选行程</summary>
                <div v-for="day in message.workflow.preview.days" :key="day.day"><strong>第{{ day.day }}天</strong><ul><li v-for="activity in day.activities" :key="activity.place.id">{{ activity.start_time }} · {{ activity.place.map.name }} · {{ activity.duration_minutes }}分钟</li></ul></div>
                <p>演示预算估算：{{ message.workflow.preview.budget.total }}元</p>
                <p v-for="warning in message.workflow.preview.warnings" :key="warning">{{ warning }}</p>
              </details>
              <template v-if="message.messageId === workflowTarget">
                <p>在下方补充条件并发送，即可继续这次规划。</p>
                <a-button v-if="message.workflow.can_accept" :disabled="busy || restoreFailed || uploading || !!deletingId || !!renamingId" @click="chooseWorkflow('accept')">采用当前候选行程</a-button>
                <a-button :disabled="busy || restoreFailed || uploading || !!deletingId || !!renamingId" @click="chooseWorkflow('cancel')">保留现状</a-button>
              </template><p v-else>此条等待已结束，请以最新对话为准。</p>
            </section>
            <AnswerSources v-if="message.role === 'assistant' && message.knowledge" :knowledge="message.knowledge" />
            <AttachmentCards v-if="message.role === 'assistant' && sessionId && message.attachments?.length" :session-id="sessionId" :items="message.attachments" :use="message.attachmentUse" :planned="!!message.itinerary" />
            <small v-if="message.role === 'assistant' && message.usedProviders?.length" class="model-used">{{ message.usedProviders.map(name => modelNames[name]).join(' → ') }}</small><span v-if="message.failed" class="failed-note">本条未确认保存，可重试或重新读取</span></div></div>
          <div v-if="restoring" class="thinking" role="status"><span class="thinking-dot" />正在读取已保存对话…</div>
          <div v-else-if="busy" class="message-row assistant stream-message">
            <img class="message-avatar" src="/brand/logo-mark.svg" alt="" /><div class="message-content">
              <ThinkingProcess :steps="progress" running />
            </div>
          </div></div>

        <div class="composer-wrap">
          <a-alert v-if="storageWarning" type="warning" class="send-error">{{ storageWarning }}</a-alert>
          <a-alert v-if="error" type="error" class="send-error">{{ error }}</a-alert>
          <a-button v-if="restoreFailed" :disabled="busy || !!deletingId" @click="reloadConversation">重试加载</a-button>
          <a-button v-if="pendingUndo" :disabled="busy || restoreFailed || !!deletingId" @click="undo(pendingUndo.target_message_id)">重试撤销</a-button>
          <a-button v-if="pendingResume && error" :disabled="busy || restoreFailed || uploading || !!deletingId || !!renamingId" @click="retryWorkflow">重试上次接续</a-button>
          <form class="composer" :class="{ 'is-dragging': fileDragDepth > 0 && !attachmentBlocked }" @submit.prevent="send" @dragenter="onFileDragEnter" @dragover="onFileDragOver" @dragleave="fileDragDepth = Math.max(0, fileDragDepth - 1)" @drop="onFileDrop">
            <div v-if="fileDragDepth > 0 && !attachmentBlocked" class="drop-hint" role="status"><ChatIcon name="file" /><strong>松开添加到当前对话</strong><span>上传后可补充旅行要求</span></div>
            <div v-if="selectedAttachments.length || uploading" class="selected-attachments">
              <template v-if="sessionId"><AttachmentFileCard v-for="item in selectedAttachments" :key="item.id" :session-id="sessionId" :item="item" removable :disabled="busy || uploading" @remove="removeAttachment" /></template>
              <span v-if="uploading" class="upload-status" role="status">正在上传附件，请稍候…</span>
            </div>
            <textarea v-model="input" maxlength="6000" :disabled="busy || restoreFailed || checkingAuth || !!deletingId || !!renamingId" aria-label="旅行需求输入" placeholder="告诉我想去哪里，一起慢慢规划…" @keydown="onComposerKeydown" />
            <input ref="attachmentInput" type="file" hidden multiple :accept="attachmentAccept" @change="onAttachmentChange" />
            <div class="composer-controls"><button class="icon-button attach-button" type="button" aria-label="添加私人附件" title="添加文件或拖入输入框" aria-haspopup="dialog" :disabled="attachmentBlocked" @click="toggleUploadMenu"><ChatIcon name="plus" /></button><span class="composer-mode"><i class="mode-dot" />旅行规划</span><div class="send-group"><select v-model="selectedProvider" class="model-select" aria-label="选择模型" :disabled="checkingAuth || restoring || pendingResume"><option v-for="option in modelOptions" :key="option.id" :value="option.id" :disabled="!option.configured">{{ option.name }}{{ option.configured ? '' : '（未配置）' }}</option></select><span class="keyboard-hint">Enter 发送 · Shift + Enter 换行</span><button v-if="canStop || stopping" type="button" class="stop-generation" :disabled="stopping" @click="stop">{{ stopping ? '停止中…' : '停止' }}</button><button v-else class="send" type="submit" :aria-label="busy ? '正在回复' : '发送消息'" :disabled="checkingAuth || busy || uploading || restoreFailed || !!pendingUndo || !!deletingId || !!renamingId || (!input.trim() && !selectedAttachments.length) || modelState !== 'configured'"><ChatIcon :name="busy ? 'more' : 'arrow'" /></button></div></div>
          </form>
          <div ref="uploadMenu" popover="auto" class="upload-popup" role="dialog" aria-label="添加旅行资料">
            <button class="choose-attachment" type="button" :disabled="attachmentBlocked" @click="uploadMenu?.hidePopover(); attachmentInput?.click()"><ChatIcon name="file" /><span>添加旅行资料<small>攻略、行程单、路线图片</small></span></button>
            <p>PDF 最大30MB · 其他文件最大10MiB<br />PDF、DOCX、PNG/JPG/WebP、TXT、Markdown<br />每条最多3份 · 仅用于当前对话</p>
            <div class="upload-tip">也可将文件拖入输入框</div>
          </div>
          <p v-if="selectedAttachments.length" class="composer-note">可说明附件用途：供参考、这些点都要去，或替换第几天。</p>
          <p v-if="!emptyChat" class="composer-note">AI 生成内容仅供参考，请核实出行信息。</p>
        </div>
      </section>
      <section v-if="account.role === 'admin' && documentsOpened" v-show="activePage === 'documents'" class="documents-workspace"><DocumentPanel :active="activePage === 'documents'" /></section>
    </main>
    <div ref="sessionMenu" popover class="floating-menu" aria-label="对话操作"><button @click="menuItem && openRename(menuItem)"><ChatIcon name="edit" />重命名</button><div class="menu-divider" /><button class="danger" @click="menuItem && confirmDelete(menuItem)"><ChatIcon name="trash" />删除对话</button></div>
  </div>
  <dialog ref="renameDialog" class="rename-dialog" aria-labelledby="rename-heading" @cancel.prevent="closeRename"><form @submit.prevent="saveRename"><div class="dialog-heading"><h2 id="rename-heading">重命名对话</h2><button class="icon-button" type="button" aria-label="关闭重命名" :disabled="!!renamingId" @click="closeRename"><ChatIcon name="close" /></button></div><label for="conversation-name">给这段旅程起个名字</label><input id="conversation-name" v-model="renameTitle" required maxlength="200" :disabled="!!renamingId" /><p v-if="renameError" class="rename-error" role="alert">{{ renameError }}</p><div class="dialog-actions"><button class="secondary" type="button" :disabled="!!renamingId" @click="closeRename">取消</button><button class="primary" type="submit" :disabled="!!renamingId">{{ renamingId ? '正在保存…' : '保存名称' }}</button></div></form></dialog>
    <dialog ref="authDialog" class="auth-dialog" aria-labelledby="auth-heading" @cancel.prevent="closeAuth" @click="($event.target === authDialog) && closeAuth()">
      <div class="auth-card"><button class="auth-close" type="button" aria-label="关闭登录窗口" :disabled="authBusy" @click="closeAuth">×</button>
        <div class="auth-brand"><img class="auth-emblem" src="/brand/logo-mark.svg" alt="" /><span class="auth-wordmark">TravelMind <em>AI</em></span></div>
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
</template>

<style scoped src="./chat.css"></style>
