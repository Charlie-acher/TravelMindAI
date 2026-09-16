/**
会话状态：服务端保存聊天内容，标签页只记住会话编号和待确认的发送。
*/
import { computed, nextTick, ref } from 'vue'
import {
  createConversation, readConversation, sendRequirement,
  type ChatResponse, type DiningItem, type PendingMessage, type SavedTurn,
} from './api/requirements'
import { ApiError } from './api/http'
import type { AttractionCard } from './api/documents'

/** 消息类型：气泡只展示自然语言，知识依据由后端保存和校验。 */
interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; failed?: boolean; attractions?: AttractionCard[]; restaurants?: DiningItem[]; clarification?: string | null }
const legacyStorageKey = 'travelmind.requirement-conversation.v1'
const welcome = '你好，想去哪里旅行？\n你可以告诉我时间、人数和预算，也可以先问问感兴趣的地方。无法确认的信息，我会直接说明。'

/** 每次调用创建独立状态；App只调用一次，不把不同会话放进全局共享变量。 */
export function useRequirementConversation() {
  const messages = ref<ChatMessage[]>([{ id: 'welcome', role: 'assistant', text: welcome }])
  const input = ref('')
  const response = ref<ChatResponse | null>(null)
  const sessionId = ref<string | null>(null)
  const revision = ref(0)
  const sending = ref(false)
  const draft = ref('')
  const progress = ref<{ stage: string; message: string }[]>([])
  const restoring = ref(false)
  const restoreFailed = ref(false)
  const busy = computed(() => sending.value || restoring.value)
  const error = ref('')
  const storageWarning = ref('')
  const chatArea = ref<HTMLElement | null>(null)
  let pending: PendingMessage | null = null
  let generation = 0
  let storageKey: string | null = null

  /** 清理过程函数：草稿和进度只活在当前页面，不写入历史或浏览器缓存。 */
  function clearStream(): void { draft.value = ''; progress.value = [] }

  /** 刷新后仍可找回这次发送的编号；服务器已提交时，重试不会重复保存。 */
  function remember(): void {
    if (!storageKey) return
    try {
      if (sessionId.value) sessionStorage.setItem(storageKey, JSON.stringify({ sessionId: sessionId.value, pending }))
      else sessionStorage.removeItem(storageKey)
    } catch {
      storageWarning.value = '浏览器不允许保存会话编号，本页面仍可使用，但刷新后可能无法自动找回。'
    }
  }

  /** Vue渲染完成后滚动，确保新消息已参与页面高度计算。 */
  async function scrollToLatest(): Promise<void> {
    await nextTick()
    chatArea.value?.scrollTo({ top: chatArea.value.scrollHeight, behavior: 'smooth' })
  }

  /** 将已提交的整轮数据画到页面；不支持的意图只显示文字，不覆盖有效需求。 */
  function showTurn(turn: SavedTurn): void {
    // 旧历史可能已经拼入[1]等引用，只在显示时去掉已知编号，不改数据库快照。
    const references = new Set([
      ...(turn.response.knowledge?.sources.map(source => source.id) ?? []),
      ...(turn.response.knowledge?.web_search?.items.map(source => source.id) ?? []),
    ])
    const reply = turn.response.reply.replace(/ ?\[(\d+)\]/g, (match, id: string) => references.has(Number(id)) ? '' : match)
    messages.value.push(
      { id: `${turn.message_id}-user`, role: 'user', text: turn.response.result.original_message },
      { id: `${turn.message_id}-assistant`, role: 'assistant', text: reply, attractions: turn.response.knowledge?.attractions ?? [], restaurants: turn.response.dining?.items ?? [], clarification: turn.response.knowledge?.clarification },
    )
    if (turn.response.status === 'needs_clarification' || turn.response.status === 'complete') response.value = turn.response
    else if (response.value) response.value = { ...response.value, changed_fields: [] }
    revision.value = turn.revision
  }

  /** 读取成功后才整体替换聊天；恢复失败保留编号，禁止带着空上下文继续发送。 */
  async function reloadConversation(): Promise<void> {
    const token = ++generation
    clearStream()
    const id = sessionId.value
    restoring.value = true
    error.value = ''
    try {
      if (id) {
        const history = await readConversation(id)
        if (token !== generation) return
        messages.value = [{ id: 'welcome', role: 'assistant', text: welcome }]
        response.value = null
        revision.value = history.revision
        history.turns.forEach(showTurn)
        // 历史不是刚发生的修改，恢复后不继续高亮某一轮的旧字段。
        if (response.value) response.value = { ...(response.value as ChatResponse), changed_fields: [] }
        if (pending && history.turns.some(turn => turn.message_id === pending?.message_id)) {
          pending = null
          input.value = ''
          remember()
        } else if (pending) {
          input.value = pending.message
          error.value = '上次发送尚未确认保存。可以点击发送重试，系统会检查是否已经保存。'
        }
      }
      restoreFailed.value = false
    } catch (cause) {
      if (token !== generation) return
      restoreFailed.value = true
      error.value = cause instanceof Error ? `恢复失败：${cause.message}` : '恢复失败，请重新读取对话。'
    } finally {
      if (token === generation) { restoring.value = false; void scrollToLatest() }
    }
  }

  /** 页面初始化先找编号再读数据库；浏览器缓存不作为需求来源。 */
  async function initialize(userId: string, restore = true): Promise<void> {
    storageKey = `travelmind.requirement-conversation.v2.${userId}`
    try {
      sessionStorage.removeItem(legacyStorageKey)
      const raw = sessionStorage.getItem(storageKey)
      if (raw && restore) {
        const saved = JSON.parse(raw)
        if (typeof saved.sessionId !== 'string') throw new Error('会话编号无效')
        sessionId.value = saved.sessionId
        if (saved.pending && typeof saved.pending.message === 'string'
          && typeof saved.pending.message_id === 'string'
          && Number.isInteger(saved.pending.expected_revision)) pending = saved.pending
      }
    } catch {
      storageWarning.value = '无法读取浏览器中的会话编号，本次从新对话开始。'
    }
    await reloadConversation()
  }

  /** 成功保存才更新需求卡片；超时或断网保留原话和同一个消息编号。 */
  async function send(): Promise<void> {
    const text = input.value.trim()
    if (!text || busy.value || restoreFailed.value) return
    sending.value = true
    clearStream()
    const token = generation
    error.value = ''
    try {
      if (!sessionId.value) {
        const createdId = await createConversation()
        if (token !== generation) return
        sessionId.value = createdId
      }
      if (token !== generation) return
      if (!pending || pending.message !== text) {
        pending = { message: text, message_id: crypto.randomUUID(), expected_revision: revision.value }
      }
      remember()
      const bubbleId = `${pending.message_id}-user`
      messages.value = messages.value.filter(item => item.id !== bubbleId)
      messages.value.push({ id: bubbleId, role: 'user', text })
      void scrollToLatest()
      const turn = await sendRequirement(sessionId.value, pending, update => {
        // 退出或切换账号后，旧连接的每一块数据都不能再画回页面。
        if (token !== generation) return
        if (update.event === 'draft') draft.value = update.data.text
        else if (update.event === 'reset') draft.value = ''
        else progress.value.push(update.data)
        void scrollToLatest()
      })
      if (token !== generation) return
      messages.value = messages.value.filter(item => item.id !== bubbleId)
      showTurn(turn)
      pending = null
      input.value = ''
      remember()
    } catch (cause) {
      if (token !== generation) return
      const failed = messages.value.find(item => item.id === `${pending?.message_id}-user`)
      if (failed) failed.failed = true
      if (cause instanceof ApiError && cause.status === 409) {
        // 已有其他请求提交：恢复后让用户重新确认这句话，不自动覆盖别人刚改的内容。
        pending = null
        remember()
        const recovery = reloadConversation()
        const recoveryToken = generation
        sending.value = false // 恢复函数已同步进入restoring，409完成后不会留下发送锁。
        await recovery
        if (generation === recoveryToken && !restoreFailed.value) error.value = '已恢复最新对话，请核对消息后重新发送。'
      } else {
        error.value = cause instanceof Error ? cause.message : '发送失败，请检查网络后重试。'
      }
    } finally {
      if (token === generation) { clearStream(); sending.value = false; void scrollToLatest() }
    }
  }

  /** 新建会话由后端先保存空记录；切换时丢弃旧对话的未发送输入。 */
  function openEmpty(id: string): void {
    ++generation
    clearStream()
    sessionId.value = id
    pending = null
    revision.value = 0
    response.value = null
    input.value = ''
    error.value = ''
    restoreFailed.value = false
    restoring.value = false
    sending.value = false
    messages.value = [{ id: 'welcome', role: 'assistant', text: welcome }]
    remember()
  }

  /** 点击历史只读取轮次，不发模型请求。 */
  async function selectConversation(id: string): Promise<void> {
    if (busy.value) return
    openEmpty(id)
    await reloadConversation()
  }

  /** 退出或401时同步销毁可见数据，并阻止在途请求回写。 */
  function clearConversation(): void {
    ++generation
    clearStream()
    sessionId.value = null
    pending = null
    revision.value = 0
    response.value = null
    input.value = ''
    error.value = ''
    restoreFailed.value = false
    restoring.value = false
    sending.value = false
    messages.value = [{ id: 'welcome', role: 'assistant', text: welcome }]
    storageWarning.value = ''
    try {
      if (storageKey) sessionStorage.removeItem(storageKey)
      sessionStorage.removeItem(legacyStorageKey)
    } catch { /* 页面状态仍已清理。 */ }
    storageKey = null
  }

  return {
    messages, input, response, sessionId, revision, busy, restoring, restoreFailed, draft, progress,
    error, storageWarning, chatArea, initialize, reloadConversation, send,
    openEmpty, selectConversation, clearConversation
  }
}
