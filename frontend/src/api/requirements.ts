/** 对话HTTP契约与请求封装；只访问自己的后端，不在前端保存DeepSeek密钥。 */
import type { AnswerResult, GeoPoint, MapLookup } from './documents'
import type { AttachmentSnapshot, AttachmentUse } from './attachments'
import { ApiError, apiFetch, readResponse } from './http'
export interface TravelRequirement {
  intent: string
  destination: string | null
  origin: string | null
  start_date: string | null
  end_date: string | null
  days: number | null
  travelers: number | null
  total_budget: string | null
  pace: 'relaxed' | 'balanced' | 'intensive' | null
  interests: string[]
  dietary: string[]
  lodging_preferences: string[]
  hard_constraints: string[]
  excluded_items: string[]
  assumptions: string[]
}

/** 最近旅行话题与长对话摘要均由服务端保存；旧历史可以没有这些字段。 */
export interface ConversationState { topic_cities: string[]; topic_places: string[] }
export interface HistorySummary { text: string; covered_revision: number }
export interface WorkflowResume { run_id: string; action: 'continue' | 'accept' | 'cancel' }
export interface WorkflowSnapshot { run_id: string; status: 'waiting' | 'completed' | 'cancelled'; attempts: number; issues: string[]; can_accept: boolean; preview?: TravelPlan | null }

/** 一次对话响应：文字回复用于左边聊天，结构化需求用于右边卡片。 */
export interface ChatResponse {
  selected_provider?: ModelProvider
  used_providers?: ModelProvider[]
  result: {
    extraction: TravelRequirement
    original_message: string
    reference_date: string
    missing_required_fields: string[]
    clarification: string | null
    message_intent: string | null
  }
  reply: string
  status: 'needs_clarification' | 'complete' | 'unsupported' | 'knowledge'
  changed_fields: string[]
  request_id: string
  knowledge?: AnswerResult | null // 历史响应可没有；新主聊天保存回答与引用原文快照。
  dining?: DiningResult | null // 餐馆及评分来自地图查询，随最终回答一起保存。
  itinerary?: PlanSnapshot | null // 旧消息可没有；只展示最终提交的逐日行程。
  conversation?: ConversationState | null
  history_summary?: HistorySummary | null
  attachment_use?: AttachmentUse | null
  attachments?: AttachmentSnapshot[] // 旧消息可没有；识别结果随本轮保存，恢复时不重跑模型。
  workflow?: WorkflowSnapshot | null
}

/** 预算快照类型：金额以字符串传输，价格仅为演示估算。 */
export interface BudgetSummary {
  days: number; travelers: number; nights: number; rooms: number; lodging: string
  total_budget: string; price_version: string; unit_prices: Record<string, string>; costs: Record<string, string>
  subtotal: string; contingency_rate: string; contingency: string; total: string; remaining: string
  over_budget: boolean; assumptions: string[]
}
export interface PlanSource { id: string; kind: 'knowledge' | 'web' | 'attachment'; attachment_id?: string | null; title: string; text: string; url: string | null }
export interface PlanPlace { id: string; map: MapLookup; sources: PlanSource[] }
export interface PlannedActivity {
  place: PlanPlace; start_time: string; duration_minutes: number
  transport: 'walk' | 'transit' | 'taxi'; transfer_minutes: number
  route?: { status: 'estimated' | 'unavailable'; duration_minutes: number | null; distance_m: number | null; provider: 'baidu'; checked_at: string } | null
}
export interface TravelPlan {
  format: 'daily-plan-v1'; title: string; destination: string
  days: { day: number; date: string | null; activities: PlannedActivity[] }[]
  budget: BudgetSummary; warnings: string[]
}
export interface PlanSnapshot {
  itinerary_id: string; version: number; previous_version: number | null
  operation: 'create' | 'modify' | 'undo'; plan: TravelPlan; changes: string[]; can_undo: boolean
}
/** 撤销请求类型：网络失败保留整个请求，重试不能更换编号或版本。 */
export interface UndoDraftRequest {
  operation_id: string; target_message_id: string; expected_revision: number; expected_itinerary_version: number
}

/** 餐馆结果类型：旧历史可没有；距离是地图直线距离，人均仅作参考。 */
export interface DiningItem {
  poi_id: string; name: string; address: string | null; location: GeoPoint
  rating: number; distance_m: number | null; reference_cost: string | null
}
export interface DiningResult {
  status: 'found' | 'empty' | 'ratings_unavailable' | 'unconfigured' | 'error' | 'needs_clarification'
  items: DiningItem[]; anchor: MapLookup | null; radius_m: number
  preference: string | null; clarification: string | null
  category?: 'dining' | 'lodging'; max_cost?: number | null
  checked_at: string; provider: 'amap' | 'baidu'; rating_missing_count: number
}

/** 过程事件类型：只展示后端实际阶段和公开回答草稿，不接收推理或原始模型JSON。 */
export type RequirementUpdate =
  | { event: 'fallback'; data: { from_alias: ModelProvider; to_alias: ModelProvider; reason_category: string } }
  | { event: 'progress'; data: { stage: string; message: string } }
  | { event: 'draft'; data: { text: string } }
  | { event: 'reset'; data: Record<string, never> }

/** 流读取函数：拼接UTF-8字节和完整事件；只有done才代表本轮已经保存。 */
export async function readRequirementStream(
  body: ReadableStream<Uint8Array>,
  onUpdate: (update: RequirementUpdate) => void,
): Promise<SavedTurn> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = ''
  let event = ''
  let data: string[] = []
  let saved: SavedTurn | null = null

  /** 事件分发函数：多行data以换行连接，心跳注释不产生界面进度。 */
  function dispatch(): void {
    if (!data.length || saved) { event = ''; data = []; return }
    if (!['progress', 'draft', 'reset', 'fallback', 'done', 'error'].includes(event)) { event = ''; data = []; return }
    let payload
    try { payload = JSON.parse(data.join('\n')) }
    catch { throw new Error('服务返回了无法读取的回复，请重新读取对话后重试。') }
    if (!payload || typeof payload !== 'object') throw new Error('回复事件格式不正确，请重新读取对话后重试。')
    if (event === 'done') {
      if (typeof payload.message_id !== 'string' || !Number.isInteger(payload.revision) || !payload.response?.result || typeof payload.response.reply !== 'string') throw new Error('完整回复缺少必要信息，请重新读取对话后重试。')
      saved = payload as SavedTurn
    }
    else if (event === 'error') throw new ApiError(payload.message, payload.status)
    else if (event === 'progress' || event === 'draft' || event === 'reset' || event === 'fallback') onUpdate({ event, data: payload } as RequirementUpdate)
    event = ''; data = []
  }

  try {
    while (!saved) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      // 先按完整行处理；块末尾的CR等待下一块，避免把CRLF误认作两次换行。
      let newline: RegExpExecArray | null
      while ((newline = /\r\n|\r|\n/.exec(buffer))) {
        if (!chunk.done && newline[0] === '\r' && newline.index === buffer.length - 1) break
        const line = buffer.slice(0, newline.index)
        buffer = buffer.slice(newline.index + newline[0].length)
        if (!line) dispatch()
        else if (!line.startsWith(':')) {
          const colon = line.indexOf(':')
          const field = colon < 0 ? line : line.slice(0, colon)
          const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
          if (field === 'event') event = value
          else if (field === 'data') data.push(value)
        }
        if (saved) break
      }
      if (chunk.done) break
    }
    if (!saved) throw new Error('回复连接已中断，尚未确认保存。请重新读取对话，或使用原消息重试。')
    return saved
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

/** 查询配置状态只读取后端配置标志，不向DeepSeek发送收费请求。 */
export type ModelProvider = 'deepseek' | 'kimi' | 'qwen'
export const modelNames: Record<ModelProvider, string> = { deepseek: 'DeepSeek', kimi: 'Kimi', qwen: 'Qwen' }
export interface ModelOption { id: ModelProvider; name: string; configured: boolean }

/** 停止只发出请求，必须等运行状态确认后才能把旧发送改成新尝试。 */
export async function stopRequirement(sessionId: string, messageId: string): Promise<void> {
  await readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages/${encodeURIComponent(messageId)}/stop`, { method: 'POST' }))
}
export async function requirementRunning(sessionId: string, messageId: string): Promise<boolean> {
  const result = await readResponse<{ running: boolean }>(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages/${encodeURIComponent(messageId)}/execution`, { cache: 'no-store' }))
  return result.running
}
export async function getModelStatus(): Promise<{ configured: boolean; model: string; providers: ModelOption[] }> {
  return readResponse(await apiFetch('/api/v1/requirements/status'))
}

/** 一轮持久化对话；编号用于安全重试，revision用于防止覆盖更新过的历史。 */
export interface SavedTurn { message_id: string; revision: number; response: ChatResponse }
export interface ConversationHistory { session_id: string; revision: number; turns: SavedTurn[] }
export interface PendingMessage { message: string; message_id: string; expected_revision: number; attachment_ids?: string[]; workflow_resume?: WorkflowResume; selected_provider?: ModelProvider }
export interface SessionSummary { id: string; thread_id: string; title: string; status: string; created_at: string; updated_at: string }
export interface SessionPage { items: SessionSummary[]; next_cursor: string | null }
/** 历史列表来自当前账号的服务端分页，浏览器不负责用户隔离。 */
export async function listSessions(cursor: string | null = null): Promise<SessionPage> {
  const params = new URLSearchParams({ limit: '30' })
  if (cursor) params.set('cursor', cursor)
  return readResponse(await apiFetch(`/api/v1/sessions?${params}`, { cache: 'no-store' }))
}

/** 会话改名函数：使用服务端返回的标题，不在浏览器单独保存名称。 */
export async function renameSession(sessionId: string, title: string): Promise<SessionSummary> {
  const result = await readResponse<{ session: SessionSummary }>(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
  }))
  return result.session
}

/** 会话删除函数：由后端校验当前账号归属，204表示整段历史已删除。 */
export async function deleteSession(sessionId: string): Promise<void> {
  await readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }))
}

/** 沿用M1的会话创建接口；第一次发送时才创建，空白页面不会自动产生记录。 */
export async function createConversation(): Promise<string> {
  const result = await readResponse<{ session: { id: string } }>(await apiFetch('/api/v1/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '新建对话' }),
  }))
  return result.session.id
}

/** 恢复只读取自己的后端数据库，不调用模型；不缓存历史，避免展示旧版本。 */
export async function readConversation(sessionId: string): Promise<ConversationHistory> {
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages`, {
    cache: 'no-store',
  }))
}

/** 撤销函数：提交原样的操作编号和双版本，由服务器原子恢复上一版。 */
export async function undoItinerary(sessionId: string, request: UndoDraftRequest): Promise<SavedTurn> {
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/drafts/undo`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    signal: AbortSignal.timeout(30_000),
  }))
}

/** 仅提交本条消息，旧需求和参考日期都由后端从数据库取得。 */
export async function sendRequirement(
  sessionId: string,
  pending: PendingMessage,
  onUpdate: (update: RequirementUpdate) => void,
): Promise<SavedTurn> {
  const controller = new AbortController()
  const startedAt = Date.now()
  let attachmentRequest = !!pending.attachment_ids?.length
  // PDF可分批识别；仅附件请求给15分钟，普通聊天仍保留3分钟上限。
  let timeout = window.setTimeout(() => controller.abort(), attachmentRequest ? 900_000 : 180_000)
  try {
    const response = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(pending),
      signal: controller.signal,
    })
    if (!response.ok) return await readResponse(response)
    if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('服务没有返回可读取的回复流，请重新读取对话后重试。')
    return await readRequirementStream(response.body, update => {
      // 复用上轮附件由服务端确认；只扩一次总期限，不随进度无限续期。
      if (!attachmentRequest && update.event === 'progress' && update.data.stage === 'attachment') {
        attachmentRequest = true
        window.clearTimeout(timeout)
        timeout = window.setTimeout(() => controller.abort(), Math.max(0, 900_000 - (Date.now() - startedAt)))
      }
      onUpdate(update)
    })
  } catch (error) {
    if (controller.signal.aborted) throw new Error('等待回复超时，请重新读取确认是否已保存，或使用原消息重试。')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
