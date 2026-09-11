/**
会话状态：服务端保存聊天内容，标签页只记住会话编号和待确认的发送。
*/
import { computed, nextTick, ref } from 'vue'
import {
  ApiError, createConversation, readConversation, sendRequirement,
  type ChatResponse, type PendingMessage, type SavedTurn,
} from './api/requirements'

interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; failed?: boolean }
const storageKey = 'travelmind.requirement-conversation.v1'
const welcome = '你好，想去哪里旅行？\n告诉我你的时间、人数和预算，也可以先说一个想法，我们一起补充。'

/** 每次调用创建独立状态；App只调用一次，不把不同会话放进全局共享变量。 */
export function useRequirementConversation() {
  const messages = ref<ChatMessage[]>([{ id: 'welcome', role: 'assistant', text: welcome }])
  const input = ref('')
  const response = ref<ChatResponse | null>(null)
  const sessionId = ref<string | null>(null)
  const revision = ref(0)
  const sending = ref(false)
  const restoring = ref(true)
  const restoreFailed = ref(false)
  const busy = computed(() => sending.value || restoring.value)
  const error = ref('')
  const storageWarning = ref('')
  const chatArea = ref<HTMLElement | null>(null)
  let pending: PendingMessage | null = null

  /** 刷新后仍可找回这次发送的编号；服务器已提交时，重试不会重复保存。 */
  function remember(): void {
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
    messages.value.push(
      { id: `${turn.message_id}-user`, role: 'user', text: turn.response.result.original_message },
      { id: `${turn.message_id}-assistant`, role: 'assistant', text: turn.response.reply },
    )
    if (turn.response.status !== 'unsupported') response.value = turn.response
    else if (response.value) response.value = { ...response.value, changed_fields: [] }
    revision.value = turn.revision
  }

  /** 读取成功后才整体替换聊天；恢复失败保留编号，禁止带着空上下文继续发送。 */
  async function reloadConversation(): Promise<void> {
    restoring.value = true
    error.value = ''
    try {
      if (sessionId.value) {
        const history = await readConversation(sessionId.value)
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
      restoreFailed.value = true
      error.value = cause instanceof Error ? `恢复失败：${cause.message}` : '恢复失败，请重新读取对话。'
    } finally {
      restoring.value = false
      void scrollToLatest()
    }
  }

  /** 页面初始化先找编号再读数据库；浏览器缓存不作为需求来源。 */
  async function initialize(): Promise<void> {
    try {
      const raw = sessionStorage.getItem(storageKey)
      if (raw) {
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
    error.value = ''
    try {
      if (!sessionId.value) sessionId.value = await createConversation()
      if (!pending || pending.message !== text) {
        pending = { message: text, message_id: crypto.randomUUID(), expected_revision: revision.value }
      }
      remember()
      const bubbleId = `${pending.message_id}-user`
      messages.value = messages.value.filter(item => item.id !== bubbleId)
      messages.value.push({ id: bubbleId, role: 'user', text })
      void scrollToLatest()
      const turn = await sendRequirement(sessionId.value, pending)
      messages.value = messages.value.filter(item => item.id !== bubbleId)
      showTurn(turn)
      pending = null
      input.value = ''
      remember()
    } catch (cause) {
      const failed = messages.value.find(item => item.id === `${pending?.message_id}-user`)
      if (failed) failed.failed = true
      if (cause instanceof ApiError && cause.status === 409) {
        // 已有其他请求提交：恢复后让用户重新确认这句话，不自动覆盖别人刚改的内容。
        pending = null
        remember()
        await reloadConversation()
        if (!restoreFailed.value) error.value = '已恢复最新对话，请检查右侧需求后重新发送。'
      } else {
        error.value = cause instanceof Error ? cause.message : '发送失败，请检查网络后重试。'
      }
    } finally {
      sending.value = false
      void scrollToLatest()
    }
  }

  /** 切换到新会话，不删除数据库旧记录；下一次发送再创建新编号。 */
  function resetConversation(): void {
    if (busy.value) return
    sessionId.value = null
    pending = null
    revision.value = 0
    response.value = null
    input.value = ''
    error.value = ''
    restoreFailed.value = false
    messages.value = [{ id: 'welcome', role: 'assistant', text: welcome }]
    remember()
  }

  return {
    messages, input, response, sessionId, revision, busy, restoring, restoreFailed,
    error, storageWarning, chatArea, initialize, reloadConversation, send, resetConversation
  }
}
