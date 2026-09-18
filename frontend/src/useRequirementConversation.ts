/**
会话状态：服务端保存聊天内容，标签页只记住会话编号和待确认的发送。
*/
import { computed, nextTick, ref } from 'vue'
import {
  createConversation, deleteSession, readConversation, sendRequirement, undoItinerary,
  type ChatResponse, type DiningResult, type DiningItem, type PendingMessage, type SavedTurn, type PlanSnapshot, type UndoDraftRequest,
} from './api/requirements'
import { ApiError } from './api/http'
import type { AnswerResult, AttractionCard } from './api/documents'
import { listAttachments, uploadAttachment, type AttachmentSnapshot } from './api/attachments'

/** 消息类型：气泡只展示自然语言，知识依据由后端保存和校验。 */
interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; failed?: boolean; attachments?: AttachmentSnapshot[]; knowledge?: AnswerResult | null; attractions?: AttractionCard[]; restaurants?: DiningItem[]; nearby?: DiningResult | null; clarification?: string | null; itinerary?: PlanSnapshot | null; messageId?: string; process?: { steps: { stage: string; message: string }[]; draft: string; seconds: number } }
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
  const selectedAttachments = ref<AttachmentSnapshot[]>([])
  const uploading = ref(false)
  const draft = ref('')
  const progress = ref<{ stage: string; message: string }[]>([])
  const restoring = ref(false)
  const restoreFailed = ref(false)
  const busy = computed(() => sending.value || restoring.value)
  const error = ref('')
  const storageWarning = ref('')
  const chatArea = ref<HTMLElement | null>(null)
  let pending: PendingMessage | null = null
  const pendingUndo = ref<UndoDraftRequest | null>(null)
  // 只有最后一轮允许撤销；后面出现新消息时，旧卡片立即失效。
  const undoTarget = computed(() => {
    const last = messages.value.at(-1)
    return last?.itinerary?.operation === 'modify' && last.itinerary.can_undo ? last.messageId ?? null : null
  })
  let generation = 0
  let storageKey: string | null = null

  /** 清理过程函数：草稿和进度只活在当前页面，不写入历史或浏览器缓存。 */
  function clearStream(): void { draft.value = ''; progress.value = [] }

  /** 选择函数：先校验整批文件，再依次上传；会话切换后忽略旧请求结果。 */
  async function addAttachments(files: File[]): Promise<void> {
    if (!files.length || busy.value || uploading.value || restoreFailed.value || !storageKey) return
    error.value = ''
    if (files.length + selectedAttachments.value.length > 3) { error.value = '每条消息最多选择3份附件。'; return }
    if (files.some(file => !/\.(png|jpe?g|webp|pdf|docx|txt|md|markdown)$/i.test(file.name))) { error.value = '附件格式仅支持 PNG、JPEG、WebP、PDF、DOCX、TXT 和 Markdown。'; return }
    if (files.some(file => !file.size || file.size > (/\.pdf$/i.test(file.name) ? 30_000_000 : 10 * 1024 * 1024))) {
      error.value = '附件不能为空；PDF最大30MB，其他附件最大10MiB。'; return
    }
    const token = generation
    uploading.value = true
    try {
      if (!sessionId.value) {
        const id = await createConversation()
        if (token !== generation) return
        sessionId.value = id
        remember()
      }
      for (const file of files) {
        const uploaded = await uploadAttachment(sessionId.value, file)
        if (token !== generation) return
        if (!selectedAttachments.value.some(item => item.id === uploaded.id)) {
          selectedAttachments.value.push(uploaded)
          pending = null // 附件变化属于新请求，不能复用已发送过的编号。
          remember()
        }
      }
    } catch (cause) {
      if (token === generation) error.value = cause instanceof Error ? cause.message : '附件上传失败，请重试。'
    } finally { if (token === generation) uploading.value = false }
  }

  /** 移除函数：只取消本条选择，保留服务器上的历史原件。 */
  function removeAttachment(id: string): void {
    if (busy.value || uploading.value) return
    selectedAttachments.value = selectedAttachments.value.filter(item => item.id !== id)
    pending = null
    remember()
  }

  /** 刷新后仍可找回这次发送的编号；服务器已提交时，重试不会重复保存。 */
  function remember(): void {
    if (!storageKey) return
    try {
      if (sessionId.value) sessionStorage.setItem(storageKey, JSON.stringify({ sessionId: sessionId.value, pending, pendingUndo: pendingUndo.value }))
      else sessionStorage.removeItem(storageKey)
    } catch {
      storageWarning.value = '浏览器不允许保存会话编号，本页面仍可使用，但刷新后可能无法自动找回。'
    }
  }

  /** 渲染后定位最终行程顶部；流式进度和普通消息继续跟随底部。 */
  async function scrollToLatest(): Promise<void> {
    await nextTick()
    const area = chatArea.value
    if (!area) return
    const last = messages.value.at(-1)
    const card = !busy.value && last?.role === 'assistant' && last.itinerary
      ? area.querySelector<HTMLElement>('.message-row:last-child .itinerary-card') : null
    const top = card ? area.scrollTop + card.getBoundingClientRect().top - area.getBoundingClientRect().top - 12 : area.scrollHeight
    area.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
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
      { id: `${turn.message_id}-user`, role: 'user', text: turn.response.result.original_message, attachments: turn.response.attachments ?? [] },
      { id: `${turn.message_id}-assistant`, messageId: turn.message_id, role: 'assistant', text: reply, attachments: turn.response.attachments ?? [], knowledge: turn.response.knowledge, attractions: turn.response.knowledge?.attractions ?? [], restaurants: turn.response.dining?.items ?? [], nearby: turn.response.dining, clarification: turn.response.knowledge?.clarification, itinerary: turn.response.itinerary },
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
        // 只给旧快照补文件大小，不覆盖当时的识别结果或失败原因。
        const needsMetadata = history.turns.some(turn => turn.response.attachments?.some(item => item.size_bytes == null))
        const attachmentMetadata = needsMetadata || pending?.attachment_ids?.length
          ? await listAttachments(id).catch(() => null) : null
        if (token !== generation) return
        if (attachmentMetadata) for (const turn of history.turns) {
          for (const item of turn.response.attachments ?? []) {
            if (item.size_bytes == null) item.size_bytes = attachmentMetadata.find(row => row.id === item.id)?.size_bytes
          }
        }
        messages.value = [{ id: 'welcome', role: 'assistant', text: welcome }]
        response.value = null
        revision.value = history.revision
        history.turns.forEach(showTurn)
        // 历史不是刚发生的修改，恢复后不继续高亮某一轮的旧字段。
        if (response.value) response.value = { ...(response.value as ChatResponse), changed_fields: [] }
        if (pending && history.turns.some(turn => turn.message_id === pending?.message_id)) {
          pending = null
          selectedAttachments.value = []
          input.value = ''
          remember()
        } else if (pending) {
          input.value = pending.message
          // 即使元数据读取失败也保留编号，避免刷新后悄悄变成无附件发送。
          selectedAttachments.value = (pending.attachment_ids ?? []).map(id => ({ id, file_name: `附件 ${id}`, analysis: null, error_message: null }))
          if (selectedAttachments.value.length) {
            if (attachmentMetadata) selectedAttachments.value = selectedAttachments.value.map(item => attachmentMetadata.find(row => row.id === item.id) ?? item)
          }
          error.value = '上次发送尚未确认保存。可以点击发送重试，系统会检查是否已经保存。'
        }
        if (pendingUndo.value && history.turns.some(turn => turn.message_id === pendingUndo.value?.operation_id)) {
          pendingUndo.value = null
          remember()
        } else if (pendingUndo.value) {
          error.value = '上次撤销尚未确认保存，请重试撤销以确认结果。'
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
        if (saved.pendingUndo && typeof saved.pendingUndo.operation_id === 'string'
          && typeof saved.pendingUndo.target_message_id === 'string'
          && Number.isInteger(saved.pendingUndo.expected_revision)
          && Number.isInteger(saved.pendingUndo.expected_itinerary_version)) pendingUndo.value = saved.pendingUndo
      }
    } catch {
      storageWarning.value = '无法读取浏览器中的会话编号，本次从新对话开始。'
    }
    await reloadConversation()
  }

  /** 成功保存才更新需求卡片；超时或断网保留原话和同一个消息编号。 */
  async function send(): Promise<void> {
    const text = input.value.trim() || (selectedAttachments.value.length ? '请帮我读取这些附件' : '')
    if (!text || busy.value || uploading.value || restoreFailed.value) return
    const attachments = [...selectedAttachments.value]
    const attachmentIds = attachments.map(item => item.id)
    if (pendingUndo.value) { error.value = '上次撤销尚未确认保存，请先重试撤销。'; return }
    sending.value = true
    // 原话由本次请求和pending保存，输入框立即留给下一条消息。
    input.value = ''
    const startedAt = Date.now()
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
      if (!pending || pending.message !== text || JSON.stringify(pending.attachment_ids ?? []) !== JSON.stringify(attachmentIds)) {
        pending = { message: text, message_id: crypto.randomUUID(), expected_revision: revision.value, attachment_ids: attachmentIds }
      }
      remember()
      const bubbleId = `${pending.message_id}-user`
      messages.value = messages.value.filter(item => item.id !== bubbleId)
      messages.value.push({ id: bubbleId, role: 'user', text, attachments })
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
      const assistant = messages.value.at(-1)
      if (assistant) assistant.process = { steps: [...progress.value], draft: draft.value, seconds: Math.max(1, Math.round((Date.now() - startedAt) / 1000)) }
      pending = null
      selectedAttachments.value = []
      remember()
    } catch (cause) {
      if (token !== generation) return
      // 失败时恢复原话方便重试，但不能覆盖用户新写的草稿。
      if (!input.value) input.value = text
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

  /** 撤销函数：失败保留同一请求，成功同步消息、需求和版本；切换会话后拒绝回写。 */
  async function undoDraft(targetMessageId: string): Promise<void> {
    if (busy.value || restoreFailed.value || !sessionId.value) return
    if (pendingUndo.value ? pendingUndo.value.target_message_id !== targetMessageId : undoTarget.value !== targetMessageId) return
    const last = messages.value.at(-1)
    if (!pendingUndo.value) {
      if (!last?.itinerary) return
      pendingUndo.value = { operation_id: crypto.randomUUID(), target_message_id: targetMessageId,
        expected_revision: revision.value, expected_itinerary_version: last.itinerary.version }
    }
    const token = generation
    sending.value = true
    clearStream()
    progress.value = [{ stage: 'undo', message: '正在恢复上一版行程…' }]
    error.value = ''
    remember()
    try {
      const turn = await undoItinerary(sessionId.value, pendingUndo.value)
      if (token !== generation) return
      showTurn(turn)
      pendingUndo.value = null
      remember()
    } catch (cause) {
      if (token !== generation) return
      if (cause instanceof ApiError && cause.status === 409) {
        pendingUndo.value = null
        remember()
        const recovery = reloadConversation()
        const recoveryToken = generation
        sending.value = false
        await recovery
        if (generation === recoveryToken && !restoreFailed.value) error.value = '已恢复最新对话，请核对当前行程后再操作。'
      } else error.value = cause instanceof Error ? `撤销尚未确认保存：${cause.message}。请重试撤销。` : '撤销尚未确认保存，请重试撤销。'
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
    selectedAttachments.value = []
    uploading.value = false
    pendingUndo.value = null
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

  /** 删除函数：服务端成功后清空当前会话；账号或会话已切换时忽略旧响应。 */
  async function deleteConversation(id: string): Promise<boolean> {
    const token = generation
    await deleteSession(id)
    if (token !== generation) return false
    if (sessionId.value === id) clearConversation(false)
    return true
  }

  /** 清空函数：销毁可见数据并阻止旧请求回写；删除当前会话时保留账号存储键。 */
  function clearConversation(clearAccount = true): void {
    ++generation
    clearStream()
    sessionId.value = null
    pending = null
    selectedAttachments.value = []
    uploading.value = false
    pendingUndo.value = null
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
    if (clearAccount) storageKey = null
  }

  return {
    messages, input, response, sessionId, revision, busy, restoring, restoreFailed, draft, progress,
    error, storageWarning, chatArea, initialize, reloadConversation, send, undoDraft, undoTarget, pendingUndo,
    openEmpty, selectConversation, clearConversation, deleteConversation,
    selectedAttachments, uploading, addAttachments, removeAttachment,
  }
}
